---
name: liepin-search-cards
description: Collect Liepin search result cards through DokoBot inside Pi only.
---

# Liepin Card Search

Use DokoBot only through the Pi runtime. Do not call provider APIs directly, do
not replay browser cookies. Do not ask for cookies, tokens, SMS codes,
passwords, localStorage, sessionStorage, or other credentials.

The browser is expected to already be logged in by the user. If login,
permission, captcha, or risk-control blocks the task, stop and return the
blocked JSON envelope.

In card mode:

- Search with the supplied keyword query.
- Preserve the provider search result order.
- Read only the search result card/listing surface.
- Do not open candidate detail pages in card mode.
- Do not click contact, chat, download, phone, email, or resume-detail actions.
- Store protected page traces, provider key material, and snapshots under
  `SEEKTALENT_PI_ARTIFACT_ROOT`.
- Return artifact refs only, never raw HTML, cookies, tokens, contact data, or
  raw resumes.

Return exactly one JSON object as the final assistant message. Do not wrap it in
Markdown and do not include notes before or after it.

Required card fields include `provider_candidate_key_material_ref`,
`safe_card_summary`, `safe_card_summary_ref`, and `protected_snapshot_ref`.
