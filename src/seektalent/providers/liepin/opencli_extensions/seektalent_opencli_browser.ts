import { spawn } from "node:child_process";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PYTHON = process.env.SEEKTALENT_PYTHON || "python";
const HELPER_MODULE = "seektalent.providers.liepin.opencli_browser_cli";
const configuredTimeoutSeconds = Number(process.env.SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS || "0");
const configuredTimeoutMs =
  configuredTimeoutSeconds > 0 ? Math.max(25000, configuredTimeoutSeconds * 1000 + 5000) : 25000;
const TIMEOUT_MS = Number(process.env.SEEKTALENT_LIEPIN_OPENCLI_TOOL_TIMEOUT_MS || String(configuredTimeoutMs));
const MAX_OUTPUT_CHARS = Number(process.env.SEEKTALENT_LIEPIN_OPENCLI_MAX_OUTPUT_CHARS || "120000");
const maxActions = Number(process.env.SEEKTALENT_LIEPIN_OPENCLI_MAX_ACTIONS_PER_TASK || "80");
const MUTATING_ACTIONS = new Set(["fill", "click", "scroll", "apply_liepin_filters", "open_liepin_detail"]);
type ToolParams = Record<string, unknown>;

let actionCount = 0;
let terminalReason: string | null = null;
let stateReady = false;
let allowedClickRefs = new Set<string>();

function textResult(payload: string) {
  return { content: [{ type: "text" as const, text: payload }], details: {} };
}

function safeJson(payload: Record<string, unknown>) {
  return JSON.stringify(payload);
}

function capabilitiesPayload() {
  return safeJson({
    ok: true,
    action: "capabilities",
    safeReasonCode: "configured",
    counts: {},
    capabilities: {
      backend: "opencli",
      tools: [
        "seektalent_opencli_status",
        "seektalent_opencli_search_liepin_cards",
        "seektalent_opencli_capabilities",
        "seektalent_opencli_open_liepin_tab",
        "seektalent_opencli_state",
        "seektalent_opencli_get_url",
        "seektalent_opencli_find",
        "seektalent_opencli_fill",
        "seektalent_opencli_click",
        "seektalent_opencli_apply_liepin_filters",
        "seektalent_opencli_extract_structured_liepin_cards",
        "seektalent_opencli_extract_visible_liepin_cards",
        "seektalent_opencli_open_liepin_detail",
        "seektalent_opencli_capture_liepin_detail_resume",
        "seektalent_opencli_finalize_liepin_resumes",
        "seektalent_opencli_scroll",
        "seektalent_opencli_wait_time",
      ],
      forbidden: ["eval", "network", "upload", "download", "storage", "cookies"],
      sourcePolicies: ["liepin"],
    },
  });
}

function updateStateFromPayload(action: string, text: string) {
  try {
    const parsed = JSON.parse(text) as {
      ok?: boolean;
      safeReasonCode?: string;
      observation?: { terminal?: boolean; allowedClickRefs?: string[] };
    };
    if (action === "open_liepin_tab") {
      stateReady = false;
      terminalReason = null;
      allowedClickRefs = new Set();
    }
    if (action === "state") {
      stateReady = parsed.ok === true && parsed.observation?.terminal !== true;
      terminalReason =
        parsed.observation?.terminal === true && typeof parsed.safeReasonCode === "string"
          ? parsed.safeReasonCode
          : null;
      allowedClickRefs =
        parsed.ok === true && Array.isArray(parsed.observation?.allowedClickRefs)
          ? new Set(parsed.observation.allowedClickRefs.filter((ref) => typeof ref === "string"))
          : new Set();
    }
    if (action === "extract_visible_liepin_cards" || action === "extract_structured_liepin_cards") {
      stateReady = parsed.ok === true && parsed.observation?.terminal !== true;
      if (stateReady) {
        terminalReason = null;
      } else {
        terminalReason =
          parsed.observation?.terminal === true && typeof parsed.safeReasonCode === "string"
            ? parsed.safeReasonCode
            : null;
      }
    }
  } catch {
    stateReady = false;
    allowedClickRefs = new Set();
  }
}

function helperEnv(action: string) {
  const env = { ...process.env };
  if (action === "click") {
    env.SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON = JSON.stringify([...allowedClickRefs]);
  }
  return env;
}

