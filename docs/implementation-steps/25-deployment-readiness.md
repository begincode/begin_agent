# 第25步：部署预检、离线运行包与目标版本验收

日期：2026-11-06。用户授权连续完善第二十至二十五步，范围见[计划](../specs/feature-steps-20-25.md)。

## 交付

新增 deploy/pms/api.sh、worker.sh（run/check），声明 PMS Python 协议并复用平台解释器，Worker 就绪不伪装成 HTTP；新增部署角色授权 SQL。API 为非超级用户 NOBYPASSRLS，Worker 为独立非超级用户 BYPASSRLS，仅授予执行和心跳所需表；正式 Worker 启动比较迁移版本。预检不调用模型/MCP/云桶，不输出 DSN 或密钥；检查 Python 3.12、Linux、PostgreSQL 16、迁移、角色、关键表、持久目录和主密钥。

构建包新增 deploy 和 build-runtime.json，记录完整 Python 版本、OS/架构、依赖锁摘要及迁移。使用 Python 3.12 构建同平台离线 wheelhouse；在全新虚拟环境禁用索引、仅从 wheelhouse 安装并启动打包 API/Worker。当前 wheel 是 macOS/arm64，不能冒称 Linux 运行包，正式部署需在目标 OS/架构/Python 完整版本重新构建。

新增受限 HTTP 读取压力脚本，只接受 loopback、有界并发和私有会话文件，不执行写请求。API/Worker模板是项目侧交付，PMS 仍须实际登记并配置支持命令式 Worker 就绪检查的运行模板。

## 本轮实测

- 本机独立 PostgreSQL 16 实例与 Python 3.12.13，prod 配置下启动独立 API/Worker 数据库角色，受控迁移账号升级到 20261106_08。完整后端 279 项、无跳过；原 Python 3.14/PostgreSQL 17 环境完整 279 项也通过。
- 前端 135 项、UI 6 项、类型及生产构建通过。补齐附件下载卸载中止，新增取消/403 回归；最终实际附件下载原字节复验通过。
- 初次部署预检发现 Worker 无权读取 alembic_version，补齐最小授权及正式 Worker 启动迁移比较后，完整重编译、打包、重启并复验。离线新虚拟环境可以启动打包 API、真实 Worker，上报共享心跳；账号登录成功。
- 独立 Chromium 六规定视口检查目标版本状态页，34 页面巡检无脚本异常或非预期提示。最终本机只读压力 200 请求/20 并发，全部 200，p50 31.35ms、p95 57.54ms、总时长 0.367s；只是单机小样本读取结果，不代表生产容量或模型吞吐。
- 本地预检 ready_for_local_rehearsal；Linux 严格预检 not_ready，OS 差异保留，未改检查阈值。生产 Linux、TLS代理链、真实云桶、外部业务 MCP 和实际 PMS 发布未验收。

证据 output/playwright/implementation-step-25：双环境完整日志、web-check、package-build、offline-install、role-grants、preflight-local/linux-target、http-capacity、interactions、geometry、截图、34页巡检及最终收尾报告。业务验收使用独立库/主密钥；全部真实模型供应商及外部 MCP 调用为零。

结束时保留实际 18029 API/Worker，关闭临时 API/Worker/PostgreSQL 16 和验收浏览器，撤销临时会话、删除临时凭据。私有备份和环境资料留在忽略目录，不进入运行包或 Git。未提交推送，未操作 master。

## 仍需外部环境

用户尚未提供 Linux 测试机、云桶、业务 MCP 或 PMS 登记配置。补齐这些条件后，应在目标环境重新构建离线包、执行预检/迁移和正式业务验收；当前交付不宣称远程上线。MiniMax/Kimi 等新增供应商也仍需要可用配置才能做真实调用。

独立文章：[把“本机能跑”变成可检查的部署条件](articles/25-deployment-readiness.md)。
