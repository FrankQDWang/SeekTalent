import { redactFixturePayload } from "./redaction";

export const EXTRACTOR_VERSION = "liepin-passive-extractor-v1" as const;

export type ExtractionSource = "network" | "dom_fallback";

export type PrivacyMetadata = {
  redactionPolicyVersion: string;
  containsDirectContact: boolean;
};

export type WorkerCandidateCard = {
  provider: "liepin";
  providerCandidateId: string;
  extractionSource: ExtractionSource;
  extractorVersion: typeof EXTRACTOR_VERSION;
  rawPayload: unknown;
  searchableText: string;
  privacy: PrivacyMetadata;
  missingFields: string[];
};

export type WorkerCandidateDetail = WorkerCandidateCard & {
  providerDetailId: string;
};

export type DomFallbackExtraction = {
  cards: WorkerCandidateCard[];
  repairHtml: string;
  selectorHealth: {
    cardSelector: boolean;
    idSelector: boolean;
    titleSelector: boolean;
  };
};

export type DetailDomFallbackExtraction = {
  detail: WorkerCandidateDetail;
  repairHtml: string;
  selectorHealth: {
    detailSelector: boolean;
    idSelector: boolean;
    titleSelector: boolean;
  };
};

export type WorkerCardExtractionResult = {
  extractionSource: ExtractionSource;
  cards: WorkerCandidateCard[];
  repairHtml?: string;
  selectorHealth?: DomFallbackExtraction["selectorHealth"];
};

type NetworkArtifact = {
  extractionSource?: string;
  redactedFixture?: {
    payload?: RedactedFixture;
    manifest?: RedactionManifestLike;
  };
};

type RedactionManifestLike = {
  redaction_policy_version?: string;
};

type RedactedFixture = {
  manifest?: {
    redaction_policy_version?: string;
  };
  data?: {
    cards?: unknown[];
    list?: unknown[];
    detail?: unknown;
  };
};

type CandidatePayload = {
  candidateId?: unknown;
  detailId?: unknown;
  title?: unknown;
  company?: unknown;
  location?: unknown;
  skills?: unknown;
  summary?: unknown;
  experience?: unknown;
  education?: unknown;
};

const DEFAULT_REDACTION_POLICY_VERSION = "unknown";
const CONTACT_PATTERN = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)/i;

export function extractCardsFromNetwork(fixture: RedactedFixture): WorkerCandidateCard[] {
  return (fixture.data?.cards ?? fixture.data?.list ?? []).map((card) =>
    buildCard(card as CandidatePayload, "network", privacyFromFixture(fixture))
  );
}

export function extractWorkerCards(options: {
  networkArtifacts: NetworkArtifact[];
  fallbackHtml: string;
}): WorkerCardExtractionResult {
  const networkCards = options.networkArtifacts
    .filter((artifact) => artifact.extractionSource === "network")
    .flatMap((artifact) => {
      const fixture = artifact.redactedFixture?.payload;
      return fixture
        ? extractCardsFromNetwork(withArtifactManifest(fixture, artifact.redactedFixture?.manifest))
        : [];
    })
    .filter(isCompleteCard);

  if (networkCards.length > 0) {
    return {
      extractionSource: "network",
      cards: networkCards,
    };
  }

  const fallback = extractCardsFromDomFallback(options.fallbackHtml);
  return {
    extractionSource: "dom_fallback",
    cards: fallback.cards.filter(isCompleteCard),
    repairHtml: fallback.repairHtml,
    selectorHealth: fallback.selectorHealth,
  };
}

export function extractDetailFromNetwork(fixture: RedactedFixture): WorkerCandidateDetail {
  const detail = (fixture.data?.detail ?? {}) as CandidatePayload;
  const card = buildCard(detail, "network", privacyFromFixture(fixture));
  const providerDetailId = stringValue(detail.detailId);

  return {
    ...card,
    providerDetailId,
    missingFields: [...card.missingFields, ...missingWhenEmpty(providerDetailId, "detailId")],
  };
}