function runAction(action: string, payload: Record<string, unknown>): Promise<string> {
  if (action === "capabilities") {
    return Promise.resolve(capabilitiesPayload());
  }
  if (process.env.SEEKTALENT_LIEPIN_OPENCLI_TASK === "liepin.search_resumes" && action === "search_cards") {
    return Promise.resolve(
      safeJson({
        ok: false,
        action,
        safeReasonCode: "liepin_opencli_forbidden_command",
        safeMessage: "card search is disabled for resume tasks",
        counts: {},
      }),
    );
  }
  if (action === "open_liepin_tab" || action === "search_cards" || action === "search_resumes") {
    actionCount = 0;
    terminalReason = null;
    stateReady = false;
    allowedClickRefs = new Set();
  }
  if (
    ![
      "status",
      "capabilities",
      "state",
      "get_url",
      "search_cards",
      "search_resumes",
      "extract_visible_liepin_cards",
      "extract_structured_liepin_cards",
      "capture_liepin_detail_resume",
      "finalize_liepin_resumes",
    ].includes(action) &&
    terminalReason
  ) {
    return Promise.resolve(safeJson({ ok: false, action, safeReasonCode: terminalReason, counts: {} }));
  }
  if (MUTATING_ACTIONS.has(action) && !stateReady) {
    return Promise.resolve(
      safeJson({
        ok: false,
        action,
        safeReasonCode: "liepin_opencli_malformed_state",
        safeMessage: "requires a fresh non-terminal state",
        counts: {},
      }),
    );
  }
    if (
      action !== "status" &&
      action !== "capabilities" &&
      action !== "search_cards" &&
      action !== "search_resumes" &&
      action !== "extract_visible_liepin_cards" &&
      action !== "extract_structured_liepin_cards" &&
      action !== "finalize_liepin_resumes"
    ) {
      actionCount += 1;
    if (actionCount > maxActions) {
      return Promise.resolve(
        safeJson({ ok: false, action, safeReasonCode: "liepin_opencli_budget_exhausted", counts: {} }),
      );
    }
  }
  if (MUTATING_ACTIONS.has(action)) {
    stateReady = false;
    if (action !== "click") {
      allowedClickRefs = new Set();
    }
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (text: string) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      updateStateFromPayload(action, text);
      resolve(text);
    };
    const child = spawn(PYTHON, ["-m", HELPER_MODULE, action], {
      stdio: ["pipe", "pipe", "pipe"],
      env: helperEnv(action),
    });
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(safeJson({ ok: false, action, safeReasonCode: "liepin_opencli_timeout", counts: {} }));
    }, TIMEOUT_MS);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
      if (stdout.length > MAX_OUTPUT_CHARS) {
        child.kill("SIGKILL");
        finish(safeJson({ ok: false, action, safeReasonCode: "liepin_opencli_helper_output_too_large", counts: {} }));
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr = (stderr + String(chunk)).slice(0, 4096);
    });
    child.on("error", () => {
      finish(safeJson({ ok: false, action, safeReasonCode: "liepin_opencli_command_missing", counts: {} }));
    });
    child.on("close", (code) => {
      if (stdout.trim()) {
        finish(stdout.trim());
        return;
      }
      const reason =
        stderr.includes("Extension") && (stderr.includes("not connected") || stderr.includes("disconnected"))
          ? "liepin_opencli_extension_disconnected"
          : code === 0
            ? "liepin_opencli_helper_empty_output"
            : "liepin_opencli_status_unavailable";
      finish(safeJson({ ok: false, action, safeReasonCode: reason, counts: {} }));
    });
    child.stdin.end(JSON.stringify(payload));
  });
}

