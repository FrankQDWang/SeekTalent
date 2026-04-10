# SeekTalent v0.3.1 设计文档

当前 `HEAD` 的主链可以概括成：

`multi-pack bootstrap + frontier runtime + offline run artifacts`

## 当前设计结论

1. knowledge pack 只参与 bootstrap
2. bootstrap routing 现在是 `explicit_pack / inferred_single_pack / inferred_multi_pack / generic_fallback`
3. round-0 先生成 `5-8` 条 candidate seeds，再剪成 `4/5` 条 final seeds
4. runtime provenance 全链路统一用 `knowledge_pack_ids`
5. round-0 operator 直接保留 seed 语义：`core_precision / must_have_alias / relaxed_floor / pack_expansion / cross_pack_bridge / generic_expansion`
6. `SearchRunBundle` 仍是唯一运行事实源

## 当前高层对象

- `BootstrapRoutingResult`：pack 路由结果
- `BootstrapKeywordDraft`：候选 seed intents
- `BootstrapOutput`：最终 round-0 seeds
- `FrontierState_t`：frontier 状态
- `SearchExecutionPlan_t`：执行计划，沿用 `knowledge_pack_ids`

## 当前 generic fallback

- 不选任何 pack
- 仍然生成多意图 candidate seeds
- final seeds 固定为 4 条
