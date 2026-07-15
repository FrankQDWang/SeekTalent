# 浏览器内存、焦点与进程基线

日期：2026-07-15

关联事项：[#292](https://github.com/FrankQDWang/SeekTalent/issues/292)

## 结论

当前最先要解决的是 tab 生命周期，不是先猜测 Chrome 参数。

- 当前代码每处理一个候选人会新建详情 tab，`finalize_liepin_resumes()` 不回收浏览器资源。本机增量测量中，10 个猎聘 tab 对应新增 10 个 renderer，Chrome 汇总 RSS 相对起点增加约 2.99 GiB；精确关闭后 renderer 数回到起点。
- 当前 Workbench 主路径在 Python 进程内执行 Liepin worker，但每个 OpenCLI 动作仍启动一次 Python launcher 和一次 Node CLI。只读 `daemon status` 的 p50/p95 为 150.3/154.9 ms，瞬时子进程树 RSS 中位数约 129.5 MiB。
- 仓库仍有一条 Domi/Pi 工具路径会为每个动作再启动 Python helper。该路径同一只读动作的 p50/p95 为 986.1/1133.4 ms，瞬时子进程树 RSS 中位数约 227.1 MiB；它不是当前 Workbench Liepin provider 的主调用链，不能把这组数字冒充主路径基线。
- OpenCLI daemon 常驻 RSS 约 55 MiB，未随 0 到 10 个 tab 明显增长。当前主要线性增长来自 Chrome renderer；逐动作 CLI 启动是独立的延迟和瞬时工作集问题。
- macOS 已有足够证据确认 tab 增长和逐动作进程成本。Windows 8GB 必须在真实用户安装形态上补测 Private Bytes、Working Set、焦点和进程树后，才能关闭 #292；它仍是发布门槛，但不再阻塞 #293 的传输决策。
- OpenCLI daemon 已经提供 `POST /command`，不需要再增加一个 broker 进程。同一只读 `tab find` 经 Node CLI 的 p50/p95 为 147.2/169.2 ms；Python 复用 daemon HTTP 连接为 2.24/3.68 ms，且不启动子进程。第一阶段应把 fork daemon 协议正式化并由 SeekTalent 直连。

## 代码事实

当前主 Workbench 路径：

```text
Workbench/runtime
  -> LiepinOpenCliWorkerClient（当前 Python 进程）
  -> OpenCliBrowserAutomation
  -> 每个动作启动 Python opencli_launcher
  -> 每个动作启动 Node OpenCLI CLI
  -> 常驻 OpenCLI daemon
  -> Chrome 扩展
  -> Chrome
```

次要 Domi/Pi 工具路径：

```text
Domi/Pi Node
  -> 每个动作启动 Python opencli_browser_cli helper
  -> 每个动作启动 Python opencli_launcher
  -> 每个动作启动 Node OpenCLI CLI
  -> 常驻 OpenCLI daemon
  -> Chrome 扩展
  -> Chrome
```

与基线直接相关的现状：

- `session_status_probe()` 会调用 `open_liepin_tab()`，所以当前“状态检查”不是只读操作。
- `_open_opencli_managed_liepin_tab()` 对每个详情使用 `tab new`，并在一个固定 session 下累计 page marker。
- 多处恢复路径使用 `tab select`；上游 1.8.6 的新 tab 也是 active tab。这些都是可见 tab 切换路径。
- `finalize_liepin_resumes()` 只组装结果，不关闭 tab。
- 本机历史 `.seektalent/opencli_leases` 中有 40 个非空 owned-page marker 文件、共 98 条记录，单个 session 文件最多 13 条。它们是历史追踪记录，不等于当前仍存活的 tab 数，但能证明现有记录会跨 run 累积且不会由 finalize 清理。

## macOS 测试环境

- MacBook Pro `Mac16,1`
- Apple M4，32 GB 内存
- macOS 15.6.1
- Python 3.12.11
- Node 24.16.0
- Chrome 已安装版本 150.0.7871.115
- OpenCLI fork build：`seektalent-opencli-1.8.6+prototype.1`

本次只使用用户现有猎聘窗口中的 inactive owned tab。宿主 tab 的 page identity、active 状态和窗口保持不变；测试结束后所有新增 tab 均通过 verified close 真实移除。没有用当前旧路径再创建一个会干扰用户的新 Chrome 窗口，因此本节不把“旧路径抢焦点次数”伪装成已测数据。

## 逐动作启动成本

所有命令均关闭业务 pacing，只测同一台机器上的只读 `daemon status`。p95 使用排序样本的 nearest-rank 值。瞬时 RSS 以约 3–5 ms 频率采样根进程及其后代；不包含常驻 daemon。

| 路径 | 样本 | p50 | p95 | 瞬时进程数峰值 | 瞬时子进程树 RSS 中位数 | RSS 最大值 |
|---|---:|---:|---:|---:|---:|---:|
| Python launcher -> Node CLI | 30/15 | 150.3 ms | 154.9 ms | 2 | 129.5 MiB | 138.4 MiB |
| 直接 Node CLI（对照） | 30/15 | 130.8 ms | 148.0 ms | 1 | 100.7 MiB | 102.2 MiB |
| Domi/Pi Python helper -> launcher -> Node CLI | 20/8 | 986.1 ms | 1133.4 ms | 3 | 227.1 MiB | 245.6 MiB |

解释：

- 仅移除 Python launcher 不能解决主要延迟，因为直接 Node CLI 的 p95 仍约 148 ms。
- 常驻 broker 是否值得进入第一阶段，应比较“绕过逐动作 CLI”后的真实命令 p95、常驻 RSS 和协议风险；不能只根据进程层数作结论。
- 如果 Domi/Pi helper 不再属于生产入口，应删除或隔离该入口，而不是专门为一条废弃路径设计 broker。

### 现有 daemon 直连对照

OpenCLI daemon 本身就是 CLI 与扩展之间的常驻 broker。fork 当前已有 loopback-only `POST /command`、自定义 header、命令 id、绝对 deadline、同 id journal replay 和结构化 error code。对同一个只读 host discovery `tab find` 进行对照：

| 路径 | 样本 | p50 | p95 | 每次动作新增子进程 |
|---|---:|---:|---:|---:|
| Node CLI -> daemon -> extension | 30 | 147.2 ms | 169.2 ms | 1 |
| Python persistent HTTP -> daemon -> extension | 100 | 2.24 ms | 3.68 ms | 0 |

因此 #293 的结论是：第一阶段替换逐动作 CLI，但不新增第二个 broker。应在 OpenCLI fork 中冻结一个受支持的 SeekTalent daemon command contract 和 capability，由 SeekTalent 维持 HTTP keep-alive 连接；CLI 只保留给安装、启动、诊断和人工调试。

直接接入必须保留现有安全语义：

- 每个逻辑动作使用唯一 command id 和绝对 deadline。
- 只有协议允许的同 id transport replay 才能重发；`command_result_unknown` 等未知结果禁止自动重试。
- capability/build/协议失配只禁用当前 Liepin source，不回退到逐动作 CLI，也不影响其他 source。
- daemon 未运行时只允许启动配对离线 bundle 中的 daemon；禁止 npm/GitHub 下载。
- SeekTalent 只开放自己所需的固定命令形状，不把任意 `eval` 或完整 OpenCLI 协议暴露给业务层。

## Tab 与 Chrome RSS 增长

在同一个已登录猎聘窗口内依次创建 1、3、5、10 个 inactive owned tab。每个 checkpoint 等待 2 秒后采样；结束后精确关闭全部 owned tab并等待 5 秒。Chrome RSS 是所有 Chrome 进程 RSS 之和，会重复计算部分共享页，只能使用同一轮的增量和趋势，不能当作 Chrome 的精确物理占用。

| Checkpoint | owned tab | renderer 增量 | renderer RSS 增量 | Chrome 汇总 RSS 增量 |
|---|---:|---:|---:|---:|
| 起点 | 0 | 0 | 0 | 0 |
| 1 个 tab | 1 | +1 | +267.2 MiB | +661.1 MiB |
| 3 个 tab | 3 | +3 | +544.8 MiB | +899.5 MiB |
| 5 个 tab | 5 | +5 | +1417.8 MiB | +1760.9 MiB |
| 10 个 tab | 10 | +10 | +2695.5 MiB | +2988.4 MiB |
| 全部关闭后 | 0 | 0 | -527.0 MiB | -217.9 MiB |

这一轮不能给出稳定的“每个 tab 固定占多少内存”，但可以回答优化方向：tab/renderer 数随候选人数一比一增长时，内存不会平台化；真实关闭后 renderer 数和内存能被回收。

## 可重复采集协议

基线与优化后都执行 3 轮，分别处理 3、5、10 个成功打开详情的候选人。每轮使用同一账号、同一搜索条件、同一 Chrome 版本，并记录中位数；失败或风控轮次单独保留，不混入性能中位数。

采样点：

1. Chrome 已打开猎聘、SeekTalent 未开始 run。
2. 搜索页 ready。
3. 第 1、3、5、10 个详情采集完成。
4. run 业务结果返回时。
5. 返回后 5 秒。
6. 返回后 65 秒，用于验证 idle fallback。

每个采样点记录：

- extension 证明的 live owned tab 数、待回收数和最老 deadline age；不扫描或记录用户 tab 内容。
- Chrome 总进程数、普通 renderer 数、浏览器/GPU/renderer RSS。
- Windows 同时记录 Working Set 与 Private Bytes；macOS 使用 RSS。
- SeekTalent Python、OpenCLI daemon、临时 Python/Node CLI 的进程数与内存。
- OpenCLI 安全 timing artifact 中每个命令的 p50/p95；不得记录参数、URL、页面文本或标题。
- 当前前台 OS 进程、Chrome focused window identity、用户 host tab 的 active 状态。
- run 结果生成时间、cleanup submission 时间和后台 close outcome。

## Windows 8GB 采集要求

Windows 结果必须来自 8 GB 物理内存的 Windows 11 机器，并使用准备交付给用户的离线 Python、Node、OpenCLI fork 和扩展组合。不能用开发机全局 Node 或运行时 npm 安装代替。

最少采集：

- `Get-CimInstance Win32_Process` 建立父子进程树和命令类型，不保存完整命令参数。
- `Get-Process -Id <pid>` 读取 `WorkingSet64`、`PrivateMemorySize64`、`PeakWorkingSet64` 和 `PeakPagedMemorySize64`。
- `GetForegroundWindow` 与 `GetWindowThreadProcessId` 记录前台进程；扩展记录 focused window identity 和 host active 状态。
- 每 100 ms 采样一次运行中进程，动作启动窗口另以不高于 10 ms 的间隔采样临时 Python/Node 子进程。
- 记录系统 commit charge、可用物理内存和 hard fault/page fault 趋势，判断是否发生分页抖动。
- run 结束 5 秒后必须没有临时 helper、launcher 或 Node CLI 孤儿进程。

Windows 实机数据未补齐前，#292 保持打开；不得把本机 32 GB macOS 数据换算成 8 GB Windows 结论。

## 第一阶段验收门槛

这些门槛判断“优化是否有效”，不是把运行时硬编码成固定两个 tab。

### 生命周期与焦点

- 不新建 Chrome 窗口；只在按 #291 选中的用户现有窗口中创建 inactive owned tab。
- 用户 host tab 不 bind、不 select、不 navigate、不 reload、不 close；100 次正常/异常 run 中 host active 状态和 focused window 不发生 SeekTalent 引起的变化。
- live owned tab/renderer 不再随已完成候选人数线性增长。以第 3 到第 10 个候选人的 3 轮中位数作线性拟合，renderer slope 必须不高于 `0.1 renderer / candidate`。这是验收趋势，不是运行时 tab 数上限。
- 正常后台 close 在 run 返回后 5 秒内完成；任一辅助回收环节失败时，extension 60 秒 idle fallback 在 65 秒 checkpoint 前完成。
- run 关键路径只提交 non-throwing cleanup：cleanup submission p95 不高于 10 ms，任何 close/verification/telemetry 故障都不能增加 source 结果返回等待。

### 内存与进程

- macOS：第 10 个候选人 checkpoint 的 Chrome renderer RSS 中位数不高于第 3 个 checkpoint `+512 MiB`，且 renderer slope 通过上述门槛。
- Windows 8GB：第 10 个 checkpoint 的 Chrome renderer Private Bytes 中位数不高于第 3 个 checkpoint `+256 MiB`，系统不能持续进入分页抖动；该阈值必须用实机结果复核，必要时只能收紧，不能因实现未达标而放宽。
- run 返回后 5 秒，临时 Python helper、Python launcher 和 Node CLI 孤儿数为 0；OpenCLI daemon 和扩展允许常驻。
- tab 全部回收后，renderer 数必须回到起点；内存以趋势判断，不要求操作系统立即把所有缓存页归零。

### 命令链路

- 第一阶段不得比当前主路径 p95 154.9 ms 更差。
- broker 只有在主路径真实动作 p95 至少降低 50%，且新增常驻 Private Bytes/RSS 不高于 128 MiB、没有扩大故障域时，才算有效；否则先保留现状，避免用新协议换来无收益复杂度。
- 现有 daemon 直连的只读命令 p95 为 3.68 ms、没有新增常驻进程，已超过 50% 收益门槛。生产接入仍需用搜索、详情采集和 close 的混合命令分布复核，p95 目标不高于 75 ms。
- Domi/Pi helper 路径必须先确认是否仍是生产入口。若不是，删除生产接线比优化它更合适；若仍是入口，其 p95 不能继续维持约 1 秒。

## 对后续事项的约束

- #293 采用现有 daemon 直连，不新增 broker 进程；Domi/Pi helper 的生产可达性继续由 #294 清理边界确认。
- #294 只能合并与同一浏览器触点直接重叠的重复 allowlist/dispatch/依赖归属问题，不能夹带全仓 AISlop 整理。
- #296 必须把 Windows 8GB 实机通过作为发布门槛，而不是在缺少数据时预先宣称“低配 Windows 已解决”。