export default function registerSeekTalentOpenCliBrowser(pi: ExtensionAPI) {
  pi.registerTool({
    name: "seektalent_opencli_status",
    label: "SeekTalent browser status",
    description: "Check whether the local browser action channel is connected without changing the page.",
    parameters: Type.Object({}),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("status", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_capabilities",
    label: "SeekTalent browser capabilities",
    description: "Return the safe browser capability manifest for Liepin card search.",
    parameters: Type.Object({}),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("capabilities", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_search_liepin_cards",
    label: "Search Liepin cards",
    description:
      "Run the bounded SeekTalent Liepin card-search flow for liepin.search_cards only. Never use this tool for liepin.search_resumes.",
    parameters: Type.Object({
      sourceRunId: Type.String(),
      query: Type.String(),
      maxPages: Type.Optional(Type.Number()),
      maxCards: Type.Optional(Type.Number()),
      nativeFilters: Type.Optional(Type.Object({}, { additionalProperties: true })),
    }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("search_cards", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_open_liepin_tab",
    label: "Open Liepin search tab",
    description: "Select an already owned policy-approved Liepin search tab; fail closed if a new tab would be needed.",
    parameters: Type.Object({ url: Type.String() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("open_liepin_tab", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_state",
    label: "Observe Liepin page",
    description: "Read the current browser page state and classify terminal login, identity, or verification states.",
    parameters: Type.Object({}),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("state", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_get_url",
    label: "Read current URL",
    description: "Read the current browser URL through the restricted helper.",
    parameters: Type.Object({}),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("get_url", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_find",
    label: "Find visible target",
    description: "Find a visible text or selector target in the current page state.",
    parameters: Type.Object({ query: Type.String() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("find", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_fill",
    label: "Fill search text",
    description: "Fill a short generated Liepin search keyword into a visible target.",
    parameters: Type.Object({ target: Type.String(), text: Type.String() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("fill", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_click",
    label: "Click target",
    description: "Click a visible target after a fresh non-terminal state observation.",
    parameters: Type.Object({ target: Type.String() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("click", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_apply_liepin_filters",
    label: "Apply Liepin filters",
    description: "Apply bounded Runtime-provided Liepin native filters after search results are visible.",
    parameters: Type.Object({
      sourceRunId: Type.String(),
      nativeFilters: Type.Object({}, { additionalProperties: true }),
    }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("apply_liepin_filters", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_extract_structured_liepin_cards",
    label: "Read structured Liepin cards",
    description:
      "Read structured Liepin result card evidence from the current search results page without clicking or opening details.",
    parameters: Type.Object({
      sourceRunId: Type.String(),
      maxCards: Type.Optional(Type.Number()),
    }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("extract_structured_liepin_cards", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_extract_visible_liepin_cards",
    label: "Read visible Liepin cards",
    description: "Read structured visible Liepin result cards from the current search results page without clicking or opening details.",
    parameters: Type.Object({
      sourceRunId: Type.String(),
      maxCards: Type.Optional(Type.Number()),
    }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("extract_visible_liepin_cards", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_open_liepin_detail",
    label: "Open Liepin detail",
    description: "Open a visible Liepin detail ref selected by the agent from the latest state.",
    parameters: Type.Object({ sourceRunId: Type.String(), ref: Type.String(), rank: Type.Number() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("open_liepin_detail", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_capture_liepin_detail_resume",
    label: "Capture Liepin detail resume",
    description: "Capture the current detail page into a safe complete-resume artifact.",
    parameters: Type.Object({ sourceRunId: Type.String(), rank: Type.Number() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("capture_liepin_detail_resume", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_finalize_liepin_resumes",
    label: "Finalize Liepin resumes",
    description: "Finalize captured Liepin detail resumes into the strict Runtime JSON envelope.",
    parameters: Type.Object({
      sourceRunId: Type.String(),
      query: Type.String(),
      maxPages: Type.Optional(Type.Number()),
      maxCards: Type.Optional(Type.Number()),
      cardsSeen: Type.Optional(Type.Number()),
      targetResumes: Type.Optional(Type.Number()),
    }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("finalize_liepin_resumes", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_scroll",
    label: "Scroll page",
    description: "Scroll the current page after a fresh non-terminal state observation.",
    parameters: Type.Object({ direction: Type.Union([Type.Literal("up"), Type.Literal("down")]) }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("scroll", params));
    },
  });

  pi.registerTool({
    name: "seektalent_opencli_wait_time",
    label: "Wait briefly",
    description: "Wait briefly for the current page to render.",
    parameters: Type.Object({ seconds: Type.Number() }),
    async execute(_toolCallId: string, params: ToolParams) {
      return textResult(await runAction("wait_time", params));
    },
  });
}
