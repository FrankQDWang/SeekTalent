import {
  EXTRACTOR_VERSION,
  extractWorkerCards,
  type WorkerCandidateCard,
} from "./extraction";
import { captureResponsesDuringAction } from "./networkCapture";

type ResponseLike = {
  url(): string;
  status(): number;
  json(): Promise<unknown>;
  request?: () => {
    method?: () => string;
  };
};

type PageLike = {
  goto(url: string, options?: { waitUntil?: "domcontentloaded" | "load" | "networkidle" }): Promise<unknown>;
  on(event: "response", handler: (response: ResponseLike) => void): void;
  off(event: "response", handler: (response: ResponseLike) => void): void;
  content?: () => Promise<string>;
};

export type CardSearchRequestBody = {
  tenantId: string;
  workspaceId: string;
  providerAccountHash: string;
  connectionId: string;
  keyword: string;
  pageSize: number;
  cursor?: string;
  round: number;
  traceId: string;
  providerFilters?: Record<string, unknown>;
};

export type PythonWorkerCandidateCard = {
  payload: Record<string, unknown>;
  normalized_text: string;
  provider_subject_id: string | null;
  provider_listing_id: string | null;
  synthetic_candidate_fingerprint: string;
  identity_confidence: "provider_subject_id" | "synthetic_fingerprint";
  extraction_source: "network" | "dom_fallback";
  extractor_version: string;
  pii_classification: "direct_contact_possible" | "no_direct_contact";
  retention_policy: "provider_snapshot_7d";
  access_scope: "local_run_only";
  redaction_state: "raw_provider_payload";
};

export type CardSearchResponse = {
  cards: PythonWorkerCandidateCard[];
  diagnostics: string[];
  exhausted: boolean;
  nextCursor?: string;
  rawCandidateCount: number;
  requestPayload: Record<string, unknown>;
};

export async function searchCards(options: {
  page: PageLike;
  request: CardSearchRequestBody;
  postActionCaptureMs?: number;
}): Promise<CardSearchResponse> {
  const captureOptions =
    options.postActionCaptureMs === undefined
      ? {}
      : { postActionCaptureMs: options.postActionCaptureMs };
  const captured = await captureResponsesDuringAction(options.page, async () => {
    await options.page.goto(searchUrlForRequest(options.request), { waitUntil: "domcontentloaded" });
  }, captureOptions);
  const fallbackHtml = options.page.content ? await options.page.content() : "";
  const extraction = extractWorkerCards({ networkArtifacts: captured, fallbackHtml });
  const rawCandidateCount = extraction.cards.length;
  const cards = extraction.cards.slice(0, options.request.pageSize).map(toPythonWorkerCard);
  const response: CardSearchResponse = {
    cards,
    diagnostics: diagnosticsFor(extraction.extractionSource, rawCandidateCount),
    exhausted: rawCandidateCount < options.request.pageSize,
    rawCandidateCount,
    requestPayload: safeRequestPayload(options.request),
  };
  const nextCursor = nextCursorFor(options.request, rawCandidateCount);
  if (nextCursor !== null) {
    response.nextCursor = nextCursor;
  }
  return response;
}

function searchUrlForRequest(request: CardSearchRequestBody): string {
  const url = new URL("https://www.liepin.com/zhaopin/");
  url.searchParams.set("key", request.keyword);
  url.searchParams.set("pageSize", String(request.pageSize));
  if (request.cursor) {
    url.searchParams.set("page", request.cursor);
  }
  for (const [key, value] of Object.entries(request.providerFilters ?? {})) {
    appendFilter(url, key, value);
  }
  return url.toString();
}

function appendFilter(url: URL, key: string, value: unknown): void {
  if (!key || key.startsWith("liepin_")) {
    return;
  }
  if (typeof value === "string" || typeof value === "number") {
    url.searchParams.set(key, String(value));
    return;
  }
  if (Array.isArray(value)) {
    const textValues = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    if (textValues.length > 0) {
      url.searchParams.set(key, textValues.join(","));
    }
  }
}

function nextCursorFor(request: CardSearchRequestBody, rawCandidateCount: number): string | null {
  if (rawCandidateCount < request.pageSize) {
    return null;
  }
  const currentPage = Number(request.cursor ?? "1");
  return Number.isInteger(currentPage) && currentPage > 0 ? String(currentPage + 1) : null;
}

function diagnosticsFor(extractionSource: "network" | "dom_fallback", rawCandidateCount: number): string[] {
  return [`card_search:${extractionSource}`, `raw_candidate_count:${rawCandidateCount}`];
}

function safeRequestPayload(request: CardSearchRequestBody): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    keyword: request.keyword,
    pageSize: request.pageSize,
    round: request.round,
    traceId: request.traceId,
  };
  if (request.cursor) {
    payload.cursor = request.cursor;
  }
  if (request.providerFilters !== undefined) {
    payload.providerFilters = request.providerFilters;
  }
  return payload;
}

function toPythonWorkerCard(card: WorkerCandidateCard): PythonWorkerCandidateCard {
  const providerSubjectId = card.providerCandidateId || null;
  return {
    payload: objectPayload(card.rawPayload),
    normalized_text: card.searchableText,
    provider_subject_id: providerSubjectId,
    provider_listing_id: stringPayloadValue(card.rawPayload, "listingId"),
    synthetic_candidate_fingerprint: card.providerCandidateId || syntheticFingerprint(card),
    identity_confidence: providerSubjectId ? "provider_subject_id" : "synthetic_fingerprint",
    extraction_source: card.extractionSource,
    extractor_version: card.extractorVersion || EXTRACTOR_VERSION,
    pii_classification: card.privacy.containsDirectContact ? "direct_contact_possible" : "no_direct_contact",
    retention_policy: "provider_snapshot_7d",
    access_scope: "local_run_only",
    redaction_state: "raw_provider_payload",
  };
}

function syntheticFingerprint(card: WorkerCandidateCard): string {
  return card.searchableText || `${card.provider}:${card.extractionSource}`;
}

function objectPayload(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function stringPayloadValue(value: unknown, key: string): string | null {
  const payload = objectPayload(value);
  const field = payload[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}
