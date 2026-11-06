# Begin Agent

按设计继续完善的实现、验收与总结文章见 [分步实现目录](docs/implementation-steps/README.md)。

面向企业内部的多租户 Agent 平台。第一版包含 独立账号密码登录、租户成员与角色、Agent 不可变版本、工具与子 Agent 授权、PostgreSQL 租约任务队列、Agent Loop、执行追踪、文本及文件产物（私有本地/S3 对象存储）；保留 DeepSeek、智谱 GLM、MiniMax、Kimi 的普通及流式对话适配。PMS 部署协议见 [接入说明](docs/pms-sso-onboarding.md)，验收范围见 [第一版规格](docs/specs/feature-agent-v1.md)。

## 本地运行

1. 安装依赖：`make install`。
2. 复制 `.env.example` 为 `.env`，设置 `AGENT_DATABASE_URL`。例如 `postgresql+asyncpg://begincode:begincode@127.0.0.1:5432/begin_agent`，请按实际数据库账号填写。
3. 初始化数据库：`make migrate`。
4. 编译前端：`make web-install && make web-build`。
5. 启动后端：`make api`，访问 `http://localhost:8000/`。
6. 首次打开设置管理员账号；在“模型”配置 API Key，在“Agent”保存草稿、选择工具/Skill/MCP 和子 Agent，调试后发布版本。
7. 启动独立 Worker：`make worker`；在“任务”提交并持续对话，在“运行记录”查看步骤与消息。

模型 API Key **在页面录入，加密存入 PostgreSQL**，不从供应商环境变量读取。列表、配置读取、错误响应都不返回密钥；编辑时密钥留空保留原值。支持同一供应商建立多份配置。

对话页提供思考模式、思考级别和流式输出开关，按具体型号的能力显示可选项。思考内容独立展示，可折叠，流式时逐段追加；正文和思考 Token 分别展示（供应商返回时）。未返回思考内容会明确提示。参数作用于本次请求，在当前页面按模型配置分别记忆。

回复正文支持 Markdown 标题、列表、表格、引用、链接、行内代码和代码块，流式输出时同步渲染。代码块提供语言标识、语法高亮与一键复制，保留原代码缩进和换行；表格、长代码在卡片内横向滚动。模型输出的原始 HTML 不执行。

本机开发自动生成 `.data/model-credential.key`（0600），用于加解密数据库中的密钥。该文件不入 Git。数据库与加密主密钥须分别备份；丢失主密钥后无法解密历史凭据。部署环境通过持久文件或 `AGENT_CREDENTIAL_ENCRYPTION_KEY` 提供同一主密钥；这不是模型 API Key。Compose 给 API 与 Worker 挂载共享的 `model_credentials` 卷。

本地模式仅允许 loopback 请求，或显式本地管理令牌。默认使用 `AGENT_AUTH_MODE=password`。首次访问创建平台管理员；密码至少 12 位、加盐 scrypt 保存，会话为 HttpOnly Cookie，数据库仅保存令牌摘要。平台管理员创建独立账号，再按用户名加入租户并分配内置或自定义角色；支持停用、重置密码及退出所有设备。正式 HTTPS 部署使用 Secure Cookie。完整运行配置和数据库角色见 [PMS 与内置账号接入说明](docs/pms-sso-onboarding.md)。

## 四家适配范围

| 供应商 | 默认模型 | 单独处理的差异 |
|---|---|---|
| DeepSeek | `deepseek-flash` | 思考开关、思考时的工具选择约束、缓存 Token、结束原因 |
| 智谱 GLM | `glm-5.3-flash` | 温度范围、不可关闭思考、历史推理保留、tool_stream、业务错误码、异常 finish_reason |
| MiniMax | `MiniMax-M3.1-Flash` | adaptive thinking、reasoning_split、max_completion_tokens、HTTP 200 业务错误、最终完整 message 帧去重 |
| Kimi | `kimi-k3` | 固定采样参数、K3/K2.x 思考配置差异、max_completion_tokens、完整历史推理回传 |

支持范围以已实现的参数规则和测试为准，不代表任意新型号的所有能力。多模态输入、供应商托管工具、Responses/Messages API 尚未接入。直接聊天接口的 Tool Call 只返回结构化数据；任务 Worker 会执行当前 Agent 版本授权的本地工具和子 Agent 调用。

## 接口

- `GET /api/v1/models`：供应商目录与建议默认值，不是连通性状态。
- `GET /api/v1/model-profiles`：数据库配置列表，无密钥，附带各型号的 `capabilities`（思考模式、级别、流式和思考内容能力）。
- `POST /api/v1/model-profiles`：新建模型配置，必填 `api_key`。
- `PATCH /api/v1/model-profiles/{profile_code}`：修改配置，省略密钥则保留。
- `POST /api/v1/model-profiles/{profile_code}/verify`：对已保存配置发起一次真实文本请求，不代表完整能力验证。
- `POST /api/v1/chat`：非流式调用。
- `POST /api/v1/chat/stream`：流式调用，以 SSE 返回正文、推理、工具调用、用量和完整结束响应。

对话通过不可变 `profile_code` 选择数据库配置，不接受前端 API Key 或临时地址覆盖。示例：

```json
{
  "profile_code": "mdl_...",
  "messages": [{"role": "user", "content": "你好"}],
  "max_tokens": 4096,
  "thinking": "enabled",
  "reasoning_effort": "high"
}
```

