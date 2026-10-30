# 当前运行页面核对

日期：2026-10-29。地址：http://127.0.0.1:18029/。

## 结论

页面可运行，尚不符合仓库现有完整交互原型。此前实现的是较窄的 MVP 范围及其运行机制，不能用该范围的测试通过代替完整原型验收。

本次内置浏览器控制连接两次启动失败，因此实际采用同一地址的独立 Playwright 浏览器。没有直接操作或截图用户内置标签页，也没有将二者混称。核对仅做页面浏览与读取，没有创建 Agent、任务或调用付费模型。

## 当前实测

- Agent、任务与运行、对话调试、工具、成员、租户六个入口均可打开，没有浏览器脚本错误。
- 桌面 1280×800、手机 390×844 未见页面横向溢出。
- 对话页加载完成后正确显示两份原有模型配置，没有页面错误提示。
- 当前数据库没有 Agent 和任务；因此这次没有实际执行创建→发布→运行→结果的业务操作。先前隔离库测试不能当作当前真实模型执行证据。
- `/api/v1/auth/config` 返回 `mode=local`，SSO 尚未在当前实例启用。

## 与现有原型的差距

对照 [交互原型说明](../output/prototype-share/agent-platform-prototype/页面说明.md)：

| 设计要求 | 当前实际 | 结论 |
|---|---|---|
| 侧边导航、平台概览、列表/创建/详情分别组织 | 顶部六项页签，主要为左右表单面板 | 未按原型对齐 |
| Task 内持续多轮对话，每轮产生 Run，全屏任务工作区 | Task 为一次性 prompt 提交；对话调试是独立模型对话 | 缺失 |
| Agent 独立配置、协作、调试、版本页；草稿、回滚和固定测试用例 | 已有创建、关系和发布机制；没有完整独立页面及回滚/测试用例入口 | 不完整 |
| 模型、工具、MCP、Skill 独立资源列表及配置、工具分组 | 模型配置合在对话页；工具仅两个本地实现；无 MCP、Skill 页面 | 不完整 |
| 平台用户、租户成员、角色权限、租户资源与审计分别组织 | 仅基础租户、成员和固定角色设置 | 不完整 |
| 对接 SSO/PMS | 项目侧适配已写；本实例仍 local，未在线登记/部署联调 | 不可判定实际接通 |

当前实际 API 也只有任务创建、查询、取消，以及版本查询/发布等入口，没有 Task 追加对话、版本回滚、MCP/Skill 管理入口，不能仅靠调整页面排版补齐这些差距。

## 证据

- [页面读取记录](../output/playwright/current-ui-review/report.json)
- [模型页加载完成记录](../output/playwright/current-ui-review/chat-loaded.json)
- [实际 API 路径](../output/playwright/current-ui-review/api-paths.json)
- [Agent 页面](../output/playwright/current-ui-review/agents.png)
- [任务页面](../output/playwright/current-ui-review/tasks.png)
- [模型对话页面](../output/playwright/current-ui-review/chat-loaded.png)
- [手机任务页面](../output/playwright/current-ui-review/tasks-mobile.png)
