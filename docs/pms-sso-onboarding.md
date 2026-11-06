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

系统状态页 `#/integrations` 提供数据库迁移、Worker 心跳分类和当前租户队列的手动采样；要求 `tenant.manage`，队列额外要求 `tasks.read`。Worker 心跳包含主机信息，兼容旧格式；只检查本机共享数据目录且可观察的 PID。不同主机或独立容器不能依靠 API 所在环境的 PID 判断远端 Worker，就绪命令应在 Worker 自身运行环境执行。最多扫描 256 条、每条 16KiB，超过上限会提示统计不完整。页面诊断不执行重试、恢复或外部通知。详见[第十七步运行诊断](implementation-steps/17-system-diagnostics.md)。

生产使用迁移账号、API 账号、Worker 账号三个独立角色。迁移账号拥有 DDL；API 是非超级用户、NOBYPASSRLS，业务表有 FORCE RLS，事务开始设置可信的 `app.tenant_code`。API 不能使用迁移账号，否则生产启动拒绝。

Worker 需要从全部租户任务中领取，使用独立非超级用户 BYPASSRLS 账号，权限只限所需表/序列；不能把这个 DSN 配给 API。租户、用户、成员目录承担授权入口，按服务端身份筛选；运行业务表使用租户过滤、复合外键和 RLS 三层保护。

任务采用至少一次执行。租约令牌阻止旧 Worker 提交结果，模型调用不占数据库事务；模型请求可能在崩溃后重复。内置工具提供文本统计和 UTC 时间；MCP 使用官方 SDK 的 Streamable HTTP，服务器管理员通过 `AGENT_MCP_ALLOWED_HOSTS` 明确允许主机。服务检测发现工具后，在 Agent 版本中授权具体工具和参数 Schema。外部工具可能重复调用，接入有写入副作用的服务需要下游幂等保障。根任务预算限制整棵调用树，Run/Step/Message 记录具体过程；Token 用量缺失时采用保守字节估算。

文本产物保存在 JSONB；第十四步增加人工上传文件及私有本地/S3 对象存储，配置与限制见下文。独立向量库或工作流不在本版范围。

## 文件产物与私有对象存储（第十四步）

支持成功任务追加人工上传文件，默认单文件 20 MiB，服务器可通过 `AGENT_ARTIFACT_MAX_FILE_BYTES` 调整至最多 100 MiB。文件不是模型输入附件，不自动加入对话上下文。文本产物继续使用既有 JSONB；文件仅保存元数据和私有对象引用，同样受不可变触发器约束。

默认 `AGENT_ARTIFACT_STORAGE_BACKEND=local`，对象目录为 `AGENT_DATA_DIR/objects`，本地开发未指定数据目录时为 `.data/objects`。可用 `AGENT_ARTIFACT_LOCAL_DIR` 指定版本目录之外的持久化绝对路径。对象目录不能映射为 Web 静态目录。多 API 实例须挂载同一持久卷，并使用相同运行账号；备份时同时保存 PostgreSQL、对象目录及已有凭据主密钥。

S3 兼容后端配置：`AGENT_ARTIFACT_STORAGE_BACKEND=s3`、`AGENT_ARTIFACT_S3_BUCKET`、`AGENT_ARTIFACT_S3_REGION`，可选 `AGENT_ARTIFACT_S3_ENDPOINT`。非本地环境要求 HTTPS。外部配置可以提供 `AGENT_ARTIFACT_S3_ACCESS_KEY` / `AGENT_ARTIFACT_S3_SECRET_KEY` / 可选 `AGENT_ARTIFACT_S3_SESSION_TOKEN`，未提供时由官方 SDK 解析受控运行身份。桶须预先存在、禁止公开访问，账号仅授予指定桶前缀的 GetObject/PutObject/DeleteObject；DeleteObject 只用于失败补偿。服务器须支持 SigV4、路径式寻址和 If-None-Match 条件写入。本轮没有登记、创建或调用远程桶。

对象键使用 `tenant/{tenantCode}/task/{taskCode}/{artifactCode}`，每个产物全新编码，文件名不参与路径计算。下载经应用权限校验与完整性检查后代理为附件，不返回存储端点、桶、对象键或预签名链接。上传与下载以临时文件中转，主机须为临时目录配置空间与访问权限；代理须允许相应请求体大小、上传最长 120 秒和下载时长，避免缓存下载响应。浏览器下载会先读取 Blob；当前适用于有限大小的交付文件，不提供分片、断点续传或内嵌预览。

切换默认后端只影响新文件，旧文件按保存的后端读取。本地历史目录和 S3 历史桶配置需要保留；不要通过直接修改数据库里的不可变产物来迁移。更换桶或目录时须另做受控迁移方案。

数据库与对象存储没有跨系统事务。数据库写入失败、任务变化、会话撤销和请求正常取消会补偿删除本次未挂接对象；提交回执不明且数据库不可用时保留对象，日志输出产物编码供核对。进程强制终止、网络错误回执不明仍可能留下孤儿对象。管理员须在停止写入或保留足够宽限期后，对照数据库 `task_artifact.content.storage` 的引用清单与对象清单核对，仅处理确认未引用的对象；不可按对象年龄直接删除，不可删除数据库仍引用的历史文件。自动清理与生命周期回收未包含在本步。

## Agent 创建回执迁移（第十九步）

新版本期望迁移 `20261106_07`，在停止 API/Worker 写入、备份数据库和既有凭据主密钥后，由迁移角色执行 `alembic upgrade head`。该版本新增 agent_creation_receipt（租户 FORCE RLS、唯一键、外键及不可变更新/删除约束），不改既有业务数据。API 运行角色补此表 SELECT、INSERT，以及生成 identity 所需序列 USAGE；使用实际角色名授权，不给 API DDL、BYPASSRLS 或超级用户。Worker 不需要写此表。迁移后启动 API/Worker，检查 `/readyz` 及 `python -m app.workers.readiness`。