多轮调用应保留响应的 `message`，尤其是 `reasoning_content`、`tool_calls`，工具结果使用 `tool_call_id` 对应。前端会按配置隔离上下文并回传完整推理字段，错误或中断的输出不会作为成功响应重放。

`thinking` 支持 `auto/enabled/disabled`，默认 `auto`；`reasoning_effort` 不传表示模型默认。DeepSeek、GLM-5.3-Flash、Kimi K3 支持 `low/high/max`，MiniMax-M3.1-Flash 支持 `low/medium/high/xhigh/max`，已覆盖的 Kimi K2.x 不支持指定级别。关闭思考时省略级别；不支持的组合在发起外部请求前返回 422。流式开关通过选择 `/chat/stream` 或 `/chat` 实现，不在请求体中增加无效的 `stream` 字段。

## 验证

前端变更遵守 [前端开发规范](docs/frontend-standards.md) 和 [web/AGENTS.md](web/AGENTS.md)。每次修改必须运行 `make web-check`；UI 变更还必须完成真实浏览器的对齐、居中、响应式和交互验收。自动规则检查不能替代浏览器检查。

```bash
# 协议和错误处理测试；未指定测试数据库时，数据库测试会明确跳过
pytest -q

# Markdown 渲染、流式代码块、复制与安全处理的前端测试
npm --prefix web test

# PostgreSQL 集成测试：仅指向专用测试库；每个测试使用独立随机 schema
TEST_DATABASE_URL=postgresql+asyncpg://user@localhost/test_database pytest -q

# 对数据库中已启用模型进行真实调用，产生可复查的 JSON 结果
python -m scripts.verify_model_providers --output /tmp/model-report.json

# 通过运行中的本机服务验证各级别、两种输出模式及思考内容的一致性
python -m scripts.verify_generation_controls --output /tmp/generation-report.json
```

真实验证覆盖文本、多轮、流式与 Tool Call/结果回传。每份配置最多 5 次模型请求，不自动重试，每次默认最多 4096 输出 Token。`--profile-code` 可只检查某份配置。执行前需具备对应模型权限与余额。缺少数据库、模型配置或可解密的密钥时结果为 `BLOCKED`；输出截断、协议错误和断流不得记为通过。

协议测试使用 `httpx.MockTransport`，验证请求和响应处理逻辑；PostgreSQL 测试使用真实数据库、模拟模型。两者都不等于供应商真实调用通过。当前结果见 [适配验证报告](docs/model-provider-adaptation-verification.md)。

## 部署

前端只在构建时运行，产物写入 `app/static/`，由 FastAPI 同时提供页面和 API；运行时无独立前端服务。

```bash
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose up --build -d api worker
```

API 与 Worker 是独立后端进程。上述 Compose 用于本机开发；PMS/正式环境使用内置账号登录、独立数据库角色和外部持久主密钥，按 [接入说明](docs/pms-sso-onboarding.md)登记部署。

## 文档

- [技术选型](docs/technology-selection.md)
- [数据模型设计](docs/multi-tenant-multi-agent-er-design.md)
- [模型适配设计](docs/model-adapter-design.md)
- [当前模型接入说明](docs/chat-model-integration-code-guide.md)

文件产物支持成功任务追加人工上传文件、服务端分页及鉴权下载；默认单文件 20 MiB。本地目录与 S3 兼容后端配置、备份和失败补偿见 [文件存储运行说明](docs/pms-sso-onboarding.md#文件产物与私有对象存储第十四步)。

真实模型与 MCP 的完整任务验收、安全运行诊断、独立 MCP 服务和只读验证脚本见 [第十五步说明](docs/implementation-steps/15-live-model-mcp.md)。

页面拆包、按需候选目录及 SSE 快照性能基准见 [第十六步说明](docs/implementation-steps/16-performance-optimization.md)；快照微基准可通过 `python -m scripts.benchmark_task_stream` 在显式隔离数据库中执行。

系统状态页提供数据库迁移、本机 Worker 心跳、当前租户队列与异常任务诊断；队列额外要求任务查看权限。采样范围、失败恢复及部署限制见 [第十七步说明](docs/implementation-steps/17-system-diagnostics.md)。

Agent 配置支持元数据/草稿/可选发布的原子保存，以及多人编辑冲突恢复。元数据、协作、发布和回滚接口需携带当前 If-Match，缺失返回 428、过期返回 412；调用协议及向导恢复见 [第十八步说明](docs/implementation-steps/18-agent-concurrent-editing.md)。

Agent 创建支持按账号与租户划分的 Idempotency-Key、结果查询与向导恢复，避免响应丢失后的重复创建。创建回执引入迁移 `20261106_07`（当前最新为 `20261106_08`）；协议、存储范围与迁移要求见 [第十九步说明](docs/implementation-steps/19-agent-creation-recovery.md)。

第二十至二十五步已补齐向导完整草稿恢复、文本参考附件、执行生成文件、共享数据库 Worker 心跳与告警、备份恢复演练，以及离线包和部署预检。当前迁移 **20261106_08**；升级与角色授权见 [运行说明](docs/pms-sso-onboarding.md#第二十至二十五步运行要求)，各步实现、验证和独立文章见 [分步目录](docs/implementation-steps/README.md)。本机 Python 3.12/PostgreSQL 16 已验收；生产 Linux 和远程服务需另做目标环境验证。
