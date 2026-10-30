# DeepSeek Harness Agent 的 Session，用来做什么的？

![DeepSeek Harness Agent 的 Session，用来做什么的？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/9c1c2647a90e443283873a317b73e8c2.png)

平时说“会话”，我们很容易想到聊天记录：用户说了一句，模型回了一句，按时间存下来就行。

到了 Agent 这里，事情多了不少。模型可能连续调用工具，命令可能已经执行但结果还没写回来，一次请求可能失败后重试，上下文也可能被压缩。进程如果刚好在中间退出，只保存用户和模型的文字，很多问题就回答不了：工具到底有没有开始？当前 Step 是否结束？下次请求应该带上哪些消息？

DeepSeek Harness 把这些问题交给 Session。它记录 Agent 运行过程中产生的事件，再从这些事件生成模型历史、恢复状态和其他需要的视图。

## 聊天记录回答不了 Agent 的运行问题

一次 Agent 任务里，真正发生的事情不只有“提问”和“回答”。

Agent Loop 会打开 Turn 和 Step，模型返回 Assistant Message，Tools Runtime 开始执行 Tool Call，工具完成后写回 Tool Result。模型请求还带着 provider、model、工具定义和其他配置。任务结束时，也要留下 `completed`、`aborted` 或 `error` 之类的原因。

这些内容有些会再次发给模型，有些只用于还原执行过程。比如模型需要看到工具结果，却不需要看到 `step/start`；排查一次中断时，又必须知道 Step 和 Turn 有没有正常闭合。

如果把它们全塞进一张“聊天消息表”，模型上下文和运行记录就混在了一起。哪些内容对模型可见，哪些内容只是运行状态，很难说清。Session 因此先保存完整事件，再决定不同消费者分别看到什么。

## Session 把运行过程追加成事件

