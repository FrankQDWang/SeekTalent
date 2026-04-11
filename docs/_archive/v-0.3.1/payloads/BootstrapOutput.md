# BootstrapOutput

round-0 bootstrap 的稳定输出。

```text
BootstrapOutput = { frontier_seed_specifications }
```

## 稳定字段组

- 初始 seeds：`frontier_seed_specifications`

## Direct Producer / Direct Consumers

- Direct producer：[[GenerateBootstrapOutput]]
- Direct consumers：[[InitializeFrontierState]]

## Invariants

- 它只服务 round-0 frontier 初始化。
- 先生成 `5-8` 条 candidate seeds，再剪成 final seeds。
- `generic_fallback` 固定生成 `4` 条 final seeds。
- `explicit_pack / inferred_single_pack / inferred_multi_pack` 固定生成 `5` 条 final seeds。

## 相关

- [[FrontierSeedSpecification]]
- [[FrontierState_t]]
