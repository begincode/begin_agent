# 第七步 租户资源概况与授权

状态：已完成。依据 `tenant-resources.html` 的分类概况、资源入口和租户授权范围。

## 实现

- 新增 `GET /api/v1/resources/overview`，返回当前租户信息、四类资源汇总和权限允许的成员/Agent 数量。未实现的额度、费用与默认模型不作为真实数据展示。
- `GET /api/v1/resources` 支持 `search`、`resource_kind`、`availability`、`limit`、`offset`。四类资源通过字段投影 UNION ALL 与授权表关联，替代原来每类最多 1000 条和逐资源查询授权；不返回凭据、端点、指令或工具正文。
- 主状态按“撤销授权 → 资源停用 → MCP 未就绪 → 可配置”的优先级判定；MCP 需已检测且至少发现一个工具。`active` 与授权 `enabled` 仍分别返回，分类状态互斥，汇总与列表一致。
- 资源概况页支持分类卡片、搜索、状态与类型筛选、稳定分页、详情和授权操作。`resources.read` 可看，`tenant.manage` 可写；租户停用时只读。成员数量受 `members.manage` 约束，Agent 数量受 `agents.read` 约束。
- 全局授权目录分批加载。Agent 草稿不能新选撤销资源，已有绑定可取消；模型保留原选择并标记不可用，工具分组全选排除不可配置项。后端发布与执行仍是最终鉴权。
- 主要代码：`app/services/resources.py`、`app/api/workspace.py`、`TenantResources.vue`、`AgentEditor.vue`、`PlatformApp.vue`。

## 验证

- 相关后端 16 项通过，覆盖 66 个混合资源的分页/汇总、MCP 待检测与空工具、撤销/停用状态、权限和跨租户。无数据库迁移。
- 前端完整 check：48 项功能测试、6 项 UI 规则测试、类型检查与生产构建通过；ruff 通过。
- Chromium 实际进入资源第二页、筛选并撤销工具授权，在草稿中移除旧绑定且选择同组可用工具；已发布版本编码保持不变。脚本增加渲染完成等待后，绑定与禁用状态断言通过。
- 三种状态各验收六种规定视口，共 18 组截图/几何数据；无页面溢出，单行控件 40px/8px。
- 证据：`output/playwright/implementation-step-07/geometry.json`、`context.json`、`draft-check.json`、`interactions.json` 与检查日志。业务写入只在隔离库，没有供应商或外部 MCP 调用。

## 边界

可配置表示当前状态满足资源选择条件，不代表外部服务始终在线；MCP 状态使用最近保存的检测结果，没有主动联网探测。资源信息在页面加载、刷新与当前授权操作后更新，其他会话变更需刷新。概况按请求聚合，尚无缓存。实际服务与完整回归在本轮收尾统一更新。

总结文章：[租户资源概况与授权的设计与实现](articles/07-tenant-resources.md)。
