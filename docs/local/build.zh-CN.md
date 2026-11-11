# 本地安全版本的构建与维护

本分支供 Apple Silicon Mac 透明网关自用。保留原应用／服务标识，通过源码构建 GUI、Mihomo、服务和安装工具，使用 ad-hoc 签名。它不需要 Apple 证书，不会自动替换 `/Applications`、启动代理或安装服务。实际切换请按 [网关维护与回退](gateway-maintenance.zh-CN.md) 操作。

## 第一次构建

要求已安装 Xcode 命令行工具、Python 3、Git 和 rustup。不要用管理员身份编译。

```sh
python3 scripts/local/build.py bootstrap
python3 scripts/local/build.py release
```

默认工具链、下载缓存、独立组件源码、编译结果和发布包都在当前工作树 `.local-build/`，不会进入 Git。可给以上每条命令指定相同的 `--state-dir /绝对路径/构建目录`，以便多个 worktree 共享工具链和缓存。不要让多个构建进程同时向同一 state directory 打包。

`bootstrap` 固定 Node、pnpm、Rust、Go 版本；下载的 Node／Go 归档必须匹配清单中的 SHA-256。它不切换系统默认工具链。`release` 要求修改已提交且工作树干净，拒绝有未记录修改的服务／核心源码；使用 Cargo.lock、pnpm-lock.yaml、go.sum 及固定源码提交。

公共 GEO 数据使用固定 GitHub 资产 ID 与哈希。上游删除旧资产时，全新构建会失败，不会偷偷使用另一份数据；保留已有缓存，或审查新数据后更新清单。GEO 数据本身不是源码编译的可执行组件。

不使用上游 `pnpm prebuild`：该命令会下载上游核心／服务和滚动 Alpha。此分支的本地脚本准备全部程序组件，两种核心文件槽位均放入同一个稳定内核；界面和后端禁用 Alpha 切换。

## 日常开发

```sh
# 只启动回环地址上的前端开发服务器，不启动 Rust、核心或 service
python3 scripts/local/build.py ui

# 复用工具链和编译缓存，构建新的可交付版本
python3 scripts/local/build.py release
```

本分支的 `pnpm dev` 默认进入上述前端模式，`pnpm build` 默认进入本地 release 流程。显式调用上游 native／fast 构建命令不属于此发布流程，不能把其产物直接用作网关版本。

前端浏览器模式没有完整 Tauri API，适合界面迭代与已有 mock。单元测试使用 `pnpm test`；项目的 native dev 命令会操作系统网络，不能视为与生产 TUN 隔离。联调 service、DNS、路由或 TUN 应用测试机，或先进入维护窗口。

构建脚本的 `prepare` 仅准备组件；`package` 验证准备阶段的精确文件哈希后编译应用；`release` 连续执行两者。初次调试允许 `package --allow-dirty`，但这种产物标记 `developmentOnly`，不应作为正式网关版本。正式构建失败后先修复并提交，再重新构建。

每次打包使用版本、Git 提交和时间组成独立目录，保留历史产物。目录包含 App ZIP、`SHA256SUMS` 和 `provenance.json`。签名用于本机代码完整性，不等于 Apple 公证或独立第三方安全认证。首次切换可能需要在 macOS「隐私与安全性」中针对该应用允许打开，不应关闭系统安全机制。

## 更新与分支

`origin` 是自有 fork，`upstream` 仅用于取得上游代码。安全版本维护在 `local/security-v2.5.10`；不要强推覆盖已有 `dev`。更新上游时使用新的 worktree／分支，审阅差异、重放最小补丁、更新固定版本清单、运行安全行为测试，再产出新包。

不要推送 `v*.*.*` 标签来发布本地包；上游继承的 workflow 会据此触发官方发布流程。此方案维护源代码分支，产物在本机手动安装。订阅、真实配置、日志和回退备份绝不进入 Git 或 GitHub release。

GUI 与核心的下载升级都被禁用，以免覆盖本地安全策略。订阅及 GEO 数据更新仍可用；未来软件安全修复需要主动审阅上游并重新构建，不能因为停用了自动更新而长期不维护。

## 本版本的安全边界

- 核心不接受 `CLASH_*` 环境变量覆盖启动配置，保留 GUI 所需显式参数；禁用 post-up/down shell hooks 和环境变量放宽文件路径保护。
- WebDAV 只接受有效证书的 HTTPS，拒绝跨源和降级重定向；旧 HTTP／未受信证书地址需调整。
- WebUI 不再把控制密钥放进打开的 URL；旧带 secret 的地址拒绝打开。
- 管理 API 限制在回环与本地应用来源；原本禁用的 TCP API 不会被启用。LAN 代理、DNS 和 TUN 配置保留。
- 核心及管理界面下载升级被禁止；服务使用生产权限校验，绝不以 service 的 `test` feature 构建发布组件。
- 没有自动添加 PF 规则、关闭 allow-lan 或修改客户端路由。可信局域网范围、防止故障直连和真实 TUN 可用性仍需在维护窗口验收。
- WebView 的 CSP 保持上游配置；同一 macOS 用户的恶意程序、恶意订阅、第三方依赖和节点提供方仍属于需要考虑的风险。本地构建不等于已证明不存在漏洞。
