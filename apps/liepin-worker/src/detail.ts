import {
  extractDetailFromDomFallback,
  extractDetailFromNetwork,
  type WorkerCandidateDetail,
} from "./extraction";
import {
  captureResponsesDuringAction,
  type CapturedResponseRecord,
} from "./networkCapture";

type ResponseLike = {
  url(): string;
  status(): number;
  json(): Promise<unknown>;
};

type PageLike = {
  on(event: "response", handler: (response: ResponseLike) => void): void;
  off(event: "response", handler: (response: ResponseLike) => void): void;
  content?: () => Promise<string>;
};

export type DetailOpenRequest = {
  requestId: string;
  attemptId: string;
  idempotencyKey: string;
  candidateId: string;
};

export type DetailOpenDiagnostics = {
  pageLoaded: boolean;
  payloadSeen: boolean;
  extractionSource: "network" | "dom_fallback" | null;
  messages: string[];
};

export type DetailOpenResult = {
  requestId: string;
  attemptId: string;
  idempotencyKey: string;
  status: "completed" | "failed_after_possible_consumption";
  workerResponseId: string;
  workerCommandId: string;
  rawEvidenceRef?: string;
  candidate?: PythonWorkerCandidateDetail;
  diagnostics: DetailOpenDiagnostics;
};

export type DetailOpenResponse = {
  workerCommandId: string;
  results: DetailOpenResult[];
};

export type PythonWorkerCandidateDetail = {
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

export async function openDetails(options: {
  page: PageLike;
  requests: DetailOpenRequest[];
  openRequest: (request: DetailOpenRequest) => Promise<void>;
  postActionCaptureMs?: number;
  workerCommandId: string;
}): Promise<DetailOpenResponse> {
  const results: DetailOpenResult[] = [];
  for (const request of options.requests) {
    results.push(await openOneDetail(options, request));
  }
  return {
    workerCommandId: options.workerCommandId,
    results,
  };
}

async function openOneDetail(
  options: {
    page: PageLike;
    openRequest: (request: DetailOpenRequest) => Promise<void>;
    postActionCaptureMs?: number;
    workerCommandId: string;
  },
  request: DetailOpenRequest
): Promise<DetailOpenResult> {
  const captureOptions =
    options.postActionCaptureMs === undefined
      ? {}
      : { postActionCaptureMs: options.postActionCaptureMs };
  const captured = await captureResponsesDuringAction(options.page, async () => {
    await options.openRequest(request);
  }, captureOptions);
  const network = detailFromNetwork(captured, request.candidateId);
  if (network !== null) {
    return completedResult({
      request,
      workerCommandId: options.workerCommandId,
      candidate: network.detail,
      rawEvidenceRef: network.rawEvidenceRef,
      extractionSource: "network",
    });
  }

  const html = options.page.content ? await options.page.content() : "";
  const fallback = extractDetailFromDomFallback(html, request.candidateId);
  if (fallback.detail.missingFields.length === 0) {
    return completedResult({
      request,
      workerCommandId: options.workerCommandId,
      candidate: fallback.detail,
      rawEvidenceRef: `dom:${request.candidateId}`,
      extractionSource: "dom_fallback",
    });
  }

  return {
    requestId: request.requestId,
    attemptId: request.attemptId,
    idempotencyKey: request.idempotencyKey,
    status: "failed_after_possible_consumption",
    workerResponseId: `${options.workerCommandId}:${request.attemptId}`,
    workerCommandId: options.workerCommandId,
    diagnostics: {
      pageLoaded: true,
      payloadSeen: false,
      extractionSource: null,
      messages: ["detail payload not found after visible open"],
    },
  };
}

function completedResult(options: {
  request: DetailOpenRequest;
  workerCommandId: string;
  candidate: WorkerCandidateDetail;
  rawEvidenceRef: string;
  extractionSource: "network" | "dom_fallback";
}): DetailOpenResult {
  return {
    requestId: options.request.requestId,
    attemptId: options.request.attemptId,
    idempotencyKey: options.request.idempotencyKey,
    status: "completed",
    workerResponseId: `${options.workerCommandId}:${options.request.attemptId}`,
    workerCommandId: options.workerCommandId,
    rawEvidenceRef: options.rawEvidenceRef,
    candidate: toPythonWorkerDetail(options.candidate),
    diagnostics: {
      pageLoaded: true,
      payloadSeen: true,
      extractionSource: options.extractionSource,
      messages: [],
    },
  };
}

function toPythonWorkerDetail(detail: WorkerCandidateDetail): PythonWorkerCandidateDetail {
  const providerSubjectId = detail.providerCandidateId || null;
  return {
    payload: objectPayload(detail.rawPayload),
    normalized_text: detail.searchableText,
    provider_subject_id: providerSubjectId,
    provider_listing_id: stringPayloadValue(detail.rawPayload, "listingId"),
    synthetic_candidate_fingerprint: detail.providerCandidateId || detail.providerDetailId,
    identity_confidence: providerSubjectId ? "provider_subject_id" : "synthetic_fingerprint",
    extraction_source: detail.extractionSource,
    extractor_version: detail.extractorVersion,
    pii_classification: detail.privacy.containsDirectContact ? "direct_contact_possible" : "no_direct_contact",
    retention_policy: "provider_snapshot_7d",
    access_scope: "local_run_only",
    redaction_state: "raw_provider_payload",
  };
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

function detailFromNetwork(
  records: CapturedResponseRecord[],
  candidateId: string
): { detail: WorkerCandidateDetail; rawEvidenceRef: string } | null {
  for (const record of records) {
    const fixture = record.redactedFixture.payload;
    if (!fixture || typeof fixture !== "object") {
      continue;
    }
    const detail = extractDetailFromNetwork(fixture);
    if (detail.missingFields.length === 0 && detail.providerCandidateId === candidateId) {
      return {
        detail,
        rawEvidenceRef: `network:${record.endpointFingerprint}:${record.responseShapeHash}`,
      };
    }
  }
  return null;
}
