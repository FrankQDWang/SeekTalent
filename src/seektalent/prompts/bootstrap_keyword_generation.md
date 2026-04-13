请为 round-0 搜索启动生成严格的结构化 bootstrap keyword draft。

把归一化后的 requirement 视为 frozen truth。
CTS 关键词检索是 conjunctive 的，所以 seed phrase 必须短、紧、可执行，不要把不相关约束捆成一长串。

你会收到：
- `requirement`：归一化后的 requirement sheet
- `routing`：routing mode 和选中的 knowledge pack ids
- `packs`：命中的 knowledge pack 上下文

返回一个 `BootstrapKeywordDraft`，包含：
- `candidate_seeds`：5-8 个 seed intents
- `negative_keywords`：全局 negative keywords

规则：
- 只能使用提供的 requirement、routing、packs。
- 不要发明 packs 之外的领域事实。
- `positive_hints` 只是正向扩展提示。
- `negative_hints` 只能做排除词，不能拿来做正向关键词。
- `keywords` 必须像短的可执行搜索短语，不能写成解释句。
- 每个 `keywords` 列表都要短、具体、可 materialize。
- `reasoning` 只能是一句短说明，不能只是复述关键词。
- `source_knowledge_pack_ids` 只有在 seed 真的依赖 pack 上下文时才允许非空。

语言规则：
- 默认优先生成中文搜索短语。
- 输入里明确出现、且本来就稳定使用英文的术语可以保留，例如 `Agent Runtime`、`tool calling`、`MCP`。
- 英文术语只能作为补充，不应该主导整条 seed phrase。
- 对中文 JD，不要把整条 seed 生成成纯英文能力标签。

按 routing mode 必须覆盖的 intents：
- `generic_fallback`：必须包含 `core_precision`、`must_have_alias`、`relaxed_floor`、`vocabulary_bridge`
- `explicit_pack` 或 `inferred_single_pack`：必须包含 `core_precision`、`must_have_alias`、`relaxed_floor`、`pack_bridge`
- `inferred_multi_pack`：必须包含 `core_precision`、`must_have_alias`、`relaxed_floor`、一个带单 pack id 的 `pack_bridge`、一个带双 pack ids 的 `pack_bridge`

pack-aware intent 额外规则：
- `pack_bridge` 只能使用一个已选 pack 或两个已选 packs，并且必须准确引用这些 pack ids

输出里不要加入额外解释。
