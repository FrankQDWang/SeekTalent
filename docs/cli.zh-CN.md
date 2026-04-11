# CLI

[English](cli.md)

规范入口是：

```bash
seektalent --help
```

## 当前阶段

这个 CLI 现在是 `v0.3.3 active` 表面。

- `doctor`、`init`、`version`、`update`、`inspect`、`run` 可用

## 命令

### `seektalent init`

写出 repo env 模板：

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

这个命令会直接读取仓库根目录的 `.env.example`，面向 source checkout 工作流。

### `seektalent doctor`

本地检查 runtime 表面，不发网络请求：

```bash
seektalent doctor
seektalent doctor --json
seektalent doctor --env-file ./local.env --json
```

### `seektalent version`

打印版本：

```bash
seektalent version
```

### `seektalent update`

打印升级说明：

```bash
seektalent update
```

### `seektalent inspect`

输出当前 CLI contract：

```bash
seektalent inspect
seektalent inspect --json
seektalent inspect --env-file ./local.env --json
```

`doctor` 现在会校验每个 callpoint 的 LLM 配置矩阵。`inspect --json` 现在会返回每个 callpoint 的 provider、model 和最终解析出的 output mode。

### `seektalent run`

这个命令接受：

- `--jd` 或 `--jd-file`
- `--notes` 或 `--notes-file`
- `--round-budget`
- `--env-file`
- `--json`

示例：

```bash
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

当前真实行为是：

- 执行完整 runtime loop，并写出 run artifacts
- `--round-budget` 会覆盖 `SEEKTALENT_ROUND_BUDGET`
- human 模式下打印 `run_dir`、`stop_reason`、`reviewer_summary`、以及 `run_summary`
- `--json` 模式下把 `SearchRunBundle.model_dump(mode="json")` 直接写到 stdout

失败仍会以一个 JSON 对象写到 stderr。

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
