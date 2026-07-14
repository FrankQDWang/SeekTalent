# OpenCLI Fork 离线分发与能力握手

日期：2026-07-14
上游基线：OpenCLI tag `v1.8.6`，commit `cad35e7a6a5ff3f7d6b859bfa4c45195c0390260`

## 结论

SeekTalent 应把 FrankQDWang/OpenCLI fork 的 CLI/daemon 与配套 Chrome 扩展作为一个不可拆分的 browser bridge release 交付。生产运行时只读取安装包内经过 SHA-256 校验的本地资产，不执行 `npm install`，不访问 npm、GitHub，也不静默退回上游 OpenCLI。

CLI/daemon 与扩展必须共享同一个 `bridgeBuildId`。SeekTalent 在任何猎聘浏览器命令前验证实现身份、build ID、协议主版本和所需 capability；不匹配只使当前 Liepin source unavailable，不能影响其他数据源或整个 run。

普通非企业 Windows/macOS Chrome 不能在完全离线时由安装器静默安装本地扩展。Chrome 官方只支持 Chrome Web Store 或 self-hosting；Windows/macOS 的 self-hosted 自动安装限企业策略。普通用户需要首次开启开发者模式并“加载已解压的扩展”。安装包应提供固定扩展目录和明确的一次性引导，后续升级替换该目录并提示重载扩展或重启 Chrome。manifest 的固定 `key` 用于保持开发期/解压安装的 extension ID 稳定。参见 Chrome 的[扩展分发说明](https://developer.chrome.com/docs/extensions/how-to/distribute)、[其他安装方式限制](https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions)、[`key` 字段说明](https://developer.chrome.com/docs/extensions/reference/manifest/key)和[加载已解压扩展步骤](https://developer.chrome.com/docs/extensions/get-started/tutorial/hello-world)。

## 当前代码事实与缺口

SeekTalent 当前 [`opencli_launcher.py`](../../src/seektalent/opencli_launcher.py) 固定使用 `@jackwener/opencli@1.8.6`，缺失时会在用户机器执行 npm 安装。这只能保留为显式开发路径，不能进入生产 launcher。

当前 macOS Intel 离线构建已经会在构建机下载 OpenCLI 运行时和扩展、记录 SHA-256，并在安装时校验 bundle；但还存在以下缺口：

- 构建的是上游 npm 包与上游扩展，不是同一 fork commit 的配对产物；
- installer 在新版本验证完成前删除旧 runtime/extension，没有事务切换或可用回滚槽；
- launcher 仍保留生产运行时 npm 安装路径；
- 扩展 hello 只包含版本和兼容范围，无法证明 fork capability；
- OpenCLI `v1.8.6` 的 update check 会访问 npm registry 和 GitHub Releases；产品构建必须禁用；
- Windows 与其他 macOS 架构还没有同等的、可重放的配对产物流程。

上游 `v1.8.6` 的扩展版本是 `1.0.22`。现有构建固定的官方 release asset `opencli-extension-v1.0.22.zip` 大小为 `44990` 字节，SHA-256 为 `9d2e3d053948beab5d97124aa79b1532d2122e33e461eca56cac113afd33207a`。该值只用于验证当前基线；fork 发布后必须改为 fork 自己的扩展产物哈希。

## 不可拆分的 release identity

每次 fork 构建生成一个不可变 `bridgeBuildId`，建议格式为：

```text
seektalent-opencli-1.8.6+<fork-commit-short>
```

构建产物包含一份签名或随 SeekTalent 安装包整体签名保护的 `bridge-manifest.json`：

```json
{
  "schemaVersion": "seektalent.browser_bridge_bundle.v1",
  "implementation": "seektalent-opencli",
  "upstreamBase": {
    "tag": "v1.8.6",
    "commit": "cad35e7a6a5ff3f7d6b859bfa4c45195c0390260"
  },
  "forkCommit": "<full-fork-commit>",
  "bridgeBuildId": "seektalent-opencli-1.8.6+<short-commit>",
  "protocolVersion": {"major": 1, "minor": 0},
  "cli": {
    "version": "1.8.6",
    "entrypoint": "runtime/bin/opencli",
    "sha256": "<sha256>",
    "size": 0
  },
  "extension": {
    "version": "1.0.22.1",
    "id": "<stable-extension-id>",
    "sha256": "<sha256>",
    "size": 0,
    "manifestSha256": "<sha256>"
  },
  "capabilities": [
    "control-fence.v1",
    "tab.close-verified.v1",
    "tab.create-in-existing-window.v1",
    "tab.find.v1",
    "tab.idle-deadline.v1"
  ]
}
```

`bridgeBuildId`、fork commit、CLI hash、扩展 hash 和 capability list 由同一次 CI build 生成，不允许安装器或 launcher 自行拼接。版本字符串可供人阅读，但不能代替 build identity。

## 安装目录与事务切换

推荐目录模型：

```text
browser-bridge/
  releases/
    <bridgeBuildId>/
      runtime/
      extension/
      bridge-manifest.json
  extension-current/
  current.json
  previous.json
```

安装或升级顺序：

1. 把新 bundle 解压到全新的 release staging 目录，拒绝绝对路径、`..` 和越界 symlink；
2. 校验 bundle manifest、文件大小、SHA-256、平台和架构；
3. 本地启动新 daemon，读取其 status/hello，验证 build ID、协议和 capability；
4. 完整刷新稳定的 `extension-current` 目录；普通用户重载扩展或重启 Chrome 后才进入扩展验证；
5. 只有 CLI/daemon 与扩展握手成功后，原子更新 `current.json`，并把旧值写入 `previous.json`；
6. 删除 staging；保留至少一个已验证的 previous release。

任何步骤失败都保留当前可用版本。Windows 上不能依赖替换正在使用的目录：runtime 使用不可变 version slot，通过小型 pointer 文件切换；扩展更新需要 Chrome 释放文件或由安装器安排下一次启动完成。不能先删除旧 runtime 再验证新包。

回滚以“完整配对版本”为单位，把 `current.json` 切回 `previous.json`，并恢复匹配的 `extension-current`。禁止只回滚 CLI 或只回滚扩展。

## 开发与生产路径

### 开发

- 可以从 `/Users/frankqdwang/Agents/OpenCLI` source build；
- 可以用 npm 安装开发依赖；
- 可以使用 `npm link` 或显式环境变量指向 fork；
- Chrome 通过固定目录加载已解压扩展；
- 必须仍通过 capability handshake，不能因为是开发环境而接受上游扩展。

### 生产

- launcher 只解析已安装的 `current.json` 和 `bridge-manifest.json`；
- 只启动 manifest 指向的本地 entrypoint；
- 不执行 npm，不调用 GitHub，不运行上游 postinstall；
- fork 产品构建硬禁用 npm/GitHub update check 与上游安装提示，不能依靠用户环境变量碰巧关闭；
- 完整性校验至少在安装时和首次启动时执行；后续可使用受保护 stamp 缓存，但 stamp 必须包含 build ID、文件 SHA-256 和 manifest identity，不能只看 mtime/size；
- 缺失、损坏或不匹配时返回稳定错误，只关闭 Liepin browser source 能力。

## Extension hello 与 SeekTalent preflight

扩展 hello 增加以下字段，daemon status 原样回显：

```json
{
  "type": "hello",
  "contextId": "<chrome-context>",
  "implementation": "seektalent-opencli",
  "bridgeBuildId": "seektalent-opencli-1.8.6+<short-commit>",
  "version": "1.0.22.1",
  "compatRange": ">=1.8.6 <1.9.0",
  "protocolVersion": {"major": 1, "minor": 0},
  "capabilities": [
    "control-fence.v1",
    "tab.close-verified.v1",
    "tab.create-in-existing-window.v1",
    "tab.find.v1",
    "tab.idle-deadline.v1"
  ]
}
```

SeekTalent preflight 必须验证：

1. `implementation == "seektalent-opencli"`；
2. manifest、daemon 与 extension 的 `bridgeBuildId` 完全一致；
3. protocol major 完全一致；minor 只允许向后兼容；
4. 所有所需 capability 都存在；
5. CLI/daemon 和扩展产物哈希与 manifest 一致。

稳定失败码：

```text
opencli_bridge_integrity_failed
opencli_bridge_wrong_implementation
opencli_bridge_build_mismatch
opencli_bridge_protocol_mismatch
opencli_bridge_capability_missing
```

错误提示应明确告诉用户是扩展未加载、Chrome 尚未重载、误启用了上游扩展，还是本地资产损坏。不得自动尝试上游协议、创建新 Chrome 窗口或联网修复。

## Chrome 安装策略

| 用户环境 | 安装方式 | 离线更新 |
|---|---|---|
| 普通 Windows/macOS | 安装器落盘固定目录；首次由用户“加载已解压的扩展” | 替换固定目录后提示重载扩展或重启 Chrome |
| 企业托管 Chrome | 管理员使用 extension policy 和自托管 update URL | 可由企业内网策略完成 |
| Chrome Web Store | 不作为当前默认方案 | 依赖可访问商店网络 |

固定 manifest `key` 只能稳定 extension ID，不能绕过 Chrome 的安装许可或替用户打开开发者模式。

## 验收清单

1. 断网环境中安装、首次启动、搜索、关闭和重启均不产生 npm/GitHub 请求。
2. 任意 runtime、extension 或 manifest 字节被篡改时，安装/启动明确失败且旧版本仍可用。
3. 恶意 zip 路径、越界 symlink、错误平台和错误架构均被拒绝。
4. CLI 与扩展 build ID 不同、protocol major 不同或缺 capability 时，只禁用 Liepin source。
5. 同时启用上游扩展时拒绝握手，不回退到上游 `chrome.windows.create` 行为。
6. 安装过程在任意一步被强杀，重新启动后仍指向旧的完整版本或可安全继续 staging。
7. 替换扩展但 Chrome 未重载时报告 build mismatch，并给出重载指引，不启动业务命令。
8. stale daemon 或旧扩展连接存在时，只有与 current manifest 匹配的一对可通过 preflight。
9. 回滚同时恢复 runtime 与 extension，握手通过后再恢复 Liepin source。
10. Windows 和每个支持的 macOS 架构都从同一 CI contract 生成独立 hash 清单并通过断网安装测试。

## 对后续 Fork 原型的约束

[准备 OpenCLI 1.8.6 fork 原型环境](https://github.com/FrankQDWang/SeekTalent/issues/297) 应从精确 tag commit 建分支，并在第一次 patch 中同时完成：

- 通用 borrowed-host tab 能力、verified close、fencing 和 idle deadline；
- extension/daemon capability hello；
- 构建时注入统一 `bridgeBuildId`；
- 产品构建关闭 update check；
- CLI 与扩展的可重复 build 命令和 hash 记录。

timer UI、本地 registry、后台 reclaimer、Liepin URL 规则和业务故障隔离仍属于 SeekTalent，不进入 OpenCLI fork。

## 2026-07-14 原型实现结果

- Fork：`FrankQDWang/OpenCLI`
- 分支：`codex/seektalent-browser-lifecycle-v1.8.6`
- Commit：`1c915be9323d21e45c3a423ea4406888d3058ee6`
- 本地路径：`/Users/frankqdwang/Agents/OpenCLI`
- Extension version：`1.0.22.1`
- 固定 extension ID：`aijmoehobdolindhgdljiaiimngpghcn`
- 配对 build ID：`seektalent-opencli-1.8.6+1c915be9323d`

该 commit 已实现只读 host tab 查找、borrowed user window 中的 inactive owned tab、精确验证关闭、extension 持久化 fencing、实际 `idleDeadlineAt`、daemon/extension capability hello、稳定扩展 ID、禁用上游更新检查，以及从干净 commit 生成配对 runtime/extension/hash manifest 的离线 bundle 脚本。

验证结果：CLI 与 extension typecheck 通过；全量 `npm test` 为 548 个 test files、5965 passed、1 skipped；CLI 与 extension build 通过；配对 bundle 的 runtime SHA-256 独立复核通过，runtime 与 extension 均包含同一个 build ID。页面灰色锁定层、60 秒可视倒计时和 SeekTalent 非阻塞后台回收仍留给后续 SeekTalent 端原型。
