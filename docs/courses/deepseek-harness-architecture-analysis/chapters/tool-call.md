# DeepSeek Harness：模型发出 Tool Call 之后，发生了什么？

![DeepSeek Harness：模型发出 Tool Call 之后，发生了什么？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/0b695919a4d341838dbf3b4769a52e9d.png)

模型返回一个 `read_file`，再带上一段文件路径，看起来像是它已经开始读文件了。

实际上，这时还没有任何工具代码被执行。模型给出的只是一个结构化请求：调用哪个工具、传入什么参数、用哪个 ID 关联结果。这个请求能不能继续，后面还要经过工具查找、策略检查、审批、执行、结果校验和 Session 记录。

DeepSeek Harness 把这些工作集中放进 Tools Runtime。Agent Loop 负责推进当前 Step，Tools Runtime 负责把一条 Tool Call 处理成一条可交回模型的 Tool Result。

## 模型拿到的是工具说明，不是执行函数

工具插件注册的内容比模型看到的多得多。

当前源码中的 [`ToolDefinition`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L222-L299) 除了名称、描述和参数 Schema，还包含 `execute()`、输出 Schema、结果渲染、超时声明、并发分类和 UI 展示等运行信息。这些函数和策略显然不能直接发给模型。

Tools Runtime 会从注册定义里生成一份模型可见的投影。[`schemas()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1255-L1295)只保留工具名、描述、参数 Schema 和延迟加载标记。`execute()`、`timeoutMs`、输出处理和展示函数仍留在 Runtime 内部。

当前 Agent 能拿到哪些 Schema，还要经过 Scope 解析。全局注册、父级 Scope 和当前 Agent 自己注册的工具会合并，同名工具按较近的 Scope 覆盖。`restrict()` 可以过滤当前 Scope 继承来的工具，这个过滤同时影响 Schema 暴露和后续工具解析；当前 Scope 自己注册的工具不在这个过滤范围内。

Harness 还支持三种呈现方式：`native` 把可见工具逐个交给模型，`ptc` 只暴露 `run_code` 和生成的工具 SDK，`both` 同时提供两种方式。PTC 程序里的子调用仍会进入相同的工具流水线。

模型看到的是 Runtime 根据 Agent、Scope 和呈现模式生成的调用界面，对应的执行函数仍留在进程内。

## Tool Call 先留下记录，再进入执行阶段

模型响应里可能同时出现多个 Tool Call。Agent Loop 会先解析每条调用的参数，再交给工具调度器处理。

一条调用实际开始时，[`startCall()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts#L164-L197)会先向 Session 追加 `tool/call`，然后才调用 Tools Runtime 的 `prepare()`。这意味着审批等待、策略拒绝、工具异常甚至执行前取消，都有一个已经记录的调用与最后的结果对应。

Tools Runtime 随后为这次调用创建执行对象。参数会被复制成可无损表示的 JSON 并冻结，Runtime 再分配一个不透明的执行 Token。插件可以读取工具名、参数、Agent 和取消信号，但不能悄悄改掉调用身份。

[`tools/pre-execute`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1484-L1534)也没有提供参数改写能力。否则 Session 记录的参数、界面展示的参数和工具实际收到的参数可能不一致。

![DeepSeek Harness Tool Call 执行流程](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/12502affa7744fc7a2a34a146dccee21.png)

## 执行前有三种不同的控制方式

工具是否可见、这一次是否允许执行、拒绝结果能不能被后面的插件推翻，是三个不同问题。

`restrict()` 处理可用工具集合。被过滤掉的继承工具不会进入模型 Schema，也无法通过这个 Scope 的工具解析执行。它收窄的是 Harness 工具注册表，不代表外部文件系统、网络服务或业务接口已经完成授权。

`tools/pre-execute` 处理单次调用。它是一个 Waterfall，监听器可以返回 `allow`、`deny`、`cancel` 或 `ask`。`ask` 会转到 Approval Service；只有 `allowed-once` 会让本次调用继续，用户拒绝、请求取消或者没有可用审批通道都会收敛成拒绝结果。[这段映射](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1697-L1768)没有把“暂时无法询问”当成默认放行。

`guard()` 位于 Waterfall 之后。Guard 只能返回一个拒绝原因，或者返回 `undefined` 表示不干预，不能主动给出允许结果。[`guardReason()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1127-L1154)依次检查全局和 Scope 链上的 Guard，只要其中一个拒绝，调用就不会进入工具主体。

这三层的职责很清楚：`restrict` 决定当前 Agent 的工具集合，`pre-execute` 让插件参与本次调用的判断，`guard` 保留一个不会被监听器顺序重新放开的拒绝位置。

## execute 阶段还包着超时和重试等运行策略

通过执行前检查以后，Tools Runtime 也不是马上调用工具函数。

它先进入 [`tools/execute`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1596-L1627) Waterfall。这个阶段包在工具主体外面，适合放超时、重试和指标采集。监听器调用 `next()` 才会进入下一层，最里面才是注册工具的 `execute(args, exec)`。

官方的超时插件就是一个例子。工具可以声明 `timeoutMs`，但 Tools Runtime 本身不会因为这个字段自动终止执行。[Timeout Policy](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/guard/timeout-policy/src/index.ts#L55-L81)在 `tools/execute` 中创建带截止时间的 Signal，再把它传给后面的工具。

这里采用的是协作式取消。工具必须观察或继续传递 `exec.signal`，收到取消后结束自己持有的工作。Runtime 会等待已经开始的 Promise 完全停稳，不会假装已经把同进程代码强行杀掉。工具没有处理 Signal，超时字段也无法把它变成可中断代码。

工具不存在、主动抛出异常、包装层失败或者输出不合法，都会被整理成 `ToolExecutionResult`。普通工具失败不会直接打断整个 Turn，而是形成 `isError: true` 的结果交给下一次模型请求。

## 工具返回值和模型看到的内容分开处理

Harness 要求类型化工具的 `execute()` 返回符合输出 Schema 的 JSON 值，而不是直接拼一段给模型看的文字。

工具主体成功后，[`createSuccessResult()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1820-L1859)会复制并校验这个值，再调用工具的 `output.render()` 生成模型可见的内容。需要给 UI 卡片保留的数据，则可以通过 `presentationMeta()` 单独生成并写入 Session。

这样，规范 JSON 值可以供 PTC 程序继续处理，Content Block 交给模型，Metadata 帮助界面在回放时恢复工具卡片。

工具结果接着进入 `tools/post-execute`。插件可以接受原结果、替换值或内容、补充下一次请求要用的上下文，也可以用反馈把结果改成失败。[`postExecute()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L1771-L1818)完成这些转换后，工具自己的 `finalizeContent()` 还有一次只处理内容的机会，最后才触发只读的 `tools/result` 通知。

工具主体执行完成后，Runtime 仍要确认返回值符合声明，并完成结果策略和内容收尾。

## 工具可以并行，记录仍按模型顺序提交

同一条模型响应可能带回多次工具调用。Harness 不会默认把它们全部并行执行。

每个工具可以通过 `isConcurrencySafe()` 声明某次调用是否能够和兄弟调用重叠。只有明确返回 `true` 的调用才进入并行池；没有声明、判断异常或工具不可见时，都按独占调用处理。独占调用会形成顺序屏障。

Agent Loop 的调度器允许工具主体并行运行，但执行前策略和最终提交保持有序。[`runGroup()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts#L122-L247)把完成结果放进对应位置，只有从当前提交位置开始形成连续结果时，才依次运行后置处理并写入 Session。

后发起的工具可以先完成，却不会抢在前面的 Tool Call 之前进入对话历史。并发没有改变下一次模型请求看到的结果顺序。

取消发生时，调度器会停止启动新工具，等待已经开始的调用收尾，并给尚未执行的 Tool Call 补上失败结果。Session 不会留下只有调用、没有结果的半截记录。

## Tool Result 写入 Session，这次调用才闭合

最终结果确定以后，Agent Loop 会创建模型协议中的 Tool Result 消息，再追加一条 `tool/result` 事件。[`appendToolResult()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts#L269-L290)记录模型可见内容、失败状态、结构化错误信息和可回放的展示 Metadata，并通过原来的 `tool/call` 序号把两条事件关联起来。

工具附带的 `additionalContexts` 会在这一批结果记录完成后进入当前 Turn，成功结果上的 `concludesTurn` 也会交回 Agent Loop 判断。工具本身不直接开启下一次模型请求，也不直接关闭 Turn。

到这里，一条 Tool Call 才算处理完整。模型生成调用意图，Harness 保证调用有明确的输入、执行条件和最终结果，再把结果放回下一步推理能够使用的位置。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/plugin-system) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/skills-mcp-plugins)