创建接口 Idempotency-Key 为可选；相同账号、租户、键及规范化内容返回原结果，首次 201、重放 200。同键不同内容 409；无键不提供创建去重保证。查询接口仍需 agents.manage，404 不代表原请求一定失败。调用方应保留原键重试，不能因超时自动换键。回执只保存原创建响应及摘要，不保存完整编辑草稿；向导刷新后不自动保存默认配置。

回执无 TTL 或自动清理，禁止按年龄清除；删除或降级删表会使原请求失去去重依据。备份与恢复须同时覆盖回执和关联 Agent；恢复操作需另行核对数据一致性。本轮本地升级前的私有备份 `.data/local-run/before-step19.dump` 权限 0600，目录验证通过，未演练完整恢复。详细验证与边界见[第十九步说明](implementation-steps/19-agent-creation-recovery.md)。

## 第二十至二十五步运行要求

当前迁移为 **20261106_08**。新增 worker_pulse 和租户 FORCE RLS 的 diagnostic_acknowledgment。迁移后由受控账号按 `deploy/grant-runtime-roles.sql` 对明确命名的 API/Worker 角色授权；脚本不创建密码。API 为 NOSUPERUSER/NOBYPASSRLS；Worker 为独立 NOSUPERUSER/BYPASSRLS，读取迁移表，写任务、运行、轮次和数据库心跳。正式 Worker 启动发现迁移不匹配即失败。主密钥、数据与日志目录必须独立于 release。

第二十步完整向导内容仅保留于按账号/租户划分的 sessionStorage，24 小时、128KiB；其中包含用户提示词，界面明确告知，模型密钥不保存。成功提交清除，关闭标签页或存储拒绝后离开页面不保证恢复。

第二十一步输入附件仅支持最多三个 UTF-8 txt/md/csv/json，单个 32KiB，文本合计 30000 字符；保存在任务 JSONB 快照，模型读取参考材料，原始字节经任务权限下载。不是任意文档解析，也不执行文件代码。

第二十二步 export_text_file 需作为工具显式授权到 Agent 发布版本；每次根任务最多三份文本文件，单个 32KiB。执行期间暂存，成功时私有对象元数据与任务状态原子提交；确认失败的对象补偿删除，提交不明且数据库不可用时保留对象供核对。生成文件与人工上传在页面区分。

第二十三步数据库心跳每十秒上报、有效窗口 45 秒，覆盖同一数据库实例；不把活跃等同于模型或工具就绪。告警确认按账号/租户记录，新样本重新提示，不修复任务、不外发消息；队列仍要求 tasks.read。

### 备份与恢复演练

先停 API/Worker 写入，使用受控备份/迁移角色（不可使用租户 API DSN）。备份包含项目 public schema、本地对象和解密主密钥，目录 0700、文件 0600；必须按敏感资料保管，不上传公开目录。`AGENT_BACKUP_DATABASE_URL` 可通过受控配置提供备份角色；不要把含密码 DSN 放进命令行或日志。

```bash
python -m scripts.backup_restore backup /private/backup/new-bundle --writers-stopped --pg-bin /usr/bin
python -m scripts.backup_restore verify /private/backup/new-bundle
python -m scripts.backup_restore restore /private/backup/new-bundle \
  --database begin_agent_restore_rehearsal --data-directory /private/restore/new-data --pg-bin /usr/bin
python -m scripts.audit_objects --verify-content --output /private/reports/object-audit.json
```

恢复只允许本机全新限定前缀数据库和全新目录。恢复期间的 clean 只对刚创建的空库执行，已有库拒绝覆盖。核对工具不删除对象；达到上限、远程引用或损坏路径时明确标记未完成。含 S3 引用不接受此本地完整备份流程，必须配合桶版本、云端备份和数据库恢复点制定单独方案。完整恢复演练在隔离库完成，不代表生产回切。

### 构建与预检

```bash
python -m scripts.build_pms --offline-deps
python -m scripts.deployment_preflight --output /private/reports/preflight.json
# 仅用于本机演练，Linux 差异会保留为非必需检查，不冒称 Linux 已验收
python -m scripts.deployment_preflight --local-rehearsal --output /private/reports/preflight-local.json
```

离线包含 deploy 模板和 build-runtime.json，记录 OS/架构/Python 完整版本、迁移及锁定依赖摘要。必须在目标兼容环境构建；本轮的 macOS/arm64 wheelhouse 不能直接部署到 Linux。Linux/Python 3.12/PostgreSQL 16 的正式预检必须全部通过，随后仍需 HTTPS/代理前缀、持久化、备份和实际业务验收。

PMS API 命令可调用 `sh deploy/pms/api.sh`，由平台提供 PMS_PYTHON、PMS_PORT、可选 PMS_ROOT_PATH；Worker 运行用 `sh deploy/pms/worker.sh run`，就绪用 `sh deploy/pms/worker.sh check`。仍需 PMS 配置支持命令式 Worker 检查，未对远程 PMS 登记或发布。

本轮在本机真实 Python 3.12.13/PostgreSQL 16、独立非超级用户 API/Worker 角色下执行完整测试和离线包启动；Linux、远程云桶、外部业务 MCP 与正式 PMS 发布仍待环境。HTTP 压力工具仅接受 loopback、私有会话文件和有界读取，结果不能作为生产吞吐承诺。详见[第二十五步](implementation-steps/25-deployment-readiness.md)。
