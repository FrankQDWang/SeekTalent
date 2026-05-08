import { describe, expect, it } from "bun:test";

import { openDetails } from "../src/detail";

class FakePage {
  readonly responseHandlers: Array<(response: FakeResponse) => void> = [];
  private html: string;

  constructor(html = "") {
    this.html = html;
  }

  on(event: "response", handler: (response: FakeResponse) => void): void {
    expect(event).toBe("response");
    this.responseHandlers.push(handler);
  }

  off(event: "response", handler: (response: FakeResponse) => void): void {
    expect(event).toBe("response");
    const index = this.responseHandlers.indexOf(handler);
    if (index !== -1) {
      this.responseHandlers.splice(index, 1);
    }
  }

  async content(): Promise<string> {
    return this.html;
  }

  emitResponse(response: FakeResponse): void {
    for (const handler of this.responseHandlers) {
      handler(response);
    }
  }
}

class FakeResponse {
  constructor(private readonly body: unknown) {}

  url(): string {
    return "https://www.liepin.com/api/detail?candidateId=cand-redacted-1&token=secret";
  }

  status(): number {
    return 200;
  }

  async json(): Promise<unknown> {
    return this.body;
  }
}

describe("detail open command", () => {
  it("opens preapproved details with passive network capture before DOM fallback", async () => {
    const page = new FakePage("<article class=\"candidate-detail\" data-candidate-id=\"dom-only\"></article>");

    const result = await openDetails({
      page,
      requests: [
        {
          requestId: "request-1",
          attemptId: "attempt-1",
          idempotencyKey: "open:cand-redacted-1",
          candidateId: "cand-redacted-1",
        },
      ],
      openRequest: async () => {
        page.emitResponse(
          new FakeResponse({
            data: {
              detail: {
                candidateId: "cand-redacted-1",
                detailId: "detail-redacted-1",
                title: "Senior Backend Engineer",
                company: "Redacted Cloud",
                skills: ["Python", "Kubernetes"],
                summary: "distributed systems",
              },
            },
          })
        );
      },
      postActionCaptureMs: 0,
      workerCommandId: "cmd-1",
    });

    expect(result.workerCommandId).toBe("cmd-1");
    expect(result.results).toHaveLength(1);
    expect(result.results[0]).toMatchObject({
      requestId: "request-1",
      attemptId: "attempt-1",
      idempotencyKey: "open:cand-redacted-1",
      status: "completed",
      workerCommandId: "cmd-1",
      diagnostics: {
        pageLoaded: true,
        payloadSeen: true,
        extractionSource: "network",
      },
      candidate: {
        payload: {
          candidateId: "cand-redacted-1",
          detailId: "detail-redacted-1",
        },
        normalized_text: expect.stringContaining("distributed systems"),
        provider_subject_id: "cand-redacted-1",
        provider_listing_id: null,
        synthetic_candidate_fingerprint: "cand-redacted-1",
        identity_confidence: "provider_subject_id",
        extraction_source: "network",
        extractor_version: "liepin-passive-extractor-v1",
        pii_classification: "no_direct_contact",
        retention_policy: "provider_snapshot_7d",
        access_scope: "local_run_only",
        redaction_state: "raw_provider_payload",
      },
    });
    expect(result.results[0]?.rawEvidenceRef).toContain("network:");
  });

  it("uses DOM fallback only when network detail payload is missing", async () => {
    const page = new FakePage(`
      <article class="candidate-detail" data-candidate-id="cand-dom-1" data-detail-id="detail-dom-1">
        <h1 class="candidate-title">Data Platform Engineer</h1>
        <div class="candidate-company">Redacted Analytics</div>
        <section class="candidate-summary">Spark pipelines</section>
        <ul><li>Python</li><li>Spark</li></ul>
      </article>
    `);

    const result = await openDetails({
      page,
      requests: [
        {
          requestId: "request-dom",
          attemptId: "attempt-dom",
          idempotencyKey: "open:cand-dom-1",
          candidateId: "cand-dom-1",
        },
      ],
      openRequest: async () => undefined,
      postActionCaptureMs: 0,
      workerCommandId: "cmd-dom",
    });

    expect(result.results[0]).toMatchObject({
      status: "completed",
      diagnostics: {
        pageLoaded: true,
        payloadSeen: true,
        extractionSource: "dom_fallback",
      },
      candidate: {
        payload: {
          providerCandidateId: "cand-dom-1",
          providerDetailId: "detail-dom-1",
        },
        normalized_text: expect.stringContaining("Spark pipelines"),
        provider_subject_id: "cand-dom-1",
        synthetic_candidate_fingerprint: "cand-dom-1",
        extraction_source: "dom_fallback",
      },
    });
  });
});
