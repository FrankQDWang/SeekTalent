# CLI

[English](cli.md)

规范入口是：

```bash
seektalent --help
```

## 当前阶段

这个 CLI 现在只是 `v0.3 phase 1` skeleton。

- `doctor`、`init`、`version`、`update`、`inspect` 可用
- `run` 会故意 fail fast，并抛出 `Phase1RuntimeGateError`

## 命令

### `seektalent init`

写出默认 env 模板：

```bash
seektalent init
seektalent init --env-file ./local.env
seektalent init --force
```

### `seektalent doctor`

本地检查 phase 1 表面，不发网络请求：

```bash
seektalent doctor
seektalent doctor --json
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
```

### `seektalent run`

这个命令仍然接受计划中的输入：

- `--jd` 或 `--jd-file`
- `--notes` 或 `--notes-file`
- `--env-file`
- `--output-dir`
- `--json`

示例：

```bash
seektalent run --jd-file ./jd.md --notes-file ./notes.md
```

当前真实行为是：

- 先校验输入
- 再加载设置
- 随后立刻被 phase 1 runtime gate 拦下

在 `--json` 模式下，失败会以一个 JSON 对象写到 stderr。

## 相关文档

- [Configuration](configuration.md)
- [Outputs](outputs.md)
