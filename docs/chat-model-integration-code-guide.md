# 当前模型 API 接入说明

当前代码已经从“同一个兼容适配器加四组地址”改为“数据库模型配置 + 四个协议适配器 + 公共 HTTP/SSE 内核”。真实供应商验证状态单独记录，不能由配置是否存在推断。

## 调用路径

```text
配置页面录入供应商、模型、地址、API Key
  → POST/PATCH /api/v1/model-profiles
  → ModelProfileService
  → CredentialCipher 加密
  → PostgreSQL model_profile

对话页面选择 profile_code
  → POST /api/v1/chat 或 /api/v1/chat/stream
  → 读取数据库模型配置，校验启用状态
  → 解密密钥并结束数据库读事务
  → ModelGateway 按 provider_code 选择 Adapter
  → 官方 HTTPS API
  → 统一响应/事件
```

## 代码位置

| 路径 | 职责 |
|---|---|
| `app/models/model_profile.py` | 配置表与不可变 profile_code |
| `app/services/model_profiles.py` | 配置新增、修改、启用状态、调用配置读取 |
| `app/core/credentials.py` | Fernet 加密、持久主密钥读取 |
| `app/model_gateway/adapters/deepseek.py` | DeepSeek 请求与错误策略 |
| `app/model_gateway/adapters/glm.py` | GLM 参数、tool_stream、业务码 |
| `app/model_gateway/adapters/minimax.py` | MiniMax 思考、长度字段、业务码 |
| `app/model_gateway/adapters/kimi.py` | Kimi 型号差异、采样与推理回传 |
| `app/model_gateway/adapters/openai_compatible.py` | HTTP POST、SSE、增量组装、完整性校验 |
| `app/model_gateway/gateway.py` | 供应商到适配器映射、取消时关闭连接 |
| `app/model_gateway/domain/chat.py` | 请求、响应、消息、工具定义、事件 |
| `app/model_gateway/capabilities.py` | 具体型号可用的思考模式、级别和输出能力，随配置列表返回 |
| `app/api/chat.py` | 配置管理、真实连接测试、对话 API |
| `web/src/App.vue` | 配置表单、思考模式与级别选择、流式切换、思考面板、用量、上下文隔离 |
| `scripts/verify_model_providers.py` | 从数据库读取配置并逐家真实调用 |
| `scripts/verify_generation_controls.py` | 经本机应用验证各级别、流式与非流式、思考分片和最终响应一致性 |

## 供应商差异

- DeepSeek：显式思考开关；思考模式不能使用 required 工具选择；思考模式下拒绝无效的温度配置；读取缓存命中与推理 Token。
- GLM：Flash 不允许关闭思考；温度在 0～1 且最多两位小数；启用历史推理保留；有工具时开启 tool_stream；将 sensitive、network_error 等结束原因视为失败。
- MiniMax：使用 adaptive thinking 和 reasoning_split；把内部 max_tokens 转换为 max_completion_tokens；校验 base_resp 业务错误；终帧完整 message 与已输出 delta 对齐，避免正文重复。
- Kimi：K3 不发送 K2 的 thinking 参数，K2.7/K2.6 使用对应配置；校验固定温度和推理强度；回传完整 assistant 推理与工具消息；输出限制使用 max_completion_tokens。

当前重点覆盖 README 中的默认型号；不能把同一家的任意模型都视为已验证。

## 响应与结束

流式事件：`start`、`reasoning`、`content`、`usage`、`tool_call`、`done`、`error`。

`done.response` 提供组装后的完整 `message`、`finish_reason`、用量及供应商请求 ID。`length` 表示输出截断，不是成功完成业务任务。用量缺失保持 null，不伪造为零。

页面将 `reasoning` 事件追加到可折叠思考面板，`content` 事件追加到正文，完成后用完整响应校准。非流式走独立 JSON 接口，等待期间显示“等待完整回复”。显示的是供应商实际返回的思考信息；未返回时明确提示，停止或失败的部分内容保留展示但不进入后续成功历史。每条回复记录发送时的模式、级别和流式状态，修改当前控件不会改写历史标签。

工具参数按 index 拼装，在获得完整合法 JSON 对象后才发出 `tool_call`。未知工具的实际执行、Schema 业务校验和权限校验属于未来 ToolExecutor，当前不会自动执行工具。验证脚本只执行一个无副作用的本地整数加法示例。

无效 JSON、异常结束原因、只有 [DONE] 没有 finish_reason、未完成的工具参数、网络中断不会变成成功 done。支持 CRLF、SSE 多行 data 和心跳。连接取消时关闭上游，供应商已产生的费用是否停止取决于其服务。

## 凭据与数据库

- API Key 仅通过模型配置表单提交，数据库存储 `api_key_ciphertext`。
- 加密主密钥不与密文存入同一个数据库，不进入 Git。
- 已保存配置不会回显密钥，修改时省略 `api_key` 保留原值。
- Pydantic 校验错误不回显输入；上游错误消息不直接返回或写入日志。
- 配置只允许各供应商官方 HTTPS 域名和已知 API 基础路径；不跟随重定向，不向未知主机转发密钥。
- 本机开发或管理令牌保护配置写入；完整登录和租户授权尚未实现。
- Alembic 迁移：`20261023_01`，新增 model_profile。

## 验证层次

1. `tests/test_model_adapters.py`：四家各自的请求、流、推理、工具参数、错误、超时、中断和取消，使用模拟供应商响应。
2. `tests/test_model_profiles.py`：真实 PostgreSQL 中的密文、CRUD、重启读取、停用、管理保护以及 API 到四家适配器的链路；模型响应仍为模拟。
3. `scripts/verify_model_providers.py`：真实外部 API，读取数据库配置，覆盖文本、多轮、流式、Tool Call/结果回传。缺少配置时必须记录 BLOCKED。

参见 [适配与验证报告](./model-provider-adaptation-verification.md)。
