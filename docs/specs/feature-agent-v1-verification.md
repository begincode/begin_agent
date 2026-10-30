# Agent 第一版验证报告

日期：2026-10-29。代码位于 `codex/agent-v1`，原仓库尚无提交，本次未提交、推送或合并 master。

## 结论

项目侧第一版已实现并通过本地验收。包含统一身份、多租户权限、不可变版本、子 Agent、白名单工具、数据库队列与 Worker、执行追踪、管理界面，以及 PMS 所需的配置和运行入口。尚未登记或部署线上应用，不能将以下本地/模拟验证称为线上 SSO 或供应商真实调用通过。

## 验收映射

| 验收项 | 证据 | 结果 |
|---|---|---|
| SSO state/PKCE、单次 handoff 兑换、Cookie、实时会话校验、退出、401/403/503、CSRF | `tests/test_platform_v1.py` 中 SSO 测试，HTTPX 模拟上游 | 通过 |
| 租户成员、角色、可信租户选择、跨租户隔离 | API + 真实 PostgreSQL 集成测试；非特权角色故意漏写 WHERE 的 RLS 测试 | 通过 |
| 租户停用后保留只读历史，阻止新任务/模型调用，管理员可重新启用 | 后端及前端回归测试；真实浏览器截图与 `disabled-tenant-report.json` | 通过 |
| 版本固定模型/提示词/工具/子版本、关系拒绝循环、停用子 Agent 禁止新调用 | PostgreSQL API/Runtime 测试 | 通过 |
| 幂等、并发领取、租约过期重领、旧 Worker fencing、退避/重试耗尽、取消 | PostgreSQL 并发测试与阻塞上游取消测试 | 通过 |
| 工具循环、三种子 Agent 模式、深度/调用预算、最大步骤、超时、运行记录和文本产物 | Runtime 集成测试（含双子 Agent 屏障并发与零深度/零子调用上限拒绝），模型使用明确的测试实现 | 通过 |
| PMS 外部 YAML、字符串属性 JSON 列表、环境切换、日志/数据目录、拒绝版本内持久化 | `tests/test_deployment_v1.py` | 通过 |
| 迁移与旧配置回填、回滚重建、独立制品、CLI 端口、运行时前缀、独立角色、Worker 就绪和退出、密钥跨版本保留 | [部署验收 JSON](../agent-v1-deployment-verification.json)，临时数据库与角色均已清理 | 通过 |
| 前端功能、权限、租户切换、停用只读、样式规范、类型检查与构建 | 前端完整 `npm --prefix web run check` | 通过 |
| 真浏览器布局、控件与边界状态 | [浏览器结果](../../output/playwright/agent-v1/browser-report.json)、[SSO 头部](../../output/playwright/agent-v1/sso-ui-report.json)、[焦点/对齐](../../output/playwright/agent-v1/focus-alignment-report.json) | 通过 |

最终后端：**127 项通过，无跳过**。前端：**20 项功能测试通过**，另 **6 项 UI 规则测试通过**，Vue/TypeScript 和生产构建通过；Ruff 通过。后端运行器对 Python 3.14 asyncio 旧接口有弃用告警，不影响本次结果。

浏览器覆盖 320×800、390×844、768×1024、1280×720、844×390、390×360，六页面共 36 组；另检查统一登录长用户号和退出按钮的六视口，以及失效会话页面、停用租户历史页。文档宽度不超视口，输入区在视口内，单行控件 40px/8px；Logo 中心偏差 x=0.0078125px、y=0px；同排控件底边一致；焦点可见、手机字体 16px。长表格和代码在卡片内部滚动。截图位于 `output/playwright/agent-v1/`。390×360 是低高度检查，未冒充真实手机软键盘测试。

浏览器任务创建/版本发布/任务取消访问真实本地 API 与 PostgreSQL；聊天回复及 SSO 界面状态使用已注明的浏览器夹具，不调用真实供应商。SSO 协议本身由后端上游模拟测试验证。

## 构建与运行证据

唯一 PMS 源码产物：`dist/pms-payload`。含应用、前端构建、迁移、脚本和锁定依赖，不包含 `.env`、`.data` 或开发依赖目录。[制品摘要](../agent-v1-artifact.json)记录完整 SHA256。

独立验收将产物复制到临时 releases 目录，从该目录加载模块；以正式模式和非特权 API 角色启动，分别验证 18031/18032 两端口覆盖配置中的 19999，以及根路径和 `/agent` 前缀的 HTML base、JS 和 readyz。Worker 用单独非超级用户 BYPASSRLS 角色验证真实就绪和 SIGTERM 清理，主密钥在共享目录预置后验证版本切换可继续解密。

本机实际环境为 Python 3.14.6、PostgreSQL 17.9、Node 22；项目目标声明为 Python 3.12 / PostgreSQL 16。**精确目标环境的 Linux Python 3.12 + PostgreSQL 16 验证及 Linux 离线 wheelhouse 构建未执行**；PMS 在目标执行器运行 `"$PMS_PYTHON" scripts/build_pms.py --offline-deps` 后，须继续目标环境验收。当前本机制品是源码资源与锁定依赖，未假称为 Linux 离线完整制品。

## 线上接入前置条件与边界

- 在 SSO V13 登记 `begin-agent` 应用，提供真实原始密钥、精确 HTTPS 回调和初始平台管理员用户号；本次未获得这些配置，未进行线上登录或修改 SSO 登记。
- 在 PMS 登记 API，配置外部 YAML、数据库角色、主密钥和代理剥离前缀。非 HTTP Worker 需要 PMS 专用运行及检查模板调用 `python -m app.workers.readiness`；现有通用 HTTP 健康检查不能代替此检查。本次未修改或部署 PMS。
- 正式环境预置并备份 Fernet 主密钥；API 使用 NOBYPASSRLS 角色，Worker 使用独立受控 BYPASSRLS 角色。已有模型配置迁入预置的旧配置租户，由平台管理员后续分配成员。
- 当前工具只有文本统计与 UTC 时间，无任意 HTTP/shell/MCP 执行；文本产物在 JSONB，二进制文件对象存储未接入。任务为至少一次执行，后续外部写工具需要下游幂等。

完整参数与平台约定见 [PMS/SSO 接入说明](../pms-sso-onboarding.md)。
