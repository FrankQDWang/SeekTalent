# WTSCLI 与既有 OpenCLI 并存隔离调查

日期：2026-07-17

## 结论

可以让已经安装 OpenCLI 的 0.7.46 用户保留旧插件，同时安装 WTSCLI；不需要用户删除、停用或迁移旧 OpenCLI。

但当前 WTSCLI 0.1.0 还没有达到这个目标。它已经拥有独立、稳定的扩展 ID `aijmoehobdolindhgdljiaiimngpghcn`，却仍和 OpenCLI 共用端口 `19825`、`X-OpenCLI` 协议头、`OPENCLI_*` 环境变量、`~/.opencli` 状态目录、`@jackwener/opencli` 包名及 `opencli` 命令别名。当前 daemon 还接受任意 `chrome-extension://` Origin。两套扩展因此会连接同一个 daemon，并形成端口占用、profile 路由歧义、错误重启和状态串用。

正确方向不是“识别到旧插件后忽略它”，而是把 WTSCLI 做成一套独立产品通道。

## 已确认的当前碰撞面

| 隔离轴 | 当前代码事实 | 后果 |
| --- | --- | --- |
| 扩展身份 | WTSCLI manifest 有固定 `key`，计算出的 ID 为 `aijmoehobdolindhgdljiaiimngpghcn`；上游 OpenCLI 是另一个 ID | 这一项已经隔离；同一个固定 key 也能避免未来 WTSCLI 因安装路径变化生成新 ID |
| 传输 | WTSCLI daemon、CLI transport 和扩展都硬编码 `19825` | OpenCLI 与 WTSCLI 不能各自启动 daemon；谁占端口，另一方就会复用或失败 |
| 连接鉴权 | HTTP 只检查 `X-OpenCLI`；WS 接受任意 `chrome-extension://`，甚至接受缺少 Origin 的连接 | 旧 OpenCLI 扩展可注册到 WTSCLI daemon；当前 bridge identity 校验发生得太晚，profile 路由已经被污染 |
| 进程生命周期 | `daemon restart` 根据固定端口发现并关闭 daemon | WTSCLI 可能关闭用户原来的 OpenCLI daemon；反向同理 |
| 本地状态 | 大量路径仍指向 `~/.opencli`，并读取 `OPENCLI_PROFILE`、`OPENCLI_CONFIG_DIR`、`OPENCLI_CACHE_DIR`、`OPENCLI_WINDOW` 等 | 用户原来的 OpenCLI 配置、profile 和缓存会影响 WTSCLI；SeekTalent launcher 当前会继承这些变量 |
| 包和命令 | 包仍叫 `@jackwener/opencli`，同时导出 `opencli`、`wtscli` 两个 bin | 全局安装或脚本 PATH 解析时可能覆盖/误调用原 OpenCLI |
| 扩展内部状态 | storage key/alarm 仍以 `opencli` 命名 | Chrome storage/alarms 本身按扩展 ID 隔离，当前不会直接互相覆盖；但应重命名以消除身份混淆和未来迁移风险 |
| 浏览器资源 | 两个扩展都有 `debugger`、`tabs`、`<all_urls>` 权限 | 即使传输完全隔离，同一 Chrome profile 中两套系统若同时主动控制同一个 tab，仍可能发生 CDP attach/关闭竞争 |

0.7.46 的 SeekTalent launcher 固定安装并使用 OpenCLI 1.8.6，位置是 `~/.seektalent/opencli-runtime/opencli/1.8.6`。因此新版本应保留该目录和 `19825` 通道，不能覆盖、清理或重启它。

## 推荐目标架构

### P0：必须完成，才能宣称可并存

1. **独立传输端点**
   - OpenCLI 保持 `127.0.0.1:19825`。
   - WTSCLI 固定使用新的产品端口，例如 `127.0.0.1:19826`。
   - WTSCLI 扩展、daemon、SeekTalent Python transport 三端同时修改；不允许回退到 `19825`。
   - 路径和协议名也改为 WTSCLI，例如 `/wtscli/ext`、`X-WTSCLI-Bridge`。

2. **精确扩展身份校验**
   - WS 只接受精确 Origin `chrome-extension://aijmoehobdolindhgdljiaiimngpghcn`，拒绝其他扩展和缺失 Origin。
   - `/ping` 的 CORS 也只允许该 Origin。
   - daemon 在把连接加入 profile registry 之前校验 implementation、build ID、protocol 和 capabilities；错误 peer 直接断开，不能先注册后在命令阶段失败。

3. **独立进程所有权**
   - `wtscli daemon start|stop|restart` 只操作 WTSCLI 端口和身份。
   - 端口上若是未知实现，必须报 `port_occupied_by_foreign_process`，不能发送 shutdown 或按 PID 杀进程。
   - 卸载/升级脚本不得请求 `19825/shutdown`。

4. **独立状态与环境变量**
   - 使用 `~/.seektalent/wtscli-runtime` 和 `~/.seektalent/wtscli-state`（或 `~/.wtscli`），不读写 `~/.opencli`。
   - 外部契约改成 `WTSCLI_*`。过渡期 SeekTalent launcher 必须清除继承到子进程的所有 `OPENCLI_*`，再显式设置 WTSCLI state/config。
   - profile、cache、network capture、update cache、adapter/plugin discovery、apps.yaml 都必须进入 WTSCLI 命名空间。

5. **独立包和入口**
   - 包名改成 WTSCLI 自己拥有的名称，例如 `@seektalent/wtscli`。
   - 产品包只导出 `wtscli`，移除 `opencli` bin 兼容别名。
   - SeekTalent 始终调用已配对 bundle 中的绝对入口，不通过用户 PATH 查找。

