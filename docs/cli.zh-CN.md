# CLI

[English](cli.md)

规范 CLI 入口是：

```bash
deepmatch run --help
```

为兼容现有用法，当前一个发布周期内仍保留旧别名：

```bash
deepmatch --jd "Python agent engineer" --notes "Shanghai preferred" --mock-cts
```

## 命令

### `deepmatch init`

在当前目录写入一个启动用 env 文件：

```bash
deepmatch init
```

写入自定义路径：

```bash
deepmatch init --env-file ./local.env
```

覆盖已存在文件：

```bash
deepmatch init --force
```

### `deepmatch doctor`

运行本地检查，不发网络请求：

```bash
deepmatch doctor
```

机器可读输出：

```bash
deepmatch doctor --json
```

### `deepmatch version`

打印当前安装包版本：

```bash
deepmatch version
```

## `deepmatch run`

每次运行都需要一个必填输入和一个可选补充输入：

- job description
- 可选的 sourcing notes / sourcing preferences

JD 必须且只能提供一种来源：

- `--jd` 或 `--jd-file`

如果你需要补充寻访偏好，也只能提供一种来源：

- `--notes` 或 `--notes-file`

### 只用 JD 运行

```bash
deepmatch run \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --real-cts
```

### 直接传文本运行

```bash
deepmatch run \
  --jd "Python agent engineer with retrieval and ranking experience" \
  --notes "Shanghai preferred, avoid pure frontend profiles" \
  --real-cts
```

### 从文件运行

```bash
deepmatch run \
  --jd-file ./jd.md \
  --notes-file ./notes.md \
  --real-cts
```

### 覆盖输出目录

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
  --output-dir ./outputs
```

### 使用自定义 env 文件

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
  --env-file ./local.env
```

### 机器可读输出

```bash
deepmatch run \
  --jd "Python agent engineer" \
  --notes "Shanghai preferred" \
  --mock-cts \
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

- 缺少 JD
- 同一个字段同时传了 inline 和 file 两种输入
- 模型配置不合法
- 缺少 provider 凭证
- 在 `--real-cts` 模式下缺少 CTS 凭证
- 任意 runtime stage 抛出异常

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
