# CLI

[English](cli.md)

规范入口是：

```bash
seektalent --help
```

在 TTY 里，`seektalent` 无参数会直接打开一次性聊天式 `Textual` 会话。`seektalent --help` 仍然是给人类和 agent 的标准协议入口。

## 当前阶段

这个 CLI 现在是 `v0.3.3 active` 表面。

- `doctor`、`init`、`version`、`update`、`inspect`、`run` 可用

## 命令

### `seektalent`

在 TTY 里，裸命令会直接打开一次性聊天式 `Textual` 会话：

```bash
seektalent
```

这个会话提供：

- 单一 transcript 作为唯一主区域
- 第一段输入 `Job Description`
- 第二段输入可选的 `Hiring Notes`
- `Shift+Enter` 提交，`Enter` 换行
- 同一条对话里持续追加 working transcript 和最终结果
- 每次启动只跑一轮；要重来就重新执行 `seektalent`

### `seektalent init`

写出 repo env 模板：

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

这个命令会写出随安装包一起分发的 starter 模板。

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

`doctor` 现在会校验每个 callpoint 的 LLM 配置矩阵。`inspect --json` 还会返回 interactive entry、聊天式会话流程、non-interactive request contract、progress contract，以及最终结果指针。

### `seektalent run`

这是非交互协议入口。

推荐输入方式：

- `--request-file <path>`
- `--request-stdin`
- `--jd-file <path>`，可选 `--notes-file <path>`

其他参数：

- `--round-budget`
- `--progress text|jsonl|off`
- `--env-file`
- `--json`

示例：

```bash
seektalent run --request-file ./request.json
seektalent run --request-file ./request.json --json --progress jsonl
cat request.json | seektalent run --request-stdin --json --progress jsonl
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

当前真实行为是：

- 执行完整 runtime loop，并写出 run artifacts
- `--round-budget` 会覆盖 request payload 里的值以及 `SEEKTALENT_ROUND_BUDGET`
- human 模式把实时进度写到 `stderr`，完成后把紧凑结果摘要写到 `stdout`
- `--progress jsonl` 会把稳定的 JSONL 进度事件写到 `stderr`
- `--json` 模式下把 `SearchRunBundle.model_dump(mode="json")` 直接写到 stdout
- 最终产品结果看 `final_result.final_candidate_cards`

`--jd` / `--notes` 这种 inline 长文本参数已经删除。请改用 request file、request stdin，或者直接使用 Textual 会话。

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
