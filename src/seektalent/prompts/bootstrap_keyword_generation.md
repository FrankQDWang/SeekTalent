Generate a strict structured bootstrap keyword draft for round-0 search startup.

You will receive:
- `requirement`: the normalized requirement sheet fields
- `routing`: the routing mode and selected knowledge pack ids
- `packs`: the routed knowledge pack context

Return a `BootstrapKeywordDraft` with:
- `candidate_seeds`: 5-8 seed intents
- `negative_keywords`: global negative keywords

Rules:
- Use only the provided requirement, routing, and packs.
- Do not invent unsupported domain facts outside the provided packs.
- `positive_hints` are positive expansion hints.
- `negative_hints` are exclusion hints only. Do not use them as positive keywords.
- `keywords` must look like short executable search phrases, not explanations or sentences.
- Keep each `keywords` list compact and concrete.
- `reasoning` must be one short sentence and must not just repeat the keywords.
- `source_knowledge_pack_ids` must be empty unless the seed truly depends on pack context.

Required intents by routing mode:
- `generic_fallback`: include `core_precision`, `must_have_alias`, `relaxed_floor`, `generic_expansion`
- `explicit_pack` or `inferred_single_pack`: include `core_precision`, `must_have_alias`, `relaxed_floor`, `pack_expansion`
- `inferred_multi_pack`: include `core_precision`, `must_have_alias`, `relaxed_floor`, `pack_expansion`, `cross_pack_bridge`

Extra rules for pack-aware intents:
- `pack_expansion` must use one selected pack and reference it in `source_knowledge_pack_ids`
- `cross_pack_bridge` must combine both selected packs and reference exactly those two pack ids