当前源码里的 [`SessionEventMap`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/types.ts#L281-L428) 定义了核心事件：`turn/start`、`turn/end`、`step/start`、`step/end`、`user/message`、`assistant/message`、`tool/call`、`tool/result`、`request/header` 等。插件还可以通过 TypeScript 的声明合并扩展新的事件类型。

事件进入 Session 时，会经过 [`Session.append()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/index.ts#L720-L771)。这段逻辑会检查数据能否无损表示为 JSON，按照当前 Log 长度生成连续的 `seq`，冻结事件内容，再追加到内存日志。追加成功后，Session 通过 `session/event` 通知监听器。

这里没有“找到原记录再更新”的过程。已经接受的事件留在原来的位置，后面的变化继续写成新事件。就算上下文压缩以后旧消息不再发给模型，旧事件也不会从 Log 里被直接改掉。

这种记录方式让每一个结果都能找到前面的原因。`tool/result` 可以对应到 Tool Call，`turn/end` 可以对应到前面的 Turn，恢复时也能按 `seq` 从头检查事件是否连续。

![Session Event Log 怎样支持模型历史、恢复和分叉](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/90b30f09f7234b61b659fa3a9b46f391.png)

## 模型看到的历史，是从事件中生成的

Session Log 记录得很全，但模型不需要看到所有内容。Harness 在 Log 之上维护了一个 Surface，可以理解为“当前仍然对模型有效的消息序列”。

[`deriveMessages()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/index.ts#L842-L863) 沿着 Surface 中保存的事件序号生成 `Message[]`。默认会进入模型历史的是 `system/message`、`developer/message`、`user/message`、`assistant/message` 和 `tool/result`。对应的转换规则在 [`deriveEventMessage()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/surface.ts#L120-L158) 中。

`turn/start`、`step/end`、失败的 `assistant/attempt` 和 `request/header` 仍然留在 Log 里，但不会变成模型消息。`tool/call` 事件也用于记录工具确实开始执行；模型看到的 Tool Call 则来自已经记录的 Assistant Message。

这样一来，Session 同时保留两种信息：一份完整的运行事件，一份当前模型可见的消息历史。两者来自同一个 Log，却不需要使用同一种结构。

Surface 也不只支持追加。上下文压缩需要替换一段旧消息时，新事件会带上 `surfaceOp: replace`，让旧节点退出后续模型输入。原来的事件仍留在 Log 中，因此“模型现在看到什么”和“过去实际发生过什么”可以同时保留。

插件拥有的事件如果需要改变模型历史，可以通过 [`registerMessageProjection()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/index.ts#L924-L932) 注册 Projection。这里的 Projection，就是根据事件生成消息视图的规则。恢复或分叉时缺少对应规则，Session 会拒绝继续推导，避免同一份记录在不同环境里被解释成不同内容。

## 下一次模型请求也能从 Session 里还原

模型消息只是一次请求的一部分。用的哪个模型、暴露了哪些工具、输出上限是多少，同样会影响模型得到什么结果。

Agent Loop 会把这些非消息配置写入 `request/header`。它保存一份规范化的请求头，其中包括模型调用配置、适配器默认值和当时组装出的 Tool Schema。系统提示词则作为 `system/message` 留在 Surface 中，实际路由信息放在 `request/context`。

需要重新构造请求时，[`foldRequestHeader()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/request-header.ts#L63-L69) 从事件流中取得最近一次有效 Header，再与 `deriveMessages()` 得到的消息历史组合。

因此，Session 不只是给界面展示旧对话。Agent Loop 的下一次请求也依赖它：前面接受了哪些消息、工具返回了什么、当时有哪些工具定义，都要能从记录中重新得到。Session 本身不调用模型，它负责准备一份可重建的输入依据。

## 写进 Session，不等于已经落盘

核心 `dsh-session` 管理的是内存中的 Session。持久化由单独的插件接入，这个边界在官方包说明里写得很明确：后端订阅 `session/event`，再把事件交给当前 Session 的写句柄。

持久化层的 [`SessionHandle`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/session/session-persistence/src/handle.ts#L59-L117) 提供 `read`、`append`、`flush` 和 `close`。`append` 保证批次顺序连续；真正要求数据能够在崩溃后保留下来时，需要等待 `flush` 这个持久化屏障。

这层拆分让 Session 的热路径不必等待磁盘 I/O，也让存储后端可以替换。但它也带来一个容易忽略的边界：只创建内存 Session，不代表它会自动持久化。必须由 Agent Loop 取得写句柄，并挂载具体的 Persistence Provider，事件才会进入存储。

进程重启以后，Agent Loop 会先通过写句柄读取已经完整保存的事件。如果日志停在一个没有闭合的 Turn 中间，[恢复逻辑](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/index.ts#L816-L890) 会调用 `interruptedTurnClosers()`，为没有结果的 Tool Call 补充中断结果，再补齐 `step/end` 和 `turn/end { interrupted }`。

它不会假装上次任务正常完成，也不会直接从半个 Step 接着运行。先把已经发生的部分整理成一段结构完整的历史，新的 Agent Loop 才从这个基础上继续。

## 同一份事件记录还能用于分叉和检查

因为 Session 保存的是有顺序的事件，分叉不需要复制某个模糊的“当前状态”。[`SessionStore.fork()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/index.ts#L1237-L1255) 选择一个事件边界，复制这段前缀，并为子 Session 加入继承标记。边界如果落在尚未结束的 Turn 中，`buildForkSeed()` 还会把子分支里的 Step 和 Turn 补齐。

读取、回放和界面展示也可以从同一份事件流生成各自需要的结果。模型历史使用 Surface，持久化后端保存事件，其他消费者可以生成标题、轨迹或统计数据。它们不需要各自维护一套容易互相冲突的会话状态。

代价也很直接。事件只追加，日志会持续增长；Surface 替换、插件 Projection 和请求头折叠都需要稳定的解释规则；Session 格式发生变化时，还要维护相邻版本之间的迁移。官方持久化接口目前也没有删除和保留策略，这些不属于 Session 自动处理的范围。

回到开头的问题，DeepSeek Harness Agent 的 Session 主要做了几件事：记录消息和运行事件，生成模型下一步看到的历史，为持久化与中断恢复提供连续记录，并支持从指定事件位置检查、回放或分叉。

聊天记录只是其中一个结果。Session 真正保存下来的是这次 Agent 交互怎样一步步运行到现在。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/stop-condition) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/plugin-system)
