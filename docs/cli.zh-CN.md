# CLI

[English](cli.md)

`SeekTalent` 有两个终端入口：

- 不带参数运行 `seektalent` 时，如果 stdin/stdout 是交互式终端，会进入交互式 TUI。
- 直接命令可以写成 `seektalent <command>`，也可以写成 `seektalent exec <command>`。

`seektalent --help` 显示顶层交互式 shell。`seektalent exec --help` 显示完整的直接命令列表。

推荐的黑盒使用顺序：

```bash
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

## 命令

| 命令 | 用途 |
| --- | --- |
| `seektalent run` | 运行一次简历匹配流程。 |
| `seektalent benchmark` | 从 JSONL 文件运行一组 benchmark JD。 |
| `seektalent migrate-judge-assets` | 从已有 run artifacts 重建本地 judge asset 数据库。 |
| `seektalent init` | 写入 starter env 文件。 |
| `seektalent doctor` | 运行本地配置检查，不发网络请求。 |
| `seektalent version` | 打印当前安装包版本。 |
| `seektalent update` | 打印升级说明。 |
| `seektalent inspect` | 配合 `--json` 输出机器可读 CLI contract。 |

上表里的每个命令也都可以放在 `seektalent exec` 后面运行，例如 `seektalent exec run ...`。

## `seektalent run`

每次运行都需要：

- 岗位名称必须且只能提供一种来源：`--job-title` 或 `--job-title-file`
- JD 必须且只能提供一种来源：`--jd` 或 `--jd-file`
- notes 可选；如果提供，也最多只能提供一种来源：`--notes` 或 `--notes-file`

示例：

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

```bash
seektalent run \
  --job-title-file ./job_title.md \
  --jd-file ./jd.md \
  --notes-file ./notes.md
```

常用选项：

| 选项 | 用途 |
| --- | --- |
| `--env-file ./local.env` | 加载指定 env 文件。 |
| `--output-dir ./outputs` | 把 run artifacts 写到自定义根目录。 |
| `--json` | 成功时 stdout 只输出一个 JSON 对象。 |
| `--max-rounds N` / `--min-rounds N` | 覆盖检索轮数上下限。 |
| `--scoring-max-concurrency N` | 覆盖评分并发数。 |
| `--search-max-pages-per-round N` | 覆盖每轮 CTS 翻页预算。 |
| `--search-max-attempts-per-round N` | 覆盖每轮 CTS 尝试次数预算。 |
| `--search-no-progress-limit N` | 覆盖连续无进展阈值。 |
| `--enable-eval` / `--disable-eval` | 为本次运行覆盖 judge + eval 开关。 |
| `--enable-reflection` / `--disable-reflection` | 为本次运行覆盖 reflection 开关。 |

默认成功输出是人类可读的最终 markdown，以及 `run_id`、`run_directory`、`trace_log`。使用 `--json` 时，成功时 stdout 只输出一个 JSON 对象，失败时 stderr 只输出一个 JSON 对象。

## `seektalent benchmark`

从维护中的 benchmark 目录运行 benchmark：

```bash
seektalent benchmark \
  --benchmarks-dir ./artifacts/benchmarks \
  --output-dir ./runs/benchmark \
  --benchmark-max-concurrency 6 \
  --enable-eval
```

运行显式指定的 JSONL 文件：

```bash
seektalent benchmark \
  --jds-file ./artifacts/benchmarks/agent_jds.jsonl \
  --output-dir ./runs/benchmark
```

每一行必须包含 `job_title` 和 `job_description`。允许有额外字段。

常用选项：

| 选项 | 用途 |
| --- | --- |
| `--jds-file PATH` | 可选的单个输入 JSONL。省略时扫描 `--benchmarks-dir`。 |
| `--benchmarks-dir PATH` | 维护中的 benchmark JSONL 目录；默认 `artifacts/benchmarks`。 |
| `--benchmark-max-concurrency N` | 最多并行运行 N 条 benchmark；默认 `1`。 |
| `--benchmark-run-retries N` | 每条失败的 benchmark row 重试 N 次；默认 `1`。 |
| `--benchmark-upload-retries N` | 每个失败的远端 eval 上传重试 N 次；默认 `1`。 |
| `--env-file PATH` | 加载指定 env 文件。 |
| `--output-dir PATH` | 把 benchmark run artifacts 写到自定义根目录。 |
| `--json` | stdout 输出一个 JSON 对象。 |
| `--enable-eval` / `--disable-eval` | 覆盖 judge + eval 开关。 |
| `--enable-reflection` / `--disable-reflection` | 覆盖 reflection 开关。 |

默认目录模式会跳过生成或临时 JSONL，例如 `phase_*.jsonl`、`*.tmp.jsonl`、`*.only.jsonl` 和 `*.subset.jsonl`。开启 eval 时，本地 run 可以并行，judge 请求共享进程级并发上限，Weave/W&B 上传会在本地 eval artifact 写完后串行执行。

该命令会在配置的 runs 目录下写入 `benchmark_summary_*.json`。

## `seektalent migrate-judge-assets`

从 run artifacts 重建本地 judge asset 数据库：

```bash
seektalent migrate-judge-assets --runs-dir runs --project-root .
```

加 `--json` 可输出机器可读的迁移摘要。

## Setup 命令

写入 starter env 文件：

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

运行本地检查，不发网络请求：

```bash
seektalent doctor
seektalent doctor --json
```

打印版本或升级说明：

```bash
seektalent version
seektalent update
```

检查发布版 CLI contract：

```bash
seektalent inspect --json
```

## 失败行为

CLI 会在这些情况下 fail fast：

- 缺少必填输入文本
- 同一个字段同时使用互斥输入参数
- settings 校验失败
- 缺少 provider 凭证
- 真实 CTS 模式下缺少 CTS 凭证
- 通过发布版 CLI 路径请求 mock CTS
- 任意 runtime stage 抛出异常

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
