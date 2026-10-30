# DeepSeek Harness：Web、Headless、SDK 和 ACP 为什么能共用一套 Runtime？

![DeepSeek Harness：Web、Headless、SDK 和 ACP 为什么能共用一套 Runtime？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/f53efe868ad247cf9b28862b98bd8c8f.png)

同一个 Agent，既可以在浏览器里连续对话，也可以从命令行接收一个任务，跑完以后退出。它还能被 Python 或 TypeScript 程序调用，或者通过 ACP 接进编辑器和其他 Agent Client。

这几种入口看起来差别很大。Web 要处理 HTTP、WebSocket 和页面状态；Headless 要把答案写到 stdout；SDK 与 ACP 都走 stdio，却使用不同的协议和会话控制方式。

如果每种入口都各自搭一套模型、工具、Session 和 Agent Loop，功能很快就会分叉。工具修复要改四遍，Session 格式升级要维护四条路径，安全策略也可能因为入口不同而出现偏差。

DeepSeek Harness 没有按界面复制 Runtime。它把共同能力放进共享组合，把入口差异放进 Host 组合，再用 Profile 决定一次进程怎样把它们装起来。

## 共用 Runtime，不等于使用同一份配置

Web、Headless、SDK 和 ACP 共享的是 Runtime 的能力模型与启动机制，不是四个进程读取完全相同的配置。

