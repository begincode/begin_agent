# Begin Agent：PMS 运行与独立账号

PMS 运行适配依据已核对的 [PMS 应用接入规范](https://www.begincode.net/pms/help?section=onboarding)。用户已取消 SSO；默认使用本平台内置账号，不需要 SSO 应用登记、回调或密钥。PMS 尚未登记，当前先完成代码与本地验收；不增加 PMS 项目/需求回写。

## PMS 应用登记

API 与 Worker 独立登记、独立启停；仓库和构建产物可复用。

| 项目 | 配置 |
|---|---|
| API 运行类型 | FastAPI / Uvicorn |
| Python | `.python-version` 声明 3.12；`pyproject.toml` 要求 ≥3.12 |
| 构建命令 | `"$PMS_PYTHON" scripts/build_pms.py --offline-deps` |
| 唯一产物路径 | `dist/pms-payload` |
| HTTP 入口 | `app.api.main:app` |
| HTTP 启动 | `python -m uvicorn app.api.main:app --host 0.0.0.0 --port <port> --root-path <prefix> --no-access-log` |
| 健康检查 | `<prefix>/readyz`：200 且 `status=ready`；`/healthz` 仅检查进程 |
| 根路径 | PMS 的 `/` 映射为空 root-path |
| 数据库迁移 | 使用独立迁移账号执行 `python -m alembic upgrade head`，启动 API/Worker 前完成 |
| Worker 入口 | `python -m app.workers.agent_worker` |
| Worker 状态 | `python -m app.workers.readiness`；成功输出 `worker ready`、退出 0 |

构建脚本非交互执行 npm ci、完整前端 check、源码资源组装，并在 `--offline-deps` 下生成 `.pms-wheelhouse`；构建执行器与目标主机的 OS/架构及 Python 完整版本须一致。PMS 按 Python 版本契约准备运行虚拟环境，依赖从 wheelhouse 离线安装。`requirements.txt` 锁定运行依赖，不包含开发依赖和本机 editable 路径。构建产物不包含 `.env`、`.data`、截图或其他项目资料。

**Worker 是非 HTTP 服务。PMS 当前通用执行器仍要求 HTTP 健康检查，因此需要配置支持上述 readiness 命令的专用运行/检查模板后才能登记发布。不得填 API 的健康 URL 冒充 Worker 检查。** 本次提供项目侧入口和就绪协议，未修改或部署 PMS 本身。

## 环境、配置与持久化

`AGENT_ENVIRONMENT` 选择 local/dev/test/staging/prod。配置目录由 `AGENT_CONFIG_DIR` 指定，读取 `application-<env>.yml`；也可用 `AGENT_CONFIG_FILE` 指定准确文件。文件为平铺 YAML 属性，支持 `AGENT_*` 键，例子见 `config/`。不存在、格式错误或必要配置不完整时启动失败。PMS 环境属性编辑器生成同名 YAML，使用下列 key；不能假设 YAML 自动导出环境变量。

优先级：构造参数（测试用）→ 进程 `AGENT_*` 环境变量 → 外部 YAML → 本机 `.env` → 默认值。`AGENT_ENVIRONMENT`、`AGENT_CONFIG_DIR`/`AGENT_CONFIG_FILE` 是定位配置的引导变量，由平台模板导出；不得把它们仅写在待加载文件内。

| 配置 | 说明 |
|---|---|
| `AGENT_DATABASE_URL` | API 的 asyncpg PostgreSQL DSN |
| `AGENT_WORKER_DATABASE_URL` | Worker 独立 DSN；本机测试可不设置 |
| `AGENT_AUTH_MODE` | 默认 password；仅 local/test 可显式使用 local 免登录 |
| `AGENT_LOG_DIR` | 可选覆盖；默认 `PMS_LOG_DIR`，写滚动 `agent.log` |
| `AGENT_DATA_DIR` | 可选覆盖；默认 `PMS_SHARED_DIR/data` |
| `AGENT_CREDENTIAL_KEY_FILE` | 默认位于持久数据目录的 `model-credential.key`，API/Worker 共用 |
| `AGENT_CREDENTIAL_ENCRYPTION_KEY` | 可选外部 Fernet 主密钥，替代主密钥文件 |

`PMS_APP_HOME` 存在时，两类目录必须有平台变量或显式绝对路径；禁止位于 `PMS_RELEASE_DIR` 所在 releases 树内。日志和数据独立取默认值，覆盖其中一项不影响另一项；目录不可读写时明确报错。API/Worker 的数据目录与主密钥必须相同，权限限制为运行账号可读；版本切换不改变这些文件。正式环境不会自动生成主密钥：首次部署前须预置有效的 Fernet 密钥文件（0600），或由外部配置提供 AGENT_CREDENTIAL_ENCRYPTION_KEY；不得在每次发布时重新生成。Worker 在数据目录 `workers/` 写 PID、实例号和心跳，进程退出会删除自身状态文件。仅本机独立运行才允许 `.data/`。

外部监听由 Uvicorn `--host/--port` 控制，项目不覆盖命令行；`python -m app.api.main` 仅是本机便捷入口。自定义运行模板声明 `# PMS_PYTHON_PROTOCOL=1` 并使用 `"$PMS_PYTHON"`，遵守 PMS 的版本契约。平台负责 start/stop/restart/status、PID 校验与进程隔离；业务无需另建部署目录树。

## 前缀与代理

代理必须把 `/agent/...` 的 `/agent` 剥离后转给 Uvicorn，Uvicorn 以 `--root-path /agent` 启动。前端资源使用相对地址，HTML 注入运行时 `<base>`，API、登录和退出路径从该 base 得到；同一份构建可以部署到根路径或非根路径。登录页、API 和资源均使用同一应用前缀。浏览器 Cookie 写请求验证同源 Origin；代理必须正确传递受信任的外部 scheme/host。

不要只改变 `--root-path` 而保留另一套代理前缀，也不要在测试/生产原制品内改写 JS。扩展参数不得重复平台标准端口、前缀、配置来源。反向代理仅信任受控转发来源并覆盖客户端伪造的转发头；Uvicorn 的可信代理范围由运行模板限定。

## 内置账号与业务授权

1. 使用 `AGENT_AUTH_MODE=password`，打开应用页面，由使用者设置首个管理员用户名、显示名称及密码。初始化只允许一次，并发请求通过数据库锁串行化；旧租户、模型和历史任务保留，首个管理员加入已有租户。
2. 用户名 3–32 位，密码 12–128 位。密码使用独立随机盐的 scrypt；数据库不保存明文密码。登录令牌不返回到前端存储，使用 HttpOnly、SameSite=Lax Cookie；HTTPS 以及非本地环境强制 Secure。令牌有效期七天，数据库仅保存 SHA-256 摘要。
3. 平台管理员在“平台用户”创建账号，再在“成员”按用户名加入租户、分配角色。提供成员、租户管理员及自定义细粒度权限角色；API 每次请求查询账号状态、成员关系和角色权限。
4. 支持修改本人密码、管理员重置、停用账号、退出当前或全部设备。修改密码撤销其他设备，重置和停用撤销该用户全部会话。禁止停用当前账号，禁止移除最后一位平台管理员和租户最后一位管理员。
5. 登录失败按用户名和来源 IP 在数据库限流，15 分钟窗口内分别最多 8 / 30 次失败。错误不泄露账号是否存在或密码内容；Cookie 写请求要求精确同源 Origin，本机 loopback CLI 可无 Origin。
6. `local` 只用于明确开启的本机开发免登录模式。正式环境不得使用它；平台不依赖任何 SSO 服务。遗留适配器不参与默认登录或用户管理流程。

生产部署应通过受控 HTTPS 代理提供服务。相同版本多实例共享数据库和凭据主密钥，无需共享进程内登录状态。

## 数据库角色与执行语义

生产使用迁移账号、API 账号、Worker 账号三个独立角色。迁移账号拥有 DDL；API 是非超级用户、NOBYPASSRLS，业务表有 FORCE RLS，事务开始设置可信的 `app.tenant_code`。API 不能使用迁移账号，否则生产启动拒绝。

Worker 需要从全部租户任务中领取，使用独立非超级用户 BYPASSRLS 账号，权限只限所需表/序列；不能把这个 DSN 配给 API。租户、用户、成员目录承担授权入口，按服务端身份筛选；运行业务表使用租户过滤、复合外键和 RLS 三层保护。

任务采用至少一次执行。租约令牌阻止旧 Worker 提交结果，模型调用不占数据库事务；模型请求可能在崩溃后重复。内置工具提供文本统计和 UTC 时间；MCP 使用官方 SDK 的 Streamable HTTP，服务器管理员通过 `AGENT_MCP_ALLOWED_HOSTS` 明确允许主机。服务检测发现工具后，在 Agent 版本中授权具体工具和参数 Schema。外部工具可能重复调用，接入有写入副作用的服务需要下游幂等保障。根任务预算限制整棵调用树，Run/Step/Message 记录具体过程；Token 用量缺失时采用保守字节估算。

对象存储尚无指定接口，本版文本产物保存在 JSONB；不声称支持文件上传、独立向量库或工作流。
