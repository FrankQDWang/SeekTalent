# External Execution Plane v1 — Release Artifact and Evidence Contract

状态：**Wayfinder #326 Draft decision contract；docs-only，未实现、未发布**

Tracks: [#326](https://github.com/FrankQDWang/SeekTalent/issues/326)

主线输入：

- [Reliability Contract](./external-execution-plane-v1-reliability-contract.md)：完整产品发布物、逐平台 clean-machine gate、A/B rollback 与真实站点 canary；
- [Runtime Topology](./external-execution-plane-v1-runtime-topology.md)：main/sidecar/WTSCLI/browser 唯一 lifecycle owner、现有 profile compatibility mode 与 dedicated-profile spike；
- [Task Semantics](./external-execution-plane-v1-task-semantics.md)：drain、safe boundary、terminal immutability、schema backup 与 authority；
- [Diagnostics and Fault Injection](./external-execution-plane-v1-diagnostics-fault-injection.md)：canonical receipt/evidence/support schema 与逐平台 fault/release gates；
- [Source Execution Port](./external-execution-plane-v1-source-execution-port.md)：exact main/sidecar pairing、installed executable hash、rollback-journal sidecar data 与 T1/T2/T3 hard cut；
- GitHub issue #326、Wayfinder #319，以及当前 `code/schema/tests/build/release scripts`。代码与本文的 current-state 描述冲突时，以代码为准；目标契约不因此自动变成已实现事实。

## 1. 决策摘要

External Execution Plane v1 的生产发布单元是一个**逐平台、不可变、可离线安装、带签名且包含完整 execution plane 依赖闭包的产品 artifact**，不是 Python wheel、源码 checkout、开发机环境或在线 bootstrap 的结果。

本文冻结以下决策：

1. 一个产品 artifact 只声明一个 target tuple：`Windows 11 x64`、`macOS x86_64` 或 `macOS arm64`。三个 tuple 独立构建、独立签名、独立取证和独立裁决；默认不承诺同步 release train。
2. v1 packaging boundary 是一个 product-owned、self-contained onedir release payload，加一个平台签名的 installer/updater wrapper。实现可选择平台原生 installer、app bundle 或受控 Domi host integration，但用户机不得用 pip/npm/git/uv 在线解析产品依赖，且 Domi 不能成为未锁定依赖源。
3. 每个 payload 必须包含 exact main、Liepin Execution Sidecar、Python/SQLite runtime、Node/WTSCLI、browser bridge、Workbench static assets、installer/updater helpers 与 licenses。Chrome Stable、用户现有 profile、CWS extension、Domi host 和用户数据是显式 external dependency，不得伪装成 payload 内组件。
4. `ReleaseManifestV1` 是 artifact 内 immutable signed contract；archive digest/build provenance/platform signature/notarization 是引用 manifest 的外部 immutable attestations。这个分层避免 manifest 自己包含自己的 archive hash。
5. 安装使用 inactive A/B slot、独立 data root、atomic active pointer 和 explicit previous pointer。Binary rollback 只切 release slot；不得静默回滚 SQLite、sidecar journal、result spool、diagnostics、Chrome profile 或 profile/account binding。
6. Schema migration 只能在完整 database-group backup、integrity、drain 和 activation write barrier 后进行。首次启动验证期间不接受用户 operation；失败时才允许无歧义地恢复 pre-migration backup 并切回 previous slot。
7. 生产 extension 通过 Chrome Web Store 或企业批准的同等生产渠道独立发布。默认 sequence 是先扩展 compatibility window、再发布 desktop artifact；CWS extension 不能随 desktop binary rollback 降级。
8. Exact artifact identity 必须从 native build provenance 贯穿 manifest、installer verification、installed copy、`MachineCapabilityReceipt`、`StartupReceipt`、`OperationEvidence`、support bundle、controlled real-site canary、opt-in real-user canary 和 platform release verdict。
9. `supported/released/unsupported/not_shipped` 是带 evidence 的逐平台声明，不是 roadmap 或愿望。没有 final signed exact artifact 和完整 required evidence 时只能 `not_shipped` 或 `unsupported`。
10. 当前 `0.7.49` 的三个目标平台都没有满足本契约的 exact artifact evidence；当前裁决是 `INTERNAL_ONLY`，三平台均为 `not_shipped`。本文不执行发布、真实 canary、CWS 上传、证书采购或密钥操作。

明确不采用以下捷径：

- 不把 PyPI wheel 或 wheel + 用户机 Domi runtime 当作完整产品；
- 不把当前 macOS Intel 手工 workflow、load-unpacked extension 或旧 constraints 结果当作 0.7.49 PASS；
- 不因一个平台通过而声明另一个平台受支持；
- 不把 synthetic fixture、typed failure 或开发机成功称为 real-site canary PASS；
- 不在没有 ADR 与实现证据时强制 PyInstaller、Native Messaging、DBOS、WAL 或 dedicated profile；
- 不等待不可获得的五位历史用户日志，也不伪造 release evidence。

### 1.1 Contract vocabulary

| 术语 | 本文唯一含义 | 不得混称为 |
|---|---|---|
| Product Artifact | 一个 target tuple 的 immutable、self-contained、signed install payload 与其 platform delivery wrapper | Python wheel、source checkout、Domi bootstrap、跨平台产品包 |
| Product Release Unit | Product Artifact、兼容的 production extension publication、逐平台 evidence set 与 signed verdict 的组合 | 单个 archive、CWS extension bytes、release notes |
| Release Manifest | artifact 内签名且不可变的组件、文件、compatibility、storage 与 installer contract | build log、support bundle、动态 capability receipt |
| Artifact Attestation | 对最终 archive/delivery wrapper 身份及 build/signing provenance 的外部 immutable statement | Release Manifest 自身、checksum 文本、平台支持声明 |
| Machine Capability Receipt | #322 owner 在一台实际机器上对 manifest、OS、Chrome、extension、Domi、SQLite、slot 与 schema 组合的观测结果 | Release Manifest、release verdict、roadmap support claim |
| Platform Release Verdict | release authority 对一个 exact artifact、一个 target tuple 与完整 required evidence refs 签名后的发布裁决 | 单项测试 PASS、跨平台总裁决、issue/PR 状态 |
| Profile binding generation | `CONTEXT.md` 定义的 Chrome profile、production extension instance 与 provider account binding 版本 | release slot、runtime attempt、browser control scope |

本文复用 `CONTEXT.md` 的 browser control scope、browser control fence、source control lane、owned tab 与 profile binding generation；发布层只能记录这些权威的 actual identity，不能创建同义术语或重定义其 lifecycle。

## 2. Ownership 与 authority

| 对象或动作 | 唯一 owner | 其他组件可以做什么 | 禁止 |
|---|---|---|---|
| Release Manifest schema、artifact closure、platform declaration、release verdict | SeekTalent release engineering / #326 | build jobs 生成 facts；installer/main 验证并引用 | installer 或运行时自行修改 manifest/支持声明 |
| Source tree、dependency lock 与 native build provenance | build pipeline | release reviewer 验证 attestation | 从用户机 PATH/cache 补齐组件后仍称 exact artifact |
| Platform code signing/notarization trust | release signing authority + OS trust store | pipeline 请求签名；installer 验证 | 私钥进入 artifact、repo、log、support bundle 或 test fixture |
| Install root、A/B slot、active/previous pointer | SeekTalent installer/updater | Domi 请求 install/launch/stop | Domi、sidecar 或 UI 直接切 slot |
| Product data、schema migration backup/restore | main-owned migration coordinator | installer 提供 activation transaction 与空间 preflight | binary rollback 静默覆盖 data/schema |
| Run、drain、safe boundary、retry/outcome | main `runtime_control` / #324 | updater 请求 drain 并读取 durable drain result | release layer从 process exit 或 diagnostics推导 run outcome |
| Sidecar/WTSCLI/browser lifecycle | #323 sidecar ownership | installer安装 exact binaries；updater等待 drain | installer接管 unknown daemon/browser process |
| Sidecar wire/journal/result spool | #325 | manifest声明 schema/SQLite compatibility；updater只迁移已批准格式 | release layer修改 operation disposition/retry |
| Receipt、Failure Envelope、Operation Evidence、support bundle | main diagnostics service / #322 | manifest/verdict只引用；release aggregator校验 refs | #326 另造 receipt/evidence schema |
| Chrome profile/login | 用户/Chrome；产品只拥有 binding control lock | sidecar确定性绑定并控制 owned tabs | installer备份、复制、清空或回滚用户现有 profile |
| Production extension artifact/distribution | extension release owner + CWS/企业渠道 | desktop manifest声明 compatibility window；receipt记录 actual | load-unpacked 作为 production；desktop假设 extension 可降级 |
| Domi host | Domi product owner | 启动/协作停止 main，提供已声明 host capability | 读取 SeekTalent raw DB/journal/log或充当未验证 runtime dependency resolver |

Release authority 只回答“这个 artifact 能否在这个平台发布”。它不能重定义 #322 的 evidence、#323 的 lifecycle、#324 的 task semantics 或 #325 的 wire behavior。

## 3. Current code truth

以下表格记录本文起草时的 mainline 事实，不是对目标状态的实现声明。

| 区域 | 当前 code truth | 影响 |
|---|---|---|
| Product version | `pyproject.toml` 与 `src/seektalent/version.py` 都是 `0.7.49`；Domi shell/PowerShell installer 默认也写死 `0.7.49` | 版本面目前同步，但没有单一生成的 product build identity |
| PyPI release | `.github/workflows/publish-pypi.yml` 在 GitHub release published 后构建 Workbench 与 Python sdist/wheel，并只发布到 PyPI | wheel 是 component artifact，不是 product artifact 或逐平台 release evidence |
| Online Domi install | `install-seektalent-domi.sh/.ps1` 在用户机对 `seektalent==0.7.49` 执行 pip online install，复用可变 Domi Python/Node，再生成 shim | 没有 offline closure、component hashes、签名、bridge/extension install、A/B 或 exact runtime receipt |
| Intel builder | `build_offline_macos_intel.py` 只允许 native macOS x86_64 + Python 3.13；下载 pip.pyz/wheels，打包 pinned WTSCLI fork与extension tree，并生成 SHA-256 | hash/tree/native-wheel验证可保留，但 builder产物未签名/公证且只覆盖一个平台 |
| Intel reproducibility | builder必须读取 `constraints-<version>-macos-intel.txt`；仓库只有 0.7.46/0.7.47，没有 0.7.49 | 当前 main 无法从 checked-in inputs 构建 0.7.49 Intel bundle |
| Intel workflow | `.github/workflows/build-macos-intel-offline.yml` 是 manual `workflow_dispatch`，在 `macos-15-intel` 构建、校验、离线 smoke、上传 14 天 artifact | workflow存在不等于 release train；没有签名、公证、clean-machine、升级/回滚或真实 canary |
| Offline manifest | `bundle-manifest.json` 仅有 schema integer、platform、Python/SeekTalent/WTSCLI/extension version、extension hash与bridge refs | 它不是本文 Release Manifest：没有完整 file/component closure、signatures、schema/protocol/Chrome/installer/evidence contract |
| Offline installer | 安装 Python prefix，stage WTSCLI/extension/bridge 后以 rename 切换三者；失败时局部恢复 backup | staging/verify/rename可演进，但 Python prefix先被删除，组件不是一个原子 product slot，成功后立即删除 previous backup |
| Production extension | offline README/installer要求 Chrome developer mode + Load unpacked；extension source/manifest和CWS release workflow不在本 repo | 当前没有可审计的 production extension ID/package/CWS compatibility evidence |
| Bridge validation | `browser_bridge_manifest.py`、builder、installer、launcher和daemon transport验证 implementation/build/protocol/capabilities；builder验证extension完整文件树 | 保留并纳入 component/receipt gate，不推倒重来 |
| Runtime identity | `opencli_launcher.py`要求离线 WTSCLI目录与bridge manifest，验证 package/bridge identity；verification stamp使用 path/mtime/size | startup pairing基础可保留；Release Manifest verifier必须增加content digest/signature和installed-copy identity |
| Current daemon/profile | daemon仍使用固定 `127.0.0.1:19825` 与 `X-OpenCLI: 1`；site adapter写死 `local-chrome-profile` | #323/#325 T3前不能称 production exact topology；release preflight必须识别并拒绝 unknown owner/mismatch |
| Update/uninstall | `seektalent update` 只打印 pip/pipx命令；没有完整产品 updater/uninstaller | 没有 drain、slot switch、whole-product rollback或data-preserving uninstall |
| SQLite migration | 多个 store会在迁移前用 SQLite backup API备份并运行 integrity check；新 schema会 fail closed | 是迁移基础，但没有与 product slot、activation write barrier、group restore和release verdict闭环 |
| Database-group backup | `backup_group.py` 枚举多个 product DB、生成 group manifest并验证每个 backup | 可演进为 upgrade backup；当前 manifest含绝对 path且没有 restore/slot/schema compatibility protocol |
| Operator health | `operator_health.py` 检查磁盘、DB大小、WAL/SHM、schema版本和 integrity | 可作为 #322 Machine Capability input；当前不检查 manifest/signature/slot/sidecar/Chrome/profile/extension |
| SQLite modes | 部分现有业务 store/mirror使用 WAL；#325 target sidecar command journal尚未实现并明确要求 `DELETE + FULL` | 不对现有 DB做全仓journal-mode重构；v1 sidecar保持rollback journal，WAL迁移仍需固定 build + 三平台 matrix + ADR |
| Signing | repo/workflows没有 macOS codesign/notarization、Windows Authenticode/installer signing 或 Release Manifest signing配置 | 三个平台都没有 production signing evidence；不得用 checksum替代签名 |
| CI | governance、workbench-contract、Intel build是 manual；`python-quality.yml` pull_request paths不含 `docs/**` | 本 docs-only diff只能报告本地 gate；不能称远程 automatic CI 已通过 |

当前逐平台声明冻结为：

| Platform tuple | Current declaration | 直接原因 |
|---|---|---|
| `windows-11-x86_64` | `not_shipped` | 无完整 native artifact、installer signature、clean-machine、upgrade/rollback和real canary evidence |
| `macos-x86_64` | `not_shipped` | 只有不完整manual builder；0.7.49缺constraints且无codesign/notarization/release evidence |
| `macos-arm64` | `not_shipped` | 无对等native builder/artifact/signing/clean-machine evidence |

`not_shipped` 不是“可能能用”或“内部测试通过”的同义词；preflight、release notes和支持答复都必须保持这个状态。

## 4. Product artifact 与依赖闭包

本文区分两个层级：**Product Artifact** 是一个平台安装的self-contained binary payload；**Product Release Unit** 是该artifact、兼容的production CWS extension publication、逐平台evidence和signed verdict的组合。Extension bytes不因是release unit的一部分就允许被desktop sideload；完整性来自manifest compatibility ref、CWS identity/publication和actual receipt。

### 4.1 v1 packaging boundary

每个平台 artifact 由两层组成：

1. **Platform delivery wrapper**：Windows签名installer；macOS签名并公证的installer/app delivery wrapper。它只负责验证、preflight、stage、drain、activate、rollback和uninstall。
2. **Immutable onedir release payload**：安装后在inactive slot中保持原始相对布局与hash，不在用户机解析pip/npm依赖，不从PATH选择可变runtime。

允许受控 Domi integration，但满足条件是：Domi只作为host launcher；SeekTalent payload包含exact Python/SQLite/Node runtime，或在构建/安装阶段把manifest声明且hash验证的Domi runtime复制进该slot成为immutable component。仅验证version string、复用Domi当前PATH或在线pip安装不满足闭包。

本文不强制 PyInstaller、MSIX、MSI、PKG、DMG 或某个 updater framework。实现PR必须通过短ADR选择平台wrapper/tool，并证明下面同一布局、签名、rollback和evidence contract；工具本身不是发布证据。

### 4.2 Artifact root closure

一个 product payload 至少包含以下 component IDs：

| Component ID | Artifact root内 | 依赖 |
|---|---|---|
| `main_application` | 必须 | `python_runtime`、`sqlite_runtime`、`workbench_assets`、`sidecar` protocol compatibility |
| `liepin_execution_sidecar` | 必须 | `python_runtime`或自己的exact runtime、`node_runtime`、`wtscli_runtime`、`browser_bridge` |
| `python_runtime` | 必须 | 平台native stdlib与全部locked wheels；不得用户机解析 |
| `sqlite_runtime` | 必须明确身份 | actual linked/embedded SQLite build；main与sidecar各自actual build都要声明，若相同可引用同一component |
| `node_runtime` | 必须 | exact executable与runtime files；不得从PATH补齐 |
| `wtscli_runtime` | 必须 | exact fork commit/package/build/files；由sidecar唯一启动 |
| `browser_bridge` | 必须 | exact implementation/build/protocol/capabilities；与WTSCLI/extension compatibility绑定 |
| `workbench_assets` | 必须 | packaged frontend file tree/hash |
| `installer_updater_support` | 必须 | manifest verifier、preflight、slot/migration/rollback/uninstall helpers |
| `licenses_sbom` | 必须 | component license inventory与machine-readable SBOM ref |

以下是 external dependency，必须声明但不得算作payload closure的一部分：

- Chrome Stable binary与OS policy；
- 用户现有 Chrome profile/login；
- production CWS extension actual installed version；
- Domi host build/channel（若产品从Domi启动）；
- LLM/provider/network endpoints；
- product data、sidecar journal、result spool、diagnostics与schema backups。

Artifact root是闭包当且仅当：manifest中每个required component都存在，所有regular files都被一个component file tree覆盖，manifest/signature/attestation metadata位于closed reserved paths，任何未声明可执行文件、symlink escape、duplicate path、case-fold collision、alternate data stream或用户机dependency resolution都使构建/安装fail closed。

### 4.3 Installed layout

逻辑布局对三个目标平台相同；平台adapter只决定根路径与pointer原子操作：

```text
INSTALL_ROOT/
  control/
    installation-id
    active-slot.json
    previous-slot.json
    activation-journal.jsonl
    install.lock
  slots/
    A/
      release/
        release-manifest.json
        signatures/release-manifest.sig
        attestations/build-provenance.ref
        bin/main
        bin/sidecar
        runtimes/python/
        runtimes/node/
        runtimes/wtscli/
        bridge/
        workbench/
        installer-support/
        licenses/
    B/
      release/...

DATA_ROOT/
  databases/
  sidecar/
    command-journal.sqlite3
    result-spool/
  diagnostics/
  receipts/
  backups/
    schema/<activation-id>/
  bindings/
    profile-binding.json
  authorities/
  support-bundles/
  data.lock

PROFILE_ROOT (external)
  existing Chrome Stable profile owned by user/Chrome
```

默认per-user roots：

| Platform | `INSTALL_ROOT` | `DATA_ROOT` |
|---|---|---|
| Windows 11 x64 | `%LOCALAPPDATA%\SeekTalent\Product` | `%LOCALAPPDATA%\SeekTalent\Data` |
| macOS x86_64/arm64 | `~/Library/Application Support/SeekTalent/Product` | `~/Library/Application Support/SeekTalent/Data` |

若平台wrapper必须把signed `.app` 放入 `~/Applications` 或 `/Applications`，`.app`只是slot payload的platform projection；slot pointer、installed manifest copy和data仍遵守上述逻辑分离。System-wide install需要独立ADR与权限/ACL matrix，不得静默把per-user测试外推到system-wide支持。

Slot规则：

- `A/B` 是固定slot identity；每个slot最多一个完整release payload，不按version散落可变子目录。
- `active-slot.json` 和 `previous-slot.json` 包含slot、`product_build_id`、manifest digest、pointer generation和committed timestamp；使用same-directory temp + fsync + atomic replace。
- Slot内release在验证后immutable；任何运行时写入slot都使startup integrity失败。
- 同一时间只有active slot可以获得normal product lifecycle lock。Inactive slot只允许updater在exclusive install/data lock、old generation已drained且user acceptance关闭时启动一个有界activation-verification generation；它不得接受用户operation、建立第二browser controller或越过验证所需的声明边界。
- Previous slot在新release完成activation commit前不得删除；commit后至少保留到release policy声明的rollback window结束。
- Slot cleanup只删除inactive binary payload，不删除`DATA_ROOT`或`PROFILE_ROOT`。

### 4.4 Data、profile 与 authority rotation

- Main databases、sidecar rollback journal/result spool、diagnostics、receipts和backup属于`DATA_ROOT`，跨binary slot共享，但每个schema都有manifest声明的reader/writer compatibility。
- v1默认继续使用用户现有Chrome Stable profile compatibility mode。installer/updater不得复制cookie、清空profile、迁移login或把profile放进slot。
- `profile-binding.json`只保存opaque profile/extension/account refs与`profile_binding_generation`；profile/account/extension identity变化由#323规则产生新generation。
- Dedicated profile只允许spike root `DATA_ROOT/spikes/dedicated-profile/<spike-id>`；没有独立产品决议前不得创建production `PROFILE_ROOT`或改写默认布局。
- 每次main/sidecar generation、slot activation和rollback都生成新transport session/nonces；不得复用旧pipe/session token。
- Slot switch会撤销旧sidecar/browser controller authority并由新generation重新activation。`control_key + browser_control_fence_token`永不从previous slot复制。
- `runtime_attempt_fence_token`仍由#324 main control plane拥有；updater只能请求durable drain/无active fence证明，不能自行生成或删除run authority。
- Installation-local trust/authority keys位于`DATA_ROOT/authorities`，使用OS user ACL/key store；rotation记录key ID/ref，不在manifest、argv、log、journal或support bundle放raw secret。

## 5. Release Manifest v1

### 5.1 Encoding、identity 与 canonical digest

Schema ID：`seektalent.release-manifest/v1`。

- 文件名固定`release-manifest.json`，UTF-8无BOM，JSON object，duplicate keys/NaN/Infinity/unknown top-level fields拒绝。
- Canonical bytes使用RFC 8785 JCS。`release_manifest_sha256 = SHA-256(JCS(manifest))`，不存回manifest本体。
- `release_series_id`关联同一产品版本/源提交的跨平台候选，不产生同步发布承诺。
- `product_build_id`逐platform artifact唯一，格式`st1-<32 lowercase hex>`，由下面canonical build identity的SHA-256前128 bits生成：

```text
product version
source revision
target OS/arch
build recipe digest
dependency lock/constraints digest set
ordered component build identities
```

- `manifest_id`是build pipeline生成的opaque ID；identity冲突时以canonical digest和`product_build_id`为准，禁止同ID不同内容。
- `payload_tree_sha256`覆盖slot `release/`内除`release-manifest.json`、`signatures/`和`attestations/`外的全部paths。外层archive/installer digest由Artifact Attestation记录，避免自引用。

### 5.2 Top-level field schema

| 字段 | 类型/约束 | 语义 |
|---|---|---|
| `schema_version` | exact string | `seektalent.release-manifest/v1` |
| `manifest_id` | opaque 1..96 | immutable manifest identity |
| `release_series_id` | opaque 1..96 | 跨平台关联，不代表simultaneous train |
| `product_name` | exact `SeekTalent` | 防止artifact混用 |
| `product_version` | normalized version 1..64 | 当前版本如`0.7.49` |
| `product_build_id` | `st1-` + 32 lowercase hex | platform-specific exact build identity |
| `source_revision` | full 40-char Git SHA | tracked source identity；dirty source build禁止release channel |
| `source_tree_digest` | SHA-256 | repo archive/tree policy定义的canonical source digest |
| `build_recipe` | object | `recipe_id/revision/digest/runner_image_ref/toolchain_refs` |
| `dependency_inputs` | non-empty list | lock/constraints name、SHA-256、platform scope；全部resolved inputs必须覆盖 |
| `target` | object | closed `os/arch/min_os_build/max_os_build`; v1 only three tuples |
| `channel` | enum | `internal/candidate/production`；channel不等于release verdict |
| `created_at` | UTC RFC3339 | build time，只作审计，不参与runtime authority |
| `payload_root` | exact `release` | installed slot相对root |
| `payload_tree_sha256` | 64 lowercase hex | reserved metadata外完整tree digest |
| `components` | 1..32 `ComponentV1` | 完整root/component closure |
| `external_dependencies` | object | Chrome/Domi/CWS/profile/network closed declarations |
| `compatibility` | object | wire/schema/runtime/Chrome/extension/upgrade windows |
| `storage_contract` | object | data roots、schema ranges、sidecar journal mode、backup policy |
| `installer_contract` | object | installer/updater/uninstaller component refs与supported operations |
| `evidence_policy` | object | #322 schema refs、matrix revision、required evidence classes |
| `build_evidence_refs` | sorted list，1..32 | 只允许build/component/SBOM/secret-scan等在manifest冻结前已存在的immutable evidence refs；install/runtime/release evidence放Platform Verdict以避免自引用 |
| `signing_policy` | object | required signer IDs/algorithms/platform verification kinds；无private material |
| `sbom_ref` | `FileRefV1` | machine-readable SBOM file ref |
| `license_inventory_ref` | `FileRefV1` | license inventory ref |

`channel=production`只表示artifact按production signing/evidence policy构建；没有Platform Release Verdict PASS仍不能公开发布或声明`released`。

### 5.3 `ComponentV1`

每个component固定字段：

| 字段 | 约束 |
|---|---|
| `component_id` | §4.2 closed ID；同manifest唯一 |
| `component_kind` | `application/sidecar/runtime/browser_engine/bridge/assets/installer_support/metadata` |
| `version` | bounded semantic/build version |
| `build_id` | exact component build identity；不得只有marketing version |
| `source_ref` | repo + full commit或verified upstream artifact ref |
| `root_path` | normalized relative path；不得absolute/`..`/symlink escape |
| `entrypoints` | 0..8 normalized relative files |
| `files` | sorted non-empty `FileRefV1`，或一个signed nested tree manifest ref；必须覆盖root所有files |
| `tree_sha256` | sorted `sha256  path\n` tree digest |
| `size_bytes` | exact non-negative total |
| `platform` | exact target tuple或`platform_independent` |
| `dependencies` | sorted component IDs；无环且transitive closure完整 |
| `protocols` | closed protocol/build/capability facts；不复制#325 DTO |
| `code_signature_ref` | nullable platform signature attestation ref；required executable上不可空 |
| `build_provenance_ref` | immutable attestation ref |

`FileRefV1`字段固定为`path/size_bytes/sha256/mode_class/executable`。`mode_class`只允许`regular_readonly/regular_executable`；v1 payload不允许symlink、device、FIFO、socket或hardlink alias。Windows还拒绝NTFS alternate data streams与case-insensitive duplicate；macOS同时检查Unicode normalization/case-fold collision。

Dependency graph至少满足：

```text
main_application -> python_runtime, sqlite_runtime, workbench_assets,
                    liepin_execution_sidecar
liepin_execution_sidecar -> python_runtime|declared_private_runtime,
                             sqlite_runtime, node_runtime,
                             wtscli_runtime, browser_bridge
wtscli_runtime -> node_runtime, browser_bridge
installer_updater_support -> all required payload components
```

一个component引用manifest外的executable、dynamic library、Python package或Node package时，只有OS baseline allowlist中的系统库可例外；每个平台必须有dependency scan golden。PATH、site-packages、global npm、Homebrew、用户Domi cache和另一个OpenCLI install不在allowlist。

### 5.4 External dependency schema

`external_dependencies`固定包含：

- `chrome_stable`：channel exact `stable`、tested min/max full version、allowed OS policy posture、actual version由Machine Capability Receipt记录；Beta/Dev/Canary/Edge拒绝；
- `chrome_profile`：v1 exact `existing_profile_compatibility`，required deterministic binding fields与residual-risk policy ref；
- `production_extension`：distribution=`chrome_web_store|enterprise_managed`、exact extension ID、store item ref、protocol major/minor window、required capabilities、minimum/maximum accepted extension version/build window、current/previous compatibility matrix ref；
- `domi_host`：`required|optional`、tested version/build window与launch contract；它不声明可变Python/Node dependency；
- `network_postures`：direct与明确validated proxy/CA posture IDs；未列组合是unverified/unsupported；
- `provider`：source=`liepin`与real-canary policy ref；不含账号或业务正文。

Manifest声明**兼容窗口**，installed receipt记录**实际值**。实际extension/Chrome/Domi落在窗口内不等于artifact自动releaseable；仍要有该exact artifact的platform evidence。

### 5.5 Compatibility schema

`compatibility`至少包含：

- main↔sidecar exact `product_build_id`、Source Port protocol major/minor range、六种required operation contract IDs；
- sidecar↔WTSCLI exact build/fork/file tree、bridge implementation/build/protocol/capabilities；
- #322 diagnostic event/Failure Envelope/receipt/Operation Evidence schema major set；
- #324 runtime-control schema min/max readable、min/max writable、migration plan ID；
- 每个product database logical name的`reader_min/reader_max/writer_target`；
- sidecar journal schema readable/writable range、actual SQLite component ref、`journal_mode=DELETE`、`synchronous=FULL`；
- result spool schema与retention compatibility；
- previous product builds allowed as N-1 upgrade sources；
- binary rollback compatibility：`reads_current_schema_without_restore|requires_activation_backup_restore|manual_recovery_only`；
- Chrome Stable、extension和Domi windows的exact refs。

Compatibility range必须闭合且可计算。Unknown schema/protocol/capability、major mismatch、range空交集、schema ahead、missing migration、未声明N-1 source或previous binary不能读当前data时均fail closed；不得猜测minor字段或自动选择另一个profile/runtime。

### 5.6 Storage 与 installer contract

`storage_contract`字段：

- logical `install_root/data_root/profile_mode`；
- A/B pointer schema与minimum atomic-filesystem capability；
- database list、schema compatibility、backup group schema、minimum free-space formula；
- sidecar journal/result spool位置、rollback mode/sync/retention；
- profile binding schema/generation policy；
- authority rotation policy ID；
- uninstall default=`preserve_user_data_and_profile`；
- purge policy需要explicit user confirmation与optional final backup。

`installer_contract`字段：

- exact installer/updater/uninstaller build IDs与file refs；
- exact `installer_version/updater_version/uninstaller_version`；
- supported actions=`clean_install/preflight/stage/drain/activate/upgrade/rollback/uninstall/repair`的closed subset；
- supported source versions与minimum installer version；
- signature/notarization requirements；
- expected installed manifest path、pointer schema、activation journal schema；
- required preflight IDs与typed reject registry；
- privilege posture=`per_user_non_admin` for v1 default。

缺少`rollback`、`uninstall`或schema-aware activation helper的wrapper不能声明production完整artifact。

### 5.7 Signing 与 attestation

签名对象分层：

| 对象 | Required proof |
|---|---|
| Release Manifest | detached signature over exact JCS bytes；signer key ID/trust-policy ID；installer内置/OS key store拥有trusted public root |
| Payload executables/libraries | Windows Authenticode或macOS hardened-runtime code signature，按平台policy递归验证 |
| macOS delivery wrapper | Apple notarization ticket/staple与Gatekeeper assessment evidence |
| Windows delivery wrapper | trusted Authenticode chain、timestamp与installer identity evidence |
| Payload tree | manifest file/tree SHA-256 closure |
| Final archive/installer bytes | `ArtifactAttestationV1`记录artifact SHA-256、size、manifest digest、platform signatures/notarization refs |
| Build provenance | immutable native runner/toolchain/source/dependency/component provenance；不含secret |
| Platform Release Verdict | release authority签名，引用artifact attestation和全部required evidence refs |

Checksum用于integrity和identity，不替代signature/authenticity。Manifest signer、platform code signer、CWS signer可不同，但policy必须列出各自role与trusted key ID。私钥操作只在批准的signing service/hardware boundary发生；本repo、artifact、CI log、test fixtures和support bundle只保存public cert/key ID与attestation ref。

### 5.8 Installed copy 与拒绝规则

Installer必须把原始signed manifest、detached signature和artifact attestation ref原样复制进slot。Machine Capability Receipt引用installed manifest digest；不得从已安装文件重新生成一个“等价manifest”。

以下任一情况在**写inactive slot前**拒绝：

- target OS/arch/channel不匹配；manifest/schema/signature/archive digest无效；
- component/file closure不完整、有extra executable/symlink/path collision；
- installer版本、权限、磁盘、filesystem atomic replace能力不满足；
- source version不在upgrade window；active/previous/control pointer损坏且无法无歧义repair；
- required backup空间不足或data lock无法取得；
- unsupported Chrome/extension/Domi posture无法通过声明的install-only remediation解决。

以下任一情况在**activation前**拒绝并保留current slot：

- staged files/content/platform signature验证不一致；
- component/protocol/schema compatibility无交集；
- database-group backup或integrity失败；
- active run/sidecar无法在deadline内drain到#324 safe boundary；
- migration dry-run/plan/hash不一致；
- preflight无法证明unknown daemon/endpoint不会被接管；
- production extension identity/capability不在兼容窗口。

以下任一情况在**startup/runtime** fail closed：

- installed manifest copy与active pointer/actual files不一致；
- main/sidecar `product_build_id`或executable hash不同；
- PATH/global runtime替代manifest component；
- schema ahead、sidecar journal mode不是`DELETE + FULL`、rollback mismatch；
- stale slot、stale profile generation或旧browser controller试图恢复authority；
- actual Chrome/extension/profile/account binding超出manifest/receipt范围。

拒绝必须产生#322-owned typed Failure Envelope/receipt fact；Manifest本身不包含retry permission或ProductOutcome。

## 6. Platform declaration 与禁止误报规则

### 6.1 `PlatformReleaseDeclarationV1`

逐平台声明是Platform Release Verdict中的closed enum：

| State | 含义 | Required evidence |
|---|---|---|
| `unsupported` | v1明确不接受该tuple或已知硬能力不满足 | typed capability/product decision；preflight稳定拒绝 |
| `not_shipped` | roadmap目标或可构建，但该release没有完整published artifact/evidence | 缺口列表；不得提供production下载/支持话术 |
| `supported` | final signed exact artifact通过技术release gates，可供受控canary分发/支持，但尚未完成real-user canary与general production publication | signed artifact attestation + technical PASS verdict；controlled canary channel ref可在分发时追加到新verdict revision，仍不得称广泛已发布 |
| `released` | `supported`基础上，逐平台opt-in real-user canary通过，production artifact和required extension sequencing完成general publication | real-user canary evidence、general publication receipt、same artifact digest、CWS phase状态 |

状态只能由带签名的immutable verdict revision前进。撤回不改写旧verdict，而发布新revision：`released -> unsupported`只在安全撤回/产品决议时允许，并必须提供typed reason和user remediation；普通缺证据使用`not_shipped`，不冒充技术unsupported。

### 6.2 独立平台规则

- Windows x64 PASS不能证明macOS；macOS Intel PASS不能证明arm64；universal2理论兼容也必须拆成两个native install/run/evidence rows。
- 每个平台有自己的`product_build_id`、artifact digest、signing chain、native runtime/wheel closure、OS/Chrome matrix、clean-machine evidence和real canary。
- 未产出final artifact、证据为`NOT_RUN/INCOMPLETE`、用了另一平台artifact、用了开发机cache或manifest digest不一致时，该平台不得超过`not_shipped`。
- 默认release series的一个平台FAIL只阻塞该平台。只有signed release plan显式`simultaneous_train=true`并列出承诺platform set时，一个平台FAIL才阻塞整个train。
- 支持文案必须列exact state与artifact version，不得使用“macOS supported”掩盖仅Intel或仅arm64，也不得用“Windows supported”掩盖Windows 10/ARM未验证。

### 6.3 Release verdict

Schema ID：`seektalent.platform-release-verdict/v1`。至少包含：

- verdict ID/revision、release series、platform tuple、product build ID；
- Release Manifest digest、Artifact Attestation digest与production channel ref；
- build/sign/notarization/install/upgrade/rollback/uninstall/CWS/evidence matrix refs；
- PR/nightly/clean-machine/fault/controlled-real-site-canary/real-user-canary/dedicated-profile-spike verdict；
- unknown failure count、privacy violation count、open blocker IDs；
- declaration、`INTERNAL_ONLY|RELEASEABLE|BLOCKED|WITHDRAWN`、typed reasons；
- `simultaneous_train`和承诺platform set；
- signer/ref与created time。

任何required gate不是PASS、unknown user-visible taxonomy非零、privacy/authority/user-tab/durable-job blocker非零或open release blocker非空时，只能`BLOCKED/INTERNAL_ONLY`。

## 7. Build、sign、install、upgrade、rollback 与 uninstall protocol

### 7.1 Build and signing order

每个平台在native clean runner独立执行：

1. Freeze source：full Git SHA、clean tree、release series、platform tuple和build recipe。
2. Resolve build inputs：读取checked-in lock/constraints，下载到isolated cache并按hash验证；生成dependency input list。用户机不参与resolution。
3. Build Workbench、Python/main、sidecar、Node/WTSCLI、bridge和installer support；记录exact toolchain与component provenance。
4. Assemble immutable onedir root；运行platform dependency scan、file closure、SBOM/license与secret scan。
5. 生成unsigned Release Manifest canonical bytes；校验component graph、schema/protocol/Chrome/extension/upgrade windows。
6. 对payload executables/libraries执行platform code signing；macOS完成hardened runtime。签名会改变文件bytes时，必须在签名后重新计算file/tree digests并生成final manifest。
7. 对final manifest JCS bytes执行detached release signature；从此manifest不可变。
8. 构建platform delivery wrapper；Windows签名/timestamp，macOS签名、公证并staple。
9. 计算final archive/installer SHA-256/size，生成immutable Artifact Attestation与build provenance ref。
10. 用final bytes在隔离machine运行build-level install smoke；任何repack/resign/recompress都会产生新的artifact digest并使旧evidence失效。

Release evidence必须引用步骤9的exact digest。步骤10之后修改任何payload、manifest、signature、wrapper或metadata都要求从受影响步骤重新build和取证。

### 7.2 Install and preflight

Clean install顺序：

1. 验证wrapper platform signature/notarization和Artifact Attestation；
2. 验证Release Manifest signature、target、installer compatibility和payload closure；
3. 创建/锁定`INSTALL_ROOT/control`与独立`DATA_ROOT`，生成installation ID；
4. 检查per-user权限、atomic replace、磁盘、OS/arch、Chrome/Domi/extension可判定事实；
5. 解包到inactive slot临时目录，逐file/tree/code-signature验证后atomic rename为inactive `release/`；
6. 由main diagnostics service签发preflight `MachineCapabilityReceipt`，installer只提供artifact/install facts；
7. 若是首次安装，初始化empty data schemas；不运行真实Liepin operation；
8. 以activation verification mode启动inactive main/sidecar，验证installed manifest、component pairing、DB/schema、sidecar journal、Chrome/extension capability；
9. 停止verification generation，rotate authority，原子提交active pointer；没有previous slot；
10. 正常首次启动，由main签发StartupReceipt。用户login/action按#323/#324进入明确流程。

Preflight不得自动kill unknown process、删除DB/profile、关闭TLS验证、切换browser/profile或执行pip/npm fallback。

### 7.3 N-1 upgrade and atomic activation

Upgrade必须按以下顺序：

1. 验证new artifact与current installed manifest，确认current build在declared upgrade sources内；
2. Stage/verify new payload到inactive slot，不触碰active slot/data；
3. 阻止新run/source operation acceptance，向main请求durable drain；
4. Main按#324停止claim新operation，active operation到registered safe boundary；sidecar按#325 drain并FULL-sync journal/result facts；
5. 获取无active runtime fence、sidecar drained和唯一product/data lock的durable refs；超时则中止upgrade，active slot继续服务；
6. 对所有product DB执行operator health/integrity和database-group backup，写activation ID、source/target schema、source manifest/build与backup hashes；任一失败中止；
7. 启用activation write barrier：除migration coordinator/diagnostics最小事实外，不接受用户写入；
8. 使用new slot的declared migrator按ordered plan迁移data与sidecar journal；每步expected schema/CAS，失败恢复backup；
9. 以new slot activation verification mode启动main/sidecar，验证manifest、schema、receipts、Source Port readiness、extension compatibility和synthetic local smoke；不运行真实账号canary；
10. verification失败：停止new generation、恢复pre-migration backup、验证integrity、rotate authorities、保留active pointer在old slot；记录rollback evidence；
11. verification成功：停止verification generation，写`previous-slot.json=old`，atomic replace`active-slot.json=new`并递增pointer generation，fsync control dir；
12. 以active generation启动new slot，重新签发Machine Capability/Startup Receipt；释放write barrier并恢复acceptance；
13. 在rollback window内保留old slot和activation backup，直到policy与evidence允许清理。

在pointer commit前，old slot仍是唯一active product；在commit后，new slot是唯一active product。不得同时启动两个sidecar/browser controller或在同release自动fallback到旧browser path。

### 7.4 Rollback

Rollback有三种closed posture：

| Posture | 条件 | 动作 |
|---|---|---|
| `pointer_only` | previous manifest声明可读写current schema，或upgrade未改变schema | drain current，verify previous，rotate authority，atomic switch pointer；data不回滚 |
| `activation_backup_restore` | activation verification期间尚未接受用户写入，且verified pre-migration group backup完整 | 停止new generation，restore exact group backup，integrity，switch previous，保留failed data snapshot供support |
| `manual_recovery_only` | new release已接受用户写入且previous不能读current schema，或backup/history不完整 | fail closed，保全current data和两个slots；提供export/support action，不自动丢新数据 |

Binary rollback不得：

- 将Chrome profile/binding generation倒回旧值；
- 删除sidecar journal/result spool以让previous启动；
- 让旧binary打开schema-ahead DB；
- 用older extension package覆盖CWS actual extension；
- 要求用户删除整个`.seektalent`/data root。

Rollback后每个main/sidecar/transport/browser control generation都重新mint。Old attempt的late writes继续由#324 fencing拒绝；rollback不是reopen terminal run的权限。

### 7.5 Uninstall

默认uninstall是data-preserving：

1. 停止新acceptance并drain；无法drain时提供取消或安全中止，不kill unknown process；
2. 停止owned main/sidecar/WTSCLI tree并reclaim owned tabs；不关闭用户Chrome/profile/host/user tabs；
3. 删除A/B binary slots、shims、active/previous pointer和installation-local executable authorities；
4. 保留`DATA_ROOT`、schema backups、support bundles、profile和CWS extension；输出明确路径类别与重新安装可恢复说明；
5. 可提示用户在Chrome UI卸载extension，但普通uninstaller不得假装能回滚/强删CWS extension。

`purge user data`是独立、显式、二次确认动作。它必须预览将删除的logical data classes，提供可选final verified backup/export，并永远不删除用户现有Chrome profile。删除material data后要记录本地uninstall receipt；若用户已选择purge，receipt只能保留在用户选择的export位置。

## 8. Chrome Web Store sequencing

### 8.1 Compatibility window

Desktop/sidecar/bridge与production extension必须至少维护一个有证据的current/previous交集：

```text
desktop N     <-> extension E(N), E(N-1) or an explicitly narrower proved window
desktop N-1   <-> extension E(N), E(N-1) during desktop rollback window
```

Exact matrix以protocol major/minor、capabilities、extension ID/build/version和desktop product build IDs表示。理论semver兼容、local unpacked测试或daemon alive不是compatibility evidence。

### 8.2 Default extension-first sequence

当desktop N需要新的extension capability时，默认顺序：

1. Build/sign extension E(N)，证明它对已released desktop N-1向后兼容或安全fail closed；
2. 发布到production CWS listing，记录store item/version/publication receipt；
3. 在controlled profiles验证安装/update、MV3 restart、N-1 desktop compatibility和撤回路径；
4. 等待release plan定义的distribution observation window；不能从“已点击publish”推断所有用户已更新；
5. Build/finalize desktop N manifest，声明E(N)/E(N-1)实际proved window；
6. 对每个平台final artifact + production extension运行clean-machine和real canary；
7. 逐平台发布desktop N并记录publication receipt。

E(N)不能兼容N-1时，不得直接发布。先交付一个bridge extension/desktop版本建立overlap，或阻塞release。

### 8.3 Product-first exception

只有同时满足以下条件才允许desktop-first：

- desktop N所需capabilities已由当前production extension提供；
- exact desktop N × current extension matrix已在每个平台通过；
- new extension只增加optional/backward-compatible能力；
- rollback到desktop N-1仍与CWS current/new extension兼容；
- signed release plan明确记录为什么不使用默认extension-first。

### 8.4 Rollback and withdrawal

- Desktop可切previous slot；CWS extension通常不可由产品降级。
- 若E(N)有问题，extension owner发布更高version的兼容forward fix或执行CWS withdrawal；不能让installer sideload older CRX。
- 在extension问题解决前，只能维持compatibility window内的desktop builds；window外startup fail closed并给出具体support/user action。
- Load unpacked只允许internal/staging，manifest和receipt必须标`distribution=load_unpacked_internal`，任何使用它的case不能进入production release verdict。

## 9. Exact artifact evidence graph

### 9.1 Identity chain

```mermaid
flowchart LR
  S["Source revision + locks + native recipe"] --> B["Component build provenance"]
  B --> M["Signed Release Manifest v1"]
  M --> A["Platform-signed artifact attestation"]
  A --> I["Installed immutable manifest copy"]
  I --> C["MachineCapabilityReceipt"]
  C --> T["StartupReceipt"]
  T --> O["OperationEvidence"]
  O --> G["Gate result set"]
  G --> V["Signed Platform Release Verdict"]
  C --> U["Privacy-safe support bundle"]
  T --> U
  O --> U
  V --> P["Publication receipt / released declaration"]
```

链上的required equality：

| Stage | 必须保持的exact identity |
|---|---|
| Build provenance | source SHA、recipe/runner/toolchain、lock/constraints digests、component source/build IDs |
| Release Manifest | product build ID、target、component file/tree hashes、compatibility/storage/installer policy |
| Artifact Attestation | final wrapper/archive SHA-256/size、manifest digest、platform signature/notarization refs |
| Installed copy | same manifest bytes/digest、same payload tree和component signatures；slot/pointer generation另记machine fact |
| Machine Capability | same manifest/artifact refs + actual OS/Chrome/extension/Domi/SQLite/slot/schema verdict |
| Startup | same capability/manifest refs + actual main/sidecar/WTSCLI/bridge generations/builds |
| Operation Evidence | same capability/startup refs + run/operation/attempt facts；schema仍由#322拥有 |
| Gate result | exact case ID、platform、machine image、artifact digest、extension actual version、receipt/evidence hashes |
| Platform verdict | same artifact/manifest digests和complete required gate refs |
| Support bundle | installed manifest/capability/startup/operation allowlisted refs；不能用bundle生成支持声明 |

任何stage只记录marketing version、路径、branch名、latest tag或“当前安装”而缺exact digest/ref时，identity chain断裂。断裂case必须`INVALID/NOT_RUN`，不能手工认定PASS。

### 9.2 Evidence object rules

- #322仍唯一拥有`MachineCapabilityReceipt/StartupReceipt/OperationEvidence/support bundle` schema。本文只要求它们引用manifest/artifact identity。
- Build、signing、notarization、installer、publication和gate result使用#326-owned immutable attestations；它们不得复制runtime Failure Envelope或ProductOutcome schema。
- 每个evidence object有schema ID、object ID/revision、canonical hash、producer build、created time、artifact/manifest refs和result=`PASS|FAIL|INVALID|NOT_RUN`。
- Evidence不可原地编辑；rerun生成新revision并保留旧FAIL/INVALID。
- Raw CI log、screenshot、console text或human checkbox不是canonical evidence。它们可作为restricted attachment，但verdict只消费structured result与hash。
- 不上传cookie、token、真实JD/简历/query/DOM/screenshot/browser history。Real canary只保存opaque account ref、operation/evidence refs、safe counts、reason codes与allowed outcome。

## 10. Evidence matrix 与 release verdict

### 10.1 Gate classes

| Gate | Artifact requirement | Required proof |
|---|---|---|
| PR deterministic | source/build changes可用fake或component candidate；release schema必须用goldens | Manifest/component graph/path/signature-policy validators、platform declaration logic、privacy、migration/slot state machine、cross-contract assertions |
| Native build | 每个平台native clean runner candidate | locked dependency closure、native dependency scan、SBOM/license、payload tree、platform signatures/notarization request、reproducible inputs |
| Nightly exact-artifact | 当日native candidate，不用developer cache | real process/pipe/SQLite/browser synthetic fault matrix、installed manifest/receipt identity、unknown taxonomy zero |
| Release clean-machine | final signed exact artifact | clean install、N-1 upgrade、crash-mid-stage/migration/switch、rollback、uninstall、Chrome/extension pairing、support export/re-import |
| Controlled real-site canary | same final artifact + production extension + controlled test account | one non-sensitive query真正执行，source-executed evidence与合法success outcome；typed failure不算PASS |
| Opt-in real-user canary | same final artifact的controlled canary publication + production extension | 每个平台至少一个独立真实用户安装，在知情同意下完成真实工作流；只手动导出allowlisted evidence，不自动上传业务正文 |
| Dedicated-profile spike | same release candidate环境；独立spike root | real login/risk/search/long session/policy/distribution/migration UX；记录adopt/reject recommendation，执行证据required但adoption不是required |
| Publication | exact artifact digest已通过verdict | production channel ref、download/install verification、CWS phase/publication receipt、no byte changes |

### 10.2 Per-platform clean-machine cases

每个声明为`supported/released`的平台至少包含：

1. standard user、non-admin、clean HOME/data/install root全新安装；
2. Unicode/space user path、low disk、read-only target、atomic replace不支持的typed preflight；
3. 从当前production N-1升级，DB group backup、migration、activation、first-start验证；
4. installer在download/verify/stage/drain/backup/migration/pointer switch每个边界被kill后的恢复；
5. first-start失败的`activation_backup_restore`，以及已有new writes时`manual_recovery_only`；
6. previous whole-release pointer rollback，不混用main/sidecar/WTSCLI/bridge build；
7. data-preserving uninstall、reinstall恢复与explicit purge negative test；
8. Chrome Stable window、production extension current/previous、MV3 restart、missing/disabled/wrong ID/version；
9. existing/fresh/multiple/locked profile、account change与binding generation；
10. legacy OpenCLI/WTSCLI remnants、unknown endpoint/process owner、double controller拒绝；
11. direct/offline/supported proxy以及unsupported custom CA/managed Chrome的capability classification；
12. actual packaged SQLite build、rollback journal sidecar、lock/full/read-only/corrupt/schema-ahead/backup restore；
13. #322 F01-F32中适用于release的平台fault rows；
14. synthetic canary的durable candidate/main restart evidence；
15. final artifact + production extension + controlled real Liepin canary；
16. doctor、receipt、support bundle preview/export/re-import和forbidden corpus zero leak。

Windows必须额外证明installer/child-tree/Job Object或已选等价机制、ACL与Authenticode；macOS必须分别在x86_64与arm64 native machine证明codesign/notarization/Gatekeeper、process group和native dependency closure。Rosetta结果不能替代任一native row。

### 10.3 Controlled real-site canary PASS

PASS必须同时证明：

- final artifact digest、installed manifest与production extension actual identity一致；
- controlled account/profile binding ready，且不采集credential；
- 一次预先批准的non-sensitive query通过Source Execution Port真实执行；
- operation有durable acceptance、dispatch/observation/main commit证据和允许的ProductOutcome；
- 默认期望`succeeded_with_results`。只有预先设计、结果集合确定为空的canary query才允许`succeeded_empty`；
- main restart后committed candidate/empty coverage与Operation Evidence仍可读取；
- user/host tabs未被控制，support bundle forbidden scan为零。

`needs_attention`、`degraded_with_results`、`failed`、`cancelled`、`reconciliation_unknown`或“完整记录到typed failure”都不算real canary PASS；它们只证明相应诊断/恢复路径。

### 10.4 Opt-in real-user canary PASS

每个准备从`supported`推进到`released`的平台至少有一个独立、知情同意的真实用户canary installation。它不是统计SLO，也不等待历史五位用户；它验证future release evidence chain在非开发者真实操作中成立。

PASS必须同时满足：

- 用户获得的wrapper/archive digest与该平台`supported` verdict完全相同，没有临时hotfix或developer dependency；
- 从clean install或受支持N-1 upgrade开始，使用production CWS extension和默认existing-profile compatibility mode；
- 用户自己完成profile/account选择、登录/风控和一个真实但最小必要的招聘工作流；
- local receipt/Operation Evidence证明install、startup、source execution、main commit和合法success outcome；
- support bundle由用户preview后主动导出，forbidden corpus/隐私扫描为零；不自动上传JD、query、简历、候选、DOM、截图、cookie或账号信息；
- canary期间没有unknown user-visible failure、user-tab ownership violation、accepted job loss、stale authority acceptance或unrecoverable upgrade/rollback；
- canary evidence引用same exact artifact/manifest/extension identity，并由release reviewer签发immutable result。

用户选择不导出evidence、evidence identity断裂或只得到typed failure时，该installation不算PASS；不得从口头“能用”推断成功。一个platform的real-user canary不能替代另一个platform。

### 10.5 Dedicated-profile spike verdict

Spike必须在三个目标平台分别记录：

- install/distribution与profile creation ownership；
- real account login、二维码/验证码/风控和用户理解成本；
- search、detail、长会话、Chrome/OS restart和MV3 update；
- enterprise policy、proxy/CA、CWS extension安装；
- existing-profile到dedicated的迁移/重新登录与rollback体验；
- reliability比较、residual risks与privacy review。

每个平台spike结果为`ADOPT_CANDIDATE|REJECT_KEEP_COMPATIBILITY|INCONCLUSIVE`。Production release要求“spike已执行且evidence完整”；不要求`ADOPT_CANDIDATE`。没有独立产品ADR时，无论结果如何，v1 manifest仍声明`existing_profile_compatibility`。

### 10.6 Verdict algorithm

对一个platform artifact：

```text
if final artifact/signature/notarization is absent:
    declaration = not_shipped
    verdict = INTERNAL_ONLY
elif any required gate is FAIL, INVALID, NOT_RUN, or identity-mismatched:
    declaration = not_shipped
    verdict = BLOCKED
elif unknown user-visible taxonomy > 0
  or privacy/authority/user-tab/durable-job blocker > 0
  or open release blocker exists:
    declaration = not_shipped
    verdict = BLOCKED
elif controlled canary publication receipt is absent:
    declaration = supported
    verdict = RELEASEABLE_FOR_CONTROLLED_CANARY
elif opt-in real-user canary is not PASS
  or general production publication receipt is absent:
    declaration = supported
    verdict = RELEASEABLE_FOR_CONTROLLED_CANARY
else:
    declaration = released
    verdict = RELEASEABLE
```

`unsupported`只由explicit product/capability decision产生，不是测试没跑的fallback。Synthetic fixture、旧artifact、不同digest、不同extension channel或manual waiver不能把required row变为PASS。

## 11. Preserve、migrate、delete

### 11.1 Preserve and strengthen

| Current asset | v1 use |
|---|---|
| `pyproject`/`version.py`同步版本面 | 作为manifest product version输入；新增generated build identity防漂移 |
| Intel builder native-arch/native-wheel检查 | 提升为每个平台native dependency closure gate |
| WTSCLI fork full SHA、bridge build/protocol/capability与extension file-tree hash | 直接进入ComponentV1和startup compatibility验证 |
| Offline `SHA256SUMS`与archive checksum | 保留为integrity input，再增加manifest/platform signatures和Artifact Attestation |
| Staging directory + verify + rename | 扩为whole-product inactive slot transaction |
| Domi command shim与host launch | 保留host体验；移除在线dependency resolution和可变runtime trust |
| SQLite migration backup/integrity/schema-ahead拒绝 | 纳入database-group activation/rollback protocol |
| Product database-group backup | 增加logical paths、schema/build/hashes、restore和activation ID；export前脱敏path |
| Operator health disk/schema/integrity | 作为Machine Capability preflight input，扩展manifest/slot/component/browser checks |
| #322/#323/#324/#325 contracts | 引用其canonical objects/owners，不复制schema或语义 |

### 11.2 Migrate

| Current path | Required migration |
|---|---|
| `bundle-manifest.json` | 替换为signed `ReleaseManifestV1` + Artifact Attestation；保留bridge nested manifest |
| `python-prefix/<version>`、WTSCLI/extension分散目录 | 迁入同一个immutable A/B release slot；data与profile留slot外 |
| 用户机pip install Domi scripts | production改为下载/验证完整signed artifact；pip path只保留developer/internal |
| Domi Python/Node runtime发现 | production只接受manifest-contained或manifest-hash-bound runtime，不按PATH/version猜测 |
| Per-component `.previous.$$` | whole-product previous slot和persistent activation journal |
| Extension load-unpacked | production迁到CWS/enterprise channel；load-unpacked明确internal-only |
| `opencli_launcher` verification stamp | 绑定manifest/component content hash、product build ID和StartupReceipt，不只path/mtime/size |
| `seektalent update`提示pip/pipx | 替换为manifest-aware updater入口；developer update说明另列 |
| Backup manifests中的absolute path | canonical local object使用logical path + protected local ref；support projection不导出raw path |
| Manual workflows | 拆成native build/nightly/release gates并保存immutable evidence；docs-only workflow状态仍如实报告 |

### 11.3 Delete after hard cut

- production安装/恢复文档中的pip、pipx、uv、npm、git和环境变量组装产品步骤；
- 把wheel、shim或current Intel bundle称为完整产品的路径；
- production developer mode/load-unpacked extension流程；
- current release内逐component覆盖、立即删除previous和无persistent activation journal；
- 从PATH/global Domi/OpenCLI选择未声明runtime；
- same-release old/new browser fallback、dual owner或dual write；
- binary rollback时删除/覆盖DB、journal、result spool或Chrome profile；
- 以另一平台、旧constraints、开发机cache、synthetic fixture或typed failure替代exact-artifact PASS；
- 任何未经ADR的WAL、Native Messaging、DBOS、PyInstaller或dedicated-profile强制路径。

## 12. Incremental implementation slices

每个slice是独立PR，保持一个主要边界和可回滚gate。不得把runtime、browser、storage、installer、CWS和三平台release塞进一个PR。

| Slice | Scope | Acceptance gate |
|---|---|---|
| R1 — Manifest schema and verifier | `ReleaseManifestV1/ComponentV1/FileRefV1/ArtifactAttestation/PlatformVerdict` models、JCS/hash/path/component graph、goldens；不接installer | same-ID conflict、unknown field/path/symlink/collision/extra executable/dependency cycle/signing-policy negatives全过；与#322/#325 ref字段无shadow schema |
| R2 — Build identity and closure | 从source/locks/toolchain/component outputs生成product build ID、payload tree、SBOM/license/provenance；先在一个internal platform candidate运行 | clean runner可重建identity inputs；dirty/unlocked/user-cache/global runtime全部拒绝；不宣称platform supported |
| R3 — Installed root and verifier | A/B logical layout、installed manifest copy、pointer schema、inactive-stage/verify/atomic replace；不迁移data | crash在每个stage/pointer statement后得到old或new完整状态；active slot永远唯一；slot write检测 |
| R4 — Data activation transaction | drain adapter、database-group logical backup/restore、activation write barrier、schema matrix、authority rotation | N-1 migration/first-start failpoint全覆盖；无user write时restore exact，已有write时manual-recovery-only；integrity与no-active-fence refs齐全 |
| R5 — Complete sidecar payload | 把#325 sidecar/WTSCLI/bridge/result-spool exact closure接入slot，满足#323 owned-child/T2；不做T3 routing | main/sidecar/WTSCLI/bridge same build、hash、handle ownership、rollback-journal mode、parent EOF与journal crash tests通过 |
| R6 — Platform wrapper: macOS x86_64 | 修复matching constraints，选择wrapper ADR，实现native signed/notarized Intel artifact | clean Intel build、codesign/notary/Gatekeeper、offline install/N-1/rollback/uninstall/evidence matrix；通过前仍`not_shipped` |
| R7 — Platform wrapper: macOS arm64 | native arm64 dependency closure和同contract wrapper | arm64 native matrix独立PASS；不能复用Intel artifact/evidence |
| R8 — Platform wrapper: Windows x64 | per-user installer、ACL、child-tree机制、Authenticode和same layout | Windows 11 x64 native clean-machine/upgrade/rollback/uninstall/evidence matrix独立PASS |
| R9 — Production extension sequencing | CWS/enterprise production ID、compatibility matrix、MV3 lifecycle、publication/withdrawal refs；不改Source Port semantics | desktop current/previous × extension current/previous exact cases工作或fail closed；load-unpacked无法进入production verdict |
| R10 — Evidence aggregation | 消费#322 receipts/evidence和#326 build/install/publication attestations，生成signed per-platform verdict | 任一missing/mismatched digest/NOT_RUN/unknown/privacy blocker deterministic BLOCK；三个platform可不同state |
| R11 — T3 and release rehearsal | #323/#325 one-release browser hard cut后，用final candidate做三平台release rehearsal、controlled real-site canary和dedicated-profile spike | 全六operation exact-artifact smoke、old path unreachable、controlled canary PASS、spike evidence完整；仍不等于已实际发布或real-user canary通过 |

推荐顺序是R1→R2→R3→R4→R5；R6/R7/R8可在shared gates稳定后并行，R9与platform matrix交叉推进，R10在canonical evidence可用后落地，R11最后执行。任一platform wrapper可先完成，但不会改变另两个platform的声明。

## 13. Cross-contract reconciliation

| Upstream owner | 本文消费 | 本文不得改变 |
|---|---|---|
| #321 reliability | complete signed artifact、三目标platform、A/B、real canary、existing-profile默认、rollback journal | deterministic gate、outcome名称、WAL ADR前置 |
| #323 topology | main/sidecar/WTSCLI唯一owner、Domi只host、profile/user tab边界、T1/T2/T3 | process/browser owner、foreground/serve前置、dedicated profile采用决策 |
| #324 task semantics | drain到safe boundary、no-active-fence、terminal immutability、schema backup/restore安全 | run FSM、RetryPosture、ProductOutcome、rerun/recovery |
| #322 diagnostics | Manifest refs进入Machine Capability/Startup/Operation Evidence/support；使用其fault matrix | receipt/Failure Envelope/support schema、privacy、diagnostic authority |
| #325 source port | exact executable hash/product build ID、installed manifest、journal/data/profile分离、`DELETE + FULL`、T3 rollback | wire DTO、runtime token、journal phase/reconcile/retention semantics |

一致性结论：

- Release Manifest声明artifact和compatibility；Machine Capability Receipt记录一台机器实际组合。二者不能合并。
- Artifact rollback切binary pointer；#324决定run恢复，#325 journal保留external-effect evidence，#322保留诊断。Release layer不能清空这些事实来制造“干净回滚”。
- Extension是独立CWS发布物，因此manifest声明窗口，receipt记录actual，verdict引用production publication。它不可能与desktop真正原子升级。
- v1 sidecar command journal保持rollback journal；现有其他DB是否WAL不由#326全仓重决。未来迁移仍需fixed SQLite build、三平台matrix和ADR。
- Existing profile仍是v1默认；dedicated-profile spike evidence required但adoption optional。本文没有把spike结果提前写成产品决定。

## 14. Document Definition of Done and current verdict

本文完成#326的docs-only contract，当且仅当：

- [x] 完整product artifact与external dependency边界明确，wheel被排除为独立产品；
- [x] Release Manifest字段、component graph、canonical digest、signing/attestation与fail-closed规则冻结；
- [x] A/B slot、data/profile/schema backup/authority rotation和rollback posture冻结；
- [x] Windows x64、macOS x86_64、macOS arm64独立声明与simultaneous-train例外冻结；
- [x] build/sign/notarize/install/preflight/drain/upgrade/atomic switch/rollback/uninstall顺序冻结；
- [x] CWS compatibility window、extension-first默认、product-first例外和forward-fix rollback冻结；
- [x] exact artifact identity贯穿build、install、runtime、support和verdict；
- [x] clean-machine、fault、controlled real-site canary、opt-in real-user canary、dedicated-profile spike与verdict算法冻结；
- [x] preserve/migrate/delete和可按PR落地的vertical slices冻结；
- [x] #322/#323/#324/#325 ownership逐项保持；
- [x] 没有把未执行的signing、canary、platform artifact或CI写成PASS。

当前实现与发布状态仍是：

```text
Release candidate: 0.7.49 current mainline
Complete Release Manifest v1 artifact: NOT IMPLEMENTED
Windows 11 x64 declaration: not_shipped
macOS x86_64 declaration: not_shipped
macOS arm64 declaration: not_shipped
Production CWS extension evidence: NOT IMPLEMENTED
Exact-artifact clean-machine matrix: NOT RUN
Controlled real Liepin canary: NOT RUN
Opt-in real-user canary: NOT RUN
Dedicated-profile spike: NOT RUN
Verdict: INTERNAL_ONLY / BLOCKED FOR EEP v1 PRODUCTION
```

本文合并不能把任何checkbox、平台或artifact改为production-ready。只有后续实现PR产生上述exact、signed、privacy-safe evidence，并由逐平台verdict消费后，状态才能从`not_shipped`推进到`supported/released`。