### P1：生产分发与升级

- WTSCLI runtime、扩展和 `bridge-manifest.json` 必须作为一个原子版本发布、安装、验证和回滚。只更新 PyPI 包不能完成桥接升级。
- 普通 Windows/macOS Chrome 的正式分发应使用一个独立的 Chrome Web Store 条目（可以设为不公开展示），保持同一 WTSCLI ID；用户只需首次安装，后续由 Chrome 自动更新。
- 企业受管 Chrome 可用 `ExtensionInstallForcelist` 静默安装。普通用户不能由 PyPI/本地安装脚本静默安装自托管 CRX。
- 若暂时继续使用 unpacked extension，应永远部署到同一稳定目录、保留同一 manifest key，并让用户对 WTSCLI 点一次 Reload；它适合过渡/测试，不适合作为最终消费级更新渠道。

### P2：浏览器级进一步收口

- 将 WTSCLI 的 `host_permissions` 从 `<all_urls>` 收窄到实际猎聘域名，并把 tab group/title、storage key、alarm、日志统一改为 `wtscli`。
- 如果“完全不受影响”包括两套 CLI 同时运行且可能操作同一页面，则应给 WTSCLI 使用独立 Chrome profile/独立 `user-data-dir`。同一 profile 下，只靠不同扩展 ID 和端口不能阻止旧 OpenCLI 被用户主动指向同一个 tab。
- 更长期可以改为 Chrome Native Messaging；host manifest 的 `allowed_origins` 能精确绑定 WTSCLI ID，并消除固定 TCP 端口。但它需要跨平台原生 host 注册，迁移成本明显高于独立端口方案，不是解决本次并存问题的前置条件。

## 0.7.46 用户的无删除迁移顺序

1. 保留现有 OpenCLI 扩展、`19825` daemon、`~/.opencli` 和 `~/.seektalent/opencli-runtime/opencli/1.8.6`，不做任何清理。
2. 新 SeekTalent 安装器把 WTSCLI runtime/manifest 放入全新目录，并安装或引导一次安装固定 ID 的 WTSCLI 扩展。
3. 启动 WTSCLI daemon 于新端口，验证 daemon 与扩展的 exact implementation/build/protocol/capabilities 以及 extension ID。
4. 只有验证成功后，新 SeekTalent 才把猎聘 provider 切到 WTSCLI；验证失败时明确提示 `wtscli_extension_missing` 或 `wtscli_bridge_mismatch`，不要回退到旧 OpenCLI。
5. 旧 0.7.46 仍可继续走 OpenCLI；新版与旧版可同时存在。以后 WTSCLI 更新只替换同一个 WTSCLI ID 和版本化 runtime，不会产生第三个扩展。

## 兼容矩阵

| 用户环境 | 新 SeekTalent 预期 |
| --- | --- |
| 只有旧 OpenCLI | 非浏览器功能可用；猎聘来源明确提示安装 WTSCLI，不连接或重启 `19825` |
| 只有 WTSCLI | 猎聘正常运行 |
| OpenCLI + WTSCLI 同时安装 | 两个 daemon 分别在 `19825`/WTSCLI 新端口，互不注册、互不停止；这是主支持场景 |
| 旧 OpenCLI daemon 占用 `19825` | 对新 SeekTalent 无影响 |
| WTSCLI 扩展与 runtime 版本不配对 | fail closed，禁止使用旧 OpenCLI 兜底 |
| 两套 CLI 同时控制同一 tab | 同 profile 仍有浏览器级竞争；严格要求下使用独立 Chrome profile |
| 用户回退到 0.7.46 | 原 OpenCLI 目录、扩展和 `19825` 均保留，仍可工作 |

## 验收测试

- 在旧 OpenCLI daemon 运行且 PID 固定时，执行 WTSCLI start/restart/stop，断言旧 PID、`19825` 状态不变。
- 同一 Chrome profile 同时启用两个扩展，断言每个 daemon 的 profile 列表只出现自己的 extension implementation/ID。
- 用旧 OpenCLI extension Origin 连接 WTSCLI WS，断言握手被拒绝且 registry 不产生 profile。
- 设置一组冲突的 `OPENCLI_*` 环境变量和 `~/.opencli` 配置，断言 WTSCLI 行为不变且不修改旧目录。
- 从模拟 0.7.46 文件树升级，断言旧 runtime、旧扩展元数据和回退路径保留。
- 对 runtime/extension 任一侧单独升级，断言 bridge fail closed；配对升级后恢复。

## 主要来源

本地代码证据：

- WTSCLI：`/Users/frankqdwang/Agents/OpenCLI/extension/manifest.json`、`src/constants.ts`、`src/daemon.ts`、`src/browser/daemon-lifecycle.ts`、`src/browser/daemon-transport.ts`、`package.json`。
- SeekTalent：`src/seektalent/opencli_launcher.py`、`src/seektalent/opencli_browser/daemon_transport.py`、`src/seektalent/opencli_browser/daemon_process.py`，以及 Git tag `v0.7.46`。

Chrome 官方资料：

- [Manifest key：保持固定扩展 ID，并可用于服务端限制扩展 Origin](https://developer.chrome.com/docs/extensions/reference/manifest/key)
- [Chrome 扩展正式分发机制](https://developer.chrome.com/docs/extensions/how-to/distribute)
- [Windows/macOS 的外部安装限制](https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions)
- [企业静默安装 ExtensionInstallForcelist](https://chromeenterprise.google/policies/extension-install-forcelist/)
- [chrome.debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger)
- [Native Messaging 与 allowed_origins](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)
