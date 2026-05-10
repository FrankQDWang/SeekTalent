# Pinpin Liepin Mapping Notes

These notes are a read-only implementation reference for SeekTalent's Liepin source work. They are not a license to copy Pinpin production code into this repository.

## Local Evidence

Installed extension:

```text
/Users/frankqdwang/Library/Application Support/Google/Chrome/Default/Extensions/gjennbhoiojgmldlfddhmklegihhdeci/600.0.796_0
```

Relevant observed files:

- `manifest.json` identifies the extension as `品聘插件`, version `600.0.0796`, and declares host access for Liepin, Maimai, 51job, Zhipin, Zhaopin, LinkedIn, and other recruiting sources.
- `manifest.json` injects shared UI and page scripts through a content script, which confirms Pinpin's product is multi-source rather than Liepin-only.
- `request.js` contains the most useful Liepin platform mapping evidence. It includes a `liepinSearch` branch, Liepin version handling, XSRF cookie lookups, and request-header mappings for several Liepin hosts.

Important observed `request.js` regions:

- Lines 296-304: `liepinSearch` reads `liepinVersion` from extension storage and defaults request data to `V3`.
- Lines 532-570: `api-h.liepin.com/api/com.liepin.searchfront4r.h.search-resumes` branch sets the `h.liepin.com` origin/referer/client headers and XSRF header.
- Lines 571-609: `api-lpt.liepin.com` branch maps the `lpt.liepin.com` origin and `/cvsearch/showcondition/` referer.
- Lines 610-647: `apic.liepin.com/api/com.liepin.searchfront4c.pc-search-job` branch maps the candidate-side job-search API.
- Lines 648-685: `api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job` branch maps another job-search API host.

The currently open Pinpin webapp page at `https://ai.pinpinsoft.cn/webapp/#/nav/webResumeSearch` showed a multi-source resume search UI with selectable sources including Liepin, Maimai, and Boss Zhipin. The Liepin result cards contained card-level fields and links to `https://h.liepin.com/resume/showresumedetail/...`.

## What We Should Borrow

Use Pinpin as a mapping reference for:

- source taxonomy and source switching UX;
- Liepin card fields that are available before opening details;
- Liepin detail-page URL patterns and candidate deeplink behavior;
- search version, host, endpoint, referer, and XSRF-sensitive platform characteristics;
- likely risk states such as login expiry, verification challenge, no permission, and missing result payload;
- pagination and list-card DOM fallback hints;
- how a mature product distinguishes card-level resume review from detail-opening workflows.

Use these observations to improve tests and fixtures:

- add redacted network fixtures that match observed endpoint families;
- add DOM fixtures for list cards with detail URLs;
- add mapper tests for source, detail URL, score evidence source, and extraction source;
- add risk-state fixtures without storing cookies, tokens, or raw personal data.

Fixture rules:

- Pinpin-derived fixtures must be redacted before they enter the repository.
- Prefer synthetic fixtures that preserve field shape and state transitions without preserving real candidate data.
- Store provenance notes with fixtures so future maintainers know they came from Pinpin observation, not production SeekTalent behavior.
- Test fixtures may live under `docs/references` or test fixture directories only.
- No production code may import Pinpin extension modules.
- No production code may reuse Pinpin's request replay path.
- No fixture may include cookies, auth headers, storage state, XSRF token values, raw personal data, CDP URLs, worker URLs, or auth-bearing detail URLs.

## What We Must Not Borrow

Do not copy Pinpin source code into SeekTalent.

Do not replicate Pinpin's direct cookie/header replay model as the SeekTalent production path. The existing SeekTalent boundary remains:

- users log into Liepin inside the managed browser page;
- the Bun worker drives the real page and passively observes page-triggered network responses;
- no production code path uses Playwright `APIRequestContext`, `page.request`, `browserContext.request`, or equivalent direct authenticated HTTP calls to Liepin endpoints;
- no extension, cookie export, token paste, or user-side local daemon is required;
- no cookies, auth headers, storage state, CDP endpoints, raw provider payloads, or auth-bearing URLs appear in external API responses, events, logs, ordinary artifacts, or fixtures.

## Product Implication

Pinpin reinforces that the new UI should be source-agnostic. Liepin is one source with extra login, budget, and risk-control behavior. CTS is another source without those constraints. The workbench source cards should represent provider state and counters, not a hard-coded Liepin-only workflow.
