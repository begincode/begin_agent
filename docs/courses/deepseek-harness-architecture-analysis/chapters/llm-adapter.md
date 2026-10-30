# DeepSeek Harness：不同模型为什么能接进同一套 Agent Loop？

![DeepSeek Harness：不同模型为什么能接进同一套 Agent Loop？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/4ec13ae9f3644761a006e1e9ffaded94.png)

给 Agent 换一个模型，看起来只是把 `provider` 和 `model` 改掉。

真正接过模型 API 就知道，事情没这么简单。各家的请求字段、消息角色、Tool Call 格式和流式事件都不一样。有的接口单独返回 Reasoning，有的把工具参数拆成很多段；结束原因、Usage、错误结构和请求回放需要的数据也各有一套。

如果这些差异直接进入 Agent Loop，循环里很快就会出现大量 Provider 判断：这家怎样拼请求，那家怎样解析流，遇到第三种模型又加一条分支。模型接得越多，Agent 的核心流程反而越难保持稳定。

DeepSeek Harness 把这部分放进了 LLM Runtime 和 Adapter。Agent Loop 只面对一套请求与响应协议，Adapter 再把这套协议翻译成具体模型能够理解的格式。

## Agent Loop 只使用一套 LLM 词汇

Agent Loop 发起模型请求时，使用的是统一的 [`GenerateOptions`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/types.ts)。里面有 `provider`、`model`、消息历史、工具定义、温度、最大输出长度、停止条件和取消信号。请求是发给 DeepSeek 还是其他 Provider，不会改变 Loop 认识的字段。

消息也有统一结构。`Message` 区分系统、用户、助手和工具结果，消息内容再拆成不同的 `ContentBlock`：文本、Reasoning、图片、文件、Tool Call，以及运行过程中对工具集合的增删。

这样做不是为了造一套更漂亮的数据类型，而是让 Agent 的后续逻辑不用理解具体协议。Loop 只关心助手返回了哪些内容块、有没有 Tool Call、为什么结束，以及这次请求用了多少 token。

