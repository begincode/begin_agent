# 第十九步验证报告：Agent 创建幂等与结果恢复

日期：2026-11-06。规格见 [feature-agent-creation-recovery.md](feature-agent-creation-recovery.md)，实施与边界见[第十九步说明](../implementation-steps/19-agent-creation-recovery.md)。

| 验收条件 | 验证方式与结果 |
|---|---|
| 相同请求只创建一次 | 后端十并发为一次 201、九次 200；浏览器丢响应后同键同体重试，一条回执对应一个 Agent |
| 不同内容不得复用键 | 规范化输入重放、内容不同 409、无键旧客户端和非法键均有集成测试 |
| 创建与回执原子提交 | 注入回执失败，Agent 回滚；注入提交后审计失败，原键可恢复 |
| 保留初始条件 | 编辑后重放仍是初始 ETag；双浏览器实际保存 412，对方元数据与零草稿保留 |
| 刷新及进程重启恢复 | 隔离 API 重启、浏览器硬刷新后 GET 找回同一编码，无默认配置 PUT |
| 结果未知不换键 | 请求发送前中止，查询 404 后保留原键，重试首次 201 |
| 身份、权限与只读 | API 账号/租户、独立 manage 权限、禁止写入与停用租户测试；浏览器切换租户及账号，其他范围 404 且无待恢复名称显示 |
| 本地输入与有限存储 | 同页编辑保留；刷新不冒称完整草稿恢复；sessionStorage 无提示词或密钥，配额异常提示后内存同键重试通过 |
| 数据库约束 | 实际迁移到 20261106_07；非特权 RLS、不可变 UPDATE/DELETE 测试通过；实际既有 24 表哈希不变 |
| 页面规范与回归 | 六状态×六视口 36 组截图/几何，34 页面巡检；完整后端 246、前端 121、UI 6、类型/构建通过 |

测试数据库为独立 begin_agent_step19_20261106，真实 PostgreSQL；浏览器为独立 Chromium。故障通过路由中止及存储方法抛错显式注入，不代表真实供应商故障。原历史版本与任务的版本编码、Run、产物、轮次和输出保持一致；合成网关两个调用、40 测试 Token，供应商/MCP 调用为零。

证据目录 `output/playwright/implementation-step-19/`：lost-response-retry、restart-reload-recovery、replay-conflict、missing-retry、scope-recovery、storage-unavailable、geometry、截图、history-before/after、final-page-smoke、backend-check/web-check、migration-preservation、restart-check、package-hashes、final-summary。

实际服务 API/Worker 保留运行，隔离验收资源在结束时清理。本轮没有远程部署、Git 提交或推送；目标 Linux 及生产容量、真机软键盘、完整备份恢复演练未验证。回执不自动清理，关闭标签页或丢失原键后的自动恢复不在保证范围内。
