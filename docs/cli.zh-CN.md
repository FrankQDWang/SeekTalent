# CLI

[English](cli.md)

规范 CLI 入口是：

```bash
seektalent --help
```

推荐的黑盒使用顺序：

```bash
seektalent --help
seektalent doctor
seektalent run --job-title-file ./job_title.md --jd-file ./jd.md
seektalent inspect --json
seektalent update
```

## 命令

### `seektalent init`

在当前目录写入一个启动用 env 文件：

```bash
seektalent init
```

写入自定义路径：

```bash
seektalent init --env-file ./local.env
```

覆盖已存在文件：

```bash
seektalent init --force
```

### `seektalent doctor`

运行本地检查，不发网络请求：

```bash
seektalent doctor
```

机器可读输出：

```bash
seektalent doctor --json
```

### `seektalent version`

打印当前安装包版本：

```bash
seektalent version
```

### `seektalent update`

打印 pip 和 pipx 的升级说明：

```bash
seektalent update
```

### `seektalent inspect`

输出面向 wrappers、agents 和 automation 的发布版 CLI 描述：

```bash
seektalent inspect
seektalent inspect --json
```

## `seektalent run`

每次运行都需要两个必填输入和一个可选补充输入：

- job title
- job description
- 可选的 sourcing notes / sourcing preferences

岗位名称必须且只能提供一种来源：

- `--job-title` 或 `--job-title-file`

JD 必须且只能提供一种来源：

- `--jd` 或 `--jd-file`

如果你需要补充寻访偏好，也只能提供一种来源：

- `--notes` 或 `--notes-file`

### 传岗位名称和 JD 运行

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience"
```

### 直接传文本运行

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles"
```

### 从文件运行

```bash
seektalent run \
  --job-title-file ./job_title.md \
  --jd-file ./jd.md \
  --notes-file ./notes.md
```

### 覆盖输出目录

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --output-dir ./outputs
```

### 使用自定义 env 文件

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --env-file ./local.env
```

### 机器可读输出

```bash
seektalent run \
  --job-title "Python agent engineer" \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --json
```

在 `--json` 模式下，成功时 stdout 只会输出一个 JSON 对象；失败时 stderr 只会输出一个 JSON 对象。

## 成功输出

默认成功输出是人类可读文本：

- final markdown answer
- `run_id`
- `run_directory`
- `trace_log`

如果不传 `--output-dir`，产物会写到当前工作目录下的 `./runs`。

## 失败行为

CLI 会在这些情况下 fail fast：

- 缺少岗位名称
- 缺少 JD
- 同一个字段同时传了 inline 和 file 两种输入
- 模型配置不合法
- 缺少 provider 凭证
- 缺少 CTS 凭证
- 通过配置请求了 mock CTS
- 任意 runtime stage 抛出异常

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
