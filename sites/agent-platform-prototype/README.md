# Agent 平台交互原型

这是企业内部多租户、多 Agent 平台的可点击原型，采用 27 个独立 HTML 页面。每页只包含当前功能的内容，通过相对链接跳转，支持直接从本地文件打开。

## 页面结构

| 功能 | 页面 |
| --- | --- |
| 平台概览 | `index.html` |
| 任务列表、创建与对话 | `tasks.html`、`task-create.html`、`task-detail.html` |
| Agent 列表与创建 | `agents.html`、`agent-create.html` |
| Agent 配置、协作、调试与版本 | `agent-config.html`、`agent-collaboration.html`、`agent-debug.html`、`agent-versions.html` |
| 运行记录 | `runs.html` |
| 模型、工具、MCP、Skill 列表与配置 | `models.html` / `model-detail.html`、`tools.html` / `tool-detail.html`、`mcps.html` / `mcp-detail.html`、`skills.html` / `skill-detail.html` |
| 租户信息、资源与审计 | `tenant.html`、`tenant-resources.html`、`tenant-audit.html` |
| 成员、角色与平台用户 | `members.html`、`member-detail.html`、`roles.html`、`users.html`、`user-detail.html` |

公共样式与页面脚本位于 `dist/assets/`，使用普通脚本加载，不依赖 CDN、前端运行时服务或模块网络请求。任务与 Agent 的演示数据通过浏览器存储及同一窗口的临时状态在页面之间传递。

## 已覆盖的交互

- 平台概览和运行状态。
- 我的任务列表、状态筛选和进度展示。
- 发起任务、查看任务详情和重新执行。
- 一个Task下的主Agent与SubAgent Run调用链。
- Agent列表、搜索和状态筛选。
- 三步创建Agent。
- 模型和工具选择。
- 主Agent与SubAgent关系配置。
- Agent基本配置和版本发布。
- 对话测试和模拟执行轨迹。
- 运行记录、模型和工具页面。
- 窄屏时左侧目录收窄为图标导航，保留选中状态与悬停提示。
- 桌面端支持手动展开、折叠侧边栏，并保存用户的显示偏好。
- 租户管理页面：基本信息、成员角色以及模型、工具、MCP、Skill 的租户授权。
- 租户管理子页面：成员权限、模型配置、工具授权、MCP 连接、Skill 配置和审计记录。
- 模型、工具、MCP、Skill 拆分为独立资源列表页，支持搜索并进入各自配置详情。
- 工具支持分组管理、分组筛选；Agent 选择工具时支持按组全选和取消全选。
- 任务支持持续对话，同一任务保存多轮上下文，每轮消息产生独立 Run。
- 任务详情采用全屏对话工作区，输入框固定在底部；桌面端执行链可收起，窄屏自动隐藏。
- 全屏对话采用单层任务头部、头像消息流、紧凑输入区和运行摘要，减少无效留白。
- 身份与权限拆分：平台用户、租户成员、角色权限和租户设置分别使用独立页面。

## 推荐开发顺序

1. 实现任务创建、查询、取消和重新执行。
2. 实现Agent基本信息、模型配置和草稿保存。
3. 接入模型防腐层，跑通单Agent调试。
4. 实现工具授权和RunStep执行轨迹。
5. 实现主Agent调用SubAgent。
6. 实现版本发布、回滚和固定测试用例。
7. 最后补充租户管理、角色权限和运营统计。

## 本地预览

直接使用浏览器打开 `dist/index.html` 即可。分享时请将整个 `dist/` 目录打包，接收者完整解压后打开 `index.html`，保留 `assets/` 及其他 HTML 文件的相对位置。

也可以通过本地静态服务预览：

```bash
python3 -m http.server 4173 --directory dist
```

浏览器访问：`http://127.0.0.1:4173/`

原型使用模拟数据，不会写入后端。创建的任务、对话内容以及 Agent 配置会在同一浏览器窗口的页面跳转和刷新后保留；资源配置等原有占位操作仍仅演示表单与提示。