官方随附的 [`PROFILE_TEMPLATES`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/app-boot/src/profile.ts#L179-L195)直接写出了这层关系：`web`、`headless`、`sdk` 和 `acp` 都先加载 `@deepseek-ai/dsh-base`，然后分别追加 `dsh-web-app`、`dsh-headless`、`dsh-sdk-app` 或 `dsh-acp-app`。

`dsh-base` 提供模型适配器、Agent Loop、工具、Session 持久化、沙箱、审批、设置和凭据等公共能力。后面的模式 Bundle 再加入当前入口需要的服务器、命令行处理和少量默认值。[官方架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.zh.md#profile-%E4%B8%8E%E7%BB%84%E5%90%88%E5%8C%85)把运行中的 `dsh` 描述成一棵由这些层按顺序叠加出来的插件树。

所以“共用一套 Runtime”指的是使用相同的核心服务、事件和生命周期，再组合出不同的应用表面。每个 Profile 仍有自己的配置结果。

## Profile 先决定这次进程是什么应用

`dsh` 没有为四种形态分别维护四个产品入口。启动时通过 `dsh --profile web`、`dsh --profile headless`、`dsh --profile sdk` 或 `dsh --profile acp` 选择 Profile。

Profile 位于 `$DSH_HOME/profiles/<name>`。它的 `package.json` 记录有序 Bundle 列表，自己的 `cordis.patch.yml` 保存用户修改和额外插件。第一次使用随附 Profile 时，[`loadProfile()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/app-boot/src/profile.ts#L789-L806)会根据模板初始化目录，然后再读取真实 Profile。

这一步让“应用类型”从可执行文件转移到了组合数据里。选择 Web，不是进入另一份 Agent 实现，而是选择一组包含 Web Host 的 Bundle。选择 Headless，也不是绕过 Runtime 直接调用模型，而是在同一个 Runtime 上挂一个一次性任务驱动器。

## Bundle 把公共能力和 Host 差异分开

Bundle 不是一个已经运行的服务。它是 Cordis 配置项及挂载代码的分发格式，`package.json` 中的 `dsh.bundle.patch` 指向一份或多份 Patch 文件。

[`loadProfileDirectory()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/app-boot/src/profile.ts#L740-L774)会按声明顺序解析 Bundle，检查版本兼容性，读取 Patch，再组成有序的配置层。无法解析的 Bundle 会被记录并跳过。

公共能力放在 [`dsh-base`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/base/README.zh.md)。这个包本身几乎没有运行逻辑，它的主体是一份静态 Patch：把 LLM、Agent、Tools、Session、文件系统、Shell、安全策略和其他服务插入配置树。

模式 Bundle 只补各自需要的部分。Web 增加浏览器应用、服务端路由和客户端模块；Headless 增加一次性 Runner；SDK 增加 Harness 自己的 JSON-RPC Server；ACP 增加符合 ACP 约定的 Bridge。共同能力仍由 Base 提供，Host 不再重新实现 Agent Loop。

`sdk-minimal` 是一个有意保留的例外：它不加载 `dsh-base`，而是由 `dsh-sdk-minimal` 提供完整的精简配置树。源码没有假定所有入口必须共享 Base；需要小型运行环境时，也可以换成另一套完整组合。

## App Boot 把配置层变成运行中的插件树

Profile 和 Bundle 到这里还是静态描述。真正启动 Runtime 的是共享的 App Boot。

CLI 的 [`runProfile()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/src/profile-boot.ts#L271-L353)先组合 Profile 和调用级 Patch，创建启动环境、退出控制器与 `ProfileContext`，然后调用同一个 `boot()`。Profile 名称、已启动 Bundle、工作目录和 Overlay 会作为启动事实提供给插件，但不会变成另一套核心类型。

[`boot()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/app-boot/src/index.ts#L1200-L1266)创建 Cordis `Context`，挂载 Loader，再把根配置加载成插件树。它会等待插件结算，并审计已启用的必需入口。必需插件没有成功激活时，启动会释放整棵树，不留下一个缺少入口的进程。

这段流程也统一了生命周期。模型客户端、Session 写入器、网络监听器和协议连接由各自 Fiber 持有，关闭时回到根 Context 的释放过程。

![DeepSeek Harness Profile 与 Host 组合关系](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/e62d89779a744b8c962bc250c8ecb14d.png)

## 四种 Host 接管的是入口、输出和退出

插件树启动以后，四种形态的主要区别落在 Host 与外部世界的连接方式上。

[`dsh-web-app`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/web-app/src/index.ts#L186-L261)提供前端资源和 Web 应用地址。它等待 Loader 结算以后，才打印认证 URL 或打开浏览器。浏览器通过 API 和流式连接驱动 Agent。

[`dsh-headless`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/headless/src/index.ts#L411-L420)从启动器取得 `appExit`，把命令行任务交给 Runner。它把最终答案或结构化事件写到输出，并在任务结束后映射退出码。

SDK Profile 挂载 [`dsh-sdk-jsonrpc-server`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sdk/server/src/index.ts#L45-L101)。它通过 stdin/stdout 接收 JSON-RPC 请求。`initialize` 返回前等待 Loader Tree 就绪，`shutdown` 排空传输和根 Fiber。

ACP 使用相同的 Agent、Session 和 Tools，Host 换成 [`dsh-acp`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/acp/acp/src/index.ts#L97-L441)。它把 ACP 会话方法转换为 Harness 操作，再把事件、状态与审批请求转换成协议更新。

Host 没有接管模型决策和工具执行。它负责把外部请求送进 Runtime，把运行状态送回调用方，并在自己的协议语义下处理就绪、取消和关闭。

## Patch 让相同骨架保留不同默认值

共享 Base 并不要求四种形态表现完全一样。Bundle、Profile、Harness Home 和单次命令都可以继续覆盖配置。

加载顺序是有意固定的：Profile 中的 Bundle 按声明顺序应用，然后是 Profile 自己的 `cordis.patch.yml`、Home Patch，调用时提供的 `--patch` Overlay 再放在上面。后层可以按 id 替换已有条目的完整 `config`，也可以插入新的插件行。

因此 Host 可以改变与交互方式有关的默认值。Web 组合浏览器模块和 Agent Preset；Headless 选择一次任务的输出策略；SDK 与 ACP 为了保证一条 stdio 连接期间的结构稳定，会关闭配置 HMR，也会禁用它们不需要的模型标题生成。

Web Profile 允许配置通过 HMR 重新组合。App Boot 会重新读取 Profile 层，并检查新一代插件是否成功激活。替换的仍是同一棵 Cordis Tree 中的条目。

这里也有一个容易踩到的规则：Patch 替换的是目标条目的整块配置，不做深度合并。只想改一个字段时，也要把希望保留的其他字段重新写出来。组合变得灵活的同时，覆盖内容和层级顺序也成了需要排查的运行条件。

## 复用发生在核心能力，边界留给 Host

这套架构避免了按产品入口复制 Agent Runtime。模型适配、工具执行、Session 格式、持久化和安全策略可以在共享层演进，Web、Headless、SDK 和 ACP 继续使用同一套 Service 与事件。

它并没有消除不同入口的差别。Web 关心浏览器认证和长连接；Headless 关心标准输出和退出码；SDK 维护 Harness JSON-RPC 生命周期；ACP 还要遵守外部协议的能力协商、会话恢复和审批语义。这些逻辑仍需要各自测试，也可能拥有不同的默认配置。

代价主要落在组合系统上。启动结果来自多层 Patch，问题可能出在 Bundle 顺序、条目 id、版本兼容性或依赖等待。功能在不同入口表现不同时，还要检查两边最终组合出的插件树。

Harness 把这份复杂度集中在 Profile、Bundle 和 App Boot，而没有让它扩散进 Agent Loop。一次进程先由 Profile 选出应用组合，App Boot 把组合变成可管理的插件树，共享 Runtime 提供 Agent 能力，Host 再把这些能力暴露成浏览器、命令行或协议接口。入口可以变化，Agent 的运行机制不需要跟着重写。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/llm-adapter) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/security-boundaries)