export function extractDetailFromDomFallback(html: string, candidateId?: string): DetailDomFallbackExtraction {
  const detailFragments = matchAll(
    html,
    /<article\b[^>]*class=["'][^"']*\bcandidate-detail\b[^"']*["'][^>]*>[\s\S]*?<\/article>/gi
  );
  const fragment = candidateId
    ? detailFragments.find((entry) => attrValue(entry, "data-candidate-id") === candidateId) ?? ""
    : detailFragments[0] ?? "";
  const providerCandidateId = attrValue(fragment, "data-candidate-id");
  const providerDetailId = attrValue(fragment, "data-detail-id") || providerCandidateId;
  const title = textForClass(fragment, "candidate-title");
  const company = textForClass(fragment, "candidate-company");
  const summary = textForClass(fragment, "candidate-summary");
  const skills = matchAll(fragment, /<li[^>]*>([\s\S]*?)<\/li>/gi).map(cleanText);
  const rawPayload = { providerCandidateId, providerDetailId, title, company, summary, skills };
  const card = buildCard(
    {
      candidateId: providerCandidateId,
      detailId: providerDetailId,
      title,
      company,
      summary,
      skills,
    },
    "dom_fallback",
    {
      redactionPolicyVersion: "liepin-fixture-redaction-v1",
      containsDirectContact: CONTACT_PATTERN.test(html),
    },
    rawPayload
  );

  return {
    detail: {
      ...card,
      providerDetailId,
      missingFields: [...card.missingFields, ...missingWhenEmpty(providerDetailId, "detailId")],
    },
    repairHtml: redactedRepairHtml(html),
    selectorHealth: {
      detailSelector: detailFragments.length > 0,
      idSelector: providerCandidateId.length > 0,
      titleSelector: title.length > 0,
    },
  };
}

export function extractCardsFromDomFallback(html: string): DomFallbackExtraction {
  const cardFragments = matchAll(
    html,
    /<article\b[^>]*class=["'][^"']*\bcandidate-card\b[^"']*["'][^>]*>[\s\S]*?<\/article>/gi
  );

  const cards = cardFragments.map((fragment) => {
    const providerCandidateId = attrValue(fragment, "data-candidate-id");
    const title = textForClass(fragment, "candidate-title");
    const company = textForClass(fragment, "candidate-company");
    const skills = matchAll(fragment, /<li[^>]*>([\s\S]*?)<\/li>/gi).map(cleanText);
    const rawPayload = { providerCandidateId, title, company, skills };

    return buildCard(
      {
        candidateId: providerCandidateId,
        title,
        company,
        skills,
      },
      "dom_fallback",
      {
        redactionPolicyVersion: "liepin-fixture-redaction-v1",
        containsDirectContact: CONTACT_PATTERN.test(html),
      },
      rawPayload
    );
  });

  return {
    cards,
    repairHtml: redactedRepairHtml(html),
    selectorHealth: {
      cardSelector: cardFragments.length > 0,
      idSelector: cards.every((card) => card.providerCandidateId.length > 0),
      titleSelector: cards.every((card) => !card.missingFields.includes("title")),
    },
  };
}

function redactedRepairHtml(html: string): string {
  const redacted = redactFixturePayload(html).payload;
  return typeof redacted === "string" ? redacted : "";
}

function buildCard(
  payload: CandidatePayload,
  extractionSource: ExtractionSource,
  privacy: PrivacyMetadata,
  rawPayload: unknown = payload
): WorkerCandidateCard {
  const providerCandidateId = stringValue(payload.candidateId);
  const title = stringValue(payload.title);
  const company = stringValue(payload.company);
  const location = stringValue(payload.location);
  const skills = stringArray(payload.skills);
  const textParts = [
    title,
    company,
    location,
    ...skills,
    stringValue(payload.summary),
    stringValue(payload.experience),
    stringValue(payload.education),
  ];

  return {
    provider: "liepin",
    providerCandidateId,
    extractionSource,
    extractorVersion: EXTRACTOR_VERSION,
    rawPayload,
    searchableText: normalizeSearchableText(textParts),
    privacy,
    missingFields: [
      ...missingWhenEmpty(providerCandidateId, "candidateId"),
      ...missingWhenEmpty(title, "title"),
    ],
  };
}

function privacyFromFixture(fixture: RedactedFixture): PrivacyMetadata {
  return {
    redactionPolicyVersion:
      fixture.manifest?.redaction_policy_version ?? DEFAULT_REDACTION_POLICY_VERSION,
    containsDirectContact: CONTACT_PATTERN.test(JSON.stringify(fixture)),
  };
}

function withArtifactManifest(
  fixture: RedactedFixture,
  manifest: RedactionManifestLike | undefined
): RedactedFixture {
  if (fixture.manifest !== undefined || manifest === undefined) {
    return fixture;
  }

  return {
    ...fixture,
    manifest,
  };
}

function normalizeSearchableText(parts: string[]): string {
  return parts.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.flatMap((entry) => (typeof entry === "string" ? [entry] : []))
    : [];
}

function missingWhenEmpty(value: string, field: string): string[] {
  return value.length === 0 ? [field] : [];
}

function isCompleteCard(card: WorkerCandidateCard): boolean {
  return card.missingFields.length === 0;
}

function matchAll(value: string, pattern: RegExp): string[] {
  return [...value.matchAll(pattern)].map((match) => match[1] ?? match[0] ?? "");
}

function attrValue(fragment: string, attribute: string): string {
  const escapedAttribute = attribute.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = fragment.match(new RegExp(`${escapedAttribute}=["']([^"']+)["']`, "i"));
  return cleanText(match?.[1] ?? "");
}

function textForClass(fragment: string, className: string): string {
  const escapedClass = className.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = fragment.match(
    new RegExp(`<[^>]*class=["'][^"']*\\b${escapedClass}\\b[^"']*["'][^>]*>([\\s\\S]*?)<\\/[^>]+>`, "i")
  );
  return cleanText(match?.[1] ?? "");
}

function cleanText(value: string): string {
  return value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}