接口的核心也很小。[`LlmAdapter`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/index.ts#L208-L291)真正要求实现的只有 `stream()`：接收统一请求，返回统一的异步 `StreamChunk`。模型列表、模型解析、价格和重试策略都有扩展位置，但不会成为接入一个 Provider 的硬前提。

## 一次调用会先固定路由和模型能力

统一接口不等于拿到 `provider` 后直接查一个 Adapter 就开始请求。Agent Loop 在发送前会调用 [`prepareCall()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/index.ts#L933-L991)，让 `LlmRuntime` 先准备好这次调用。

这一步会解析 Provider 和 Model，找到负责该路由的 Adapter，同时固定模型信息、默认配置、重试策略、输入模态，以及系统提示词和工具更新的处理方式。返回的调用句柄只能使用一次，也不能拿去发送另一份请求。

Harness 支持运行期替换 Adapter。如果模型信息来自旧实例，请求却在稍后发给新实例，同一次调用的准备和执行就会使用两套规则。`prepareCall()` 捕获当时的 Adapter 代际，让配置、能力判断和真正发送请求属于同一个实现。

Adapter 的注册同样按路由处理。[`registerAdapter()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/index.ts#L389-L418)要求一组路由要么全部注册成功，要么不产生部分结果；替换和卸载也由对应的 Fiber 生命周期管理。Agent Loop 因此不需要保存 Provider 客户端，更不需要跟踪哪个实例正在生效。

## 发送之前，Runtime 还会整理模型输入

模型能力不同，不能只靠 Adapter 在发送阶段随机应变。进入 Provider 之前，`LlmRuntime` 会先把统一消息处理成当前路由可以接收的形态。

[`adapterStream()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/index.ts#L1041-L1130)会把文件内容块转换为文本形式的只读句柄。目标模型不接受图片时，图片也会变成文本表示。工具集合在运行中发生过增删，还会根据当前路由声明的 `toolUpdate` 模式，保留动态更新，或者转换成它能够处理的消息历史。

这里的“转换”可以理解为一次能力投影：保留 Agent 想表达的内容，再按目标模型支持的输入形式生成请求视图。它不会改写 Session 中原来的消息，只影响这次模型调用。

经过这一步，Adapter 拿到的已经是一份与目标能力匹配的 Harness 请求。它接下来负责协议细节，而不是重新决定 Agent 应该看见什么。

## Adapter 负责请求和响应两次翻译

以官方的 [`DeepSeekAdapter`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-deepseek/src/adapter.ts)为例，请求侧会把 Harness 的消息、工具和生成参数序列化成 DeepSeek Messages API 的 HTTP 请求，处理认证和多模态输入，再建立 SSE 流。

响应侧则反过来，把 Provider 返回的增量事件翻译成 Harness 的 `StreamChunk`。网络错误、超时和取消也要转换成 LLM 层认识的失败信息。

`llm-pi-ai` 走的是另一条实现路径。它借助 pi-ai 对接多种 Provider 协议，再由 [`toStreamChunks()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-pi-ai/src/stream.ts#L142-L234)把 `text_*`、`thinking_*`、`toolcall_*`、完成和错误事件转成同一套流式事件。

两种 Adapter 的内部结构可以不同，最终交给 Runtime 的语言必须相同。新增 Provider 时，变化留在 Adapter 这一侧；Agent Loop、Session 和工具执行流程不需要跟着增加分支。

![DeepSeek Harness LLM Adapter 数据流](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/63c0fde4e76b4634b11e56be067c8e9f.png)

## StreamChunk 统一的是增量过程

只统一最终文本还不够。Agent 需要边接收边展示内容，也要知道一段 Tool Call 从哪里开始、参数怎样增长、什么时候可以安全执行。

Harness 的 [`StreamChunk`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/types.ts#L452-L464)是一组封闭的事件：`block-start` 创建内容块，文本、Reasoning 和 Tool Call 各自用 delta 追加内容，`block-end` 关闭内容块，随后是 Usage 和最终的 `finish`。

与内容块相关的流事件都带 index。即使 Provider 把文本和工具参数交错返回，Runtime 仍能把增量放回正确的位置。Tool Call 参数在流里保持原始 JSON 字符串，不会在参数尚未接收完整时提前解析。

这套协议还规定了结束顺序：Usage 出现在 `finish` 之前，`finish` 之后不能再有事件。Adapter 可以在迭代时抛出异常，也可以返回错误结束；`LlmRuntime` 对外会把失败归一成终止块，区分普通错误和取消。上层消费流时，不需要同时维护多套 Provider 异常路径。

## 增量响应要组装以后才能交给 Agent Loop

流式事件适合传输，不适合直接写入消息历史。一次响应过程中，Harness 同时维护两份结果。

[`AssistantStreamAccumulator`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/assistant-stream.ts)记录带时间信息的增量过程，并合并连续的小 delta，形成可以持久化和回放的 stream record。[`BlockAssembler`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/assembler.ts#L38-L208)按 index 组装内容块，收集 Usage、结束原因和 Provider 回放数据，生成 `AssistantMessage`。

Agent Loop 中的 [`AssistantStreamAttempt`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/assistant-stream.ts)把两者放在同一次尝试里：新 chunk 到达时，既推送给实时监听者，也进入组装器；只有消息成功追加到 Session 后，才把这次流标记为已经提交。

组装阶段还负责阻止不完整行为继续向下游传播。如果因为 `max-tokens` 截断，尚未完整生成的 Tool Call 会被丢弃；用户中断请求时，只保留已经形成的文本和 Reasoning，不把半截工具参数交给 Tools Runtime。模型输出可以逐 token 到达，工具执行的入口仍然要求内容块完整。

消息提交后，Agent Loop 才检查其中的 Tool Call。没有 Tool Call，可以进入本轮结束判断；有 Tool Call，则继续走同一套工具解析、检查、执行和结果记录流程。不同模型的分流，到这里已经结束了。

## 统一接口没有丢掉 Provider 差异

抽象层最容易走向两个极端：上层完全依赖某家 Provider，或者为了统一而丢掉有用的专属信息。Harness 选择把差异保存在明确的位置。

助手消息的 `source` 会记录 Provider 和 Model。Adapter 还可以在 `finish` 中附带 [`ReplayEnvelope`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm/src/types.ts#L430-L442)，保存响应级或与内容块对齐的私有数据。后续请求仍由同一个 Adapter 实例处理时，这些数据可以用于协议回放；路由已经换到另一个实例时，`LlmRuntime` 会去掉私有回放数据，只保留通用消息内容，避免把一种协议的内部状态传给另一种实现。

Usage 也不会被压成一个简单 token 总数。它作为独立流事件进入组装结果，供 Session、计费和观测逻辑继续使用。错误则被归一为 `LlmFailure`，保留状态码、请求 ID、Provider 建议的等待时间和图片卸载提示等可选信息。

一次 Adapter 调用只代表一次 Provider 尝试。是否重试，由 Agent Loop 的 `agent/request-error` 扩展点决定；失败的尝试会先写入 Session，再开始下一次请求。这样，Provider 负责描述发生了什么，Agent 运行策略负责决定接下来怎么做。

这套设计的代价也很明确。统一协议一旦增加新的内容块或结束语义，Adapter、组装器、UI、Compaction 和回放逻辑都可能需要一起更新。Provider 新能力如果无法落进现有类型，也不能只在某个 Adapter 里偷偷处理，否则上层根本不知道它存在。

但它换来的边界更稳定：Agent Loop 决定任务怎样继续，LlmRuntime 负责路由和能力处理，Adapter 负责具体协议，Assembler 把增量还原成 Agent 能使用的消息。接入不同模型时，需要变化的是翻译层，而不是整套 Agent 执行流程。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/context-compaction) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/runtime-hosts)
