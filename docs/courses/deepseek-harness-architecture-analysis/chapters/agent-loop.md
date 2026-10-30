# DeepSeek Harness：Agent Loop 怎样推进一次任务

![DeepSeek Harness：Agent Loop 怎样推进一次任务](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/55b19370f3a64c9fabf05cbc406e521e.png)

用户让 Agent 查一个项目里的报错。

模型先搜索代码，拿到结果以后读取文件，最后才给出判断。中途用户还可能补一句：“先别改代码，只分析原因。”

从聊天界面看，这仍然只是一问一答。Runtime 里却已经发生了几次模型请求、几批工具调用，还有一条执行过程中进入的新消息。工具结果要进入后续请求，追加要求要找到合适的生效位置，取消也不能抹掉已经展示的内容。

DeepSeek Harness 用 Agent Loop 处理这段过程。理解它的关键，是先分清两个边界：`Turn` 和 `Step`。

## Turn 和 Step 处理的不是同一层问题

在 Harness 的默认实现里，一次 Turn 从 `turn/start` 开始，到 `turn/end` 结束。它通常由一条排队的用户消息启动，但一个 Turn 内可以调用模型多次。

每次模型调用，以及这次响应触发的工具执行，被放在一个 Step 里。Session 类型对 [`step/start`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/types.ts#L286-L289) 的定义很直接：一个 Step 包含一次模型调用和它请求的工具执行。

比如用户让 Agent 查找配置并解释结果，可能形成这样的结构：

```text
Turn 1
├── Step 1：模型决定调用搜索工具
├── Step 2：模型读取搜索到的文件
└── Step 3：模型根据已有结果给出回答
```

三个 Step 共享同一个 Turn，因为它们都在处理同一条用户请求。只有当前 Turn 结束以后，队列中的下一条普通消息才会启动新的 Turn。

Runtime 既要知道“一条请求整体有没有完成”，也要知道“当前这次模型调用进行到了哪里”。取消、重试和工具结果回传，都要落在明确的边界上。

![Turn 和 Step 如何推进一次任务](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/f036ff45340146ba8cfad515548c070e.png)

## 一个 Turn 先打开，再决定要不要进入 Step

[`ReactLoopAgent.turn()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts#L269-L350) 开始时，先向 Session 追加 `turn/start`，然后才从 Inbox 领取消息。

第一次领取会拿走两类输入：所有等待 `next-step` 的消息，以及 `next-turn` 队列中的第一条消息。这里一次只取一条普通消息，所以连续发送的多条 `followup` 不会挤进同一个 Turn，而是按队列依次处理。

消息被领取以后，Agent Loop 组装系统提示词、工具 Schema 和运行时上下文，再触发 `agent/pre-step`。插件可以接纳、替换或拒绝即将进入 Step 的消息。

这时 Step 还没正式开始。如果返回 `reject`，Turn 以 `blocked` 结束；如果第一批消息被改成空数组，Turn 也会关闭。只有消息被接纳，Agent Loop 才写入 `step/start` 并准备模型请求。因此有 Turn 不代表一定有 Step。

## 一个 Step 怎样完成一次模型调用

进入 Step 后，Agent Loop 通过 `agent/request` 让插件调整模型配置，再调用 `prepareCall()` 确认适配器。完成以后，系统提示词和领取的用户消息才会写入 Session，随后从 Session 派生完整历史并请求模型。

`agent/pre-step` 接纳消息，不代表它已经进入历史。如果路由解析期间发生取消，尚未提交的提示词和用户消息不会写入。这项顺序在[官方 Agent Loop 说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.zh.md#%E8%BD%AE%E6%AC%A1%E4%B8%8E%E6%AD%A5%E9%AA%A4%E6%B5%81%E7%A8%8B)中也有明确说明。

模型最终输出会记录为 `assistant/message`。请求失败时，插件可以通过 `agent/request-error` 要求重试。重试仍属于当前 Step，不会重新领取 Inbox，也不会重复提交已经接纳的用户消息。

## 工具调用为什么会产生下一个 Step

模型没有返回工具调用时，当前 Step 得到 `completed`，Agent Loop 开始检查 Turn 能否结束。

如果模型返回了 Tool Call，执行工作交给 [`executeToolCalls()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/tool-calls.ts#L60-L102)。工具可以并行执行，但 `tool/call`、`tool/result` 和附加上下文仍按模型给出的调用顺序提交。

工具结果写入 Session 后，当前 Step 结束。Agent Loop 再开启一个 Step，从 Session 重新派生历史。下一次模型请求已经能看到刚才的 Tool Call 和 Tool Result，可以继续调用工具，也可以生成最终回答。

工具也可以设置 `concludesTurn`，要求当前 Turn 收尾。不过它不会越过已经排队的 `next-step` 消息。工具附带的上下文或同时到达的 steering 仍会先被处理。

## followup、steer 和 inject 从哪个边界生效

任务运行时又收到一条消息，最麻烦的不是把它放进数组，而是决定它应该影响哪次模型请求。

[`ReactLoopAgent`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts#L128-L147) 提供了三种入口：

| 方法 | 进入的队列 | 是否唤醒 Agent | 生效位置 |
|---|---|---|---|
| `followup()` | `next-turn` | 是 | 当前 Turn 结束后的新 Turn |
| `steer()` | `next-step` | 是 | 最近一次尚未领取消息的 Step |
| `inject()` | `next-step` | 否 | 下一次被其他输入推动的 Step |

`followup` 是普通的下一轮请求，运行中先在 `next-turn` 等待。`steer` 用来调整正在进行的任务，在下一个 Step 边界生效；Agent 空闲时，它也会启动一个 Turn。

`inject` 只补充下一次模型请求的上下文，不会唤醒空闲 Agent。之后出现 `followup`、`steer`，或者当前任务本来就会继续，它才会被领取。

![followup、steer 和 inject 的生效边界](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/23965264d1924297967a12c7200c8baf.png)

这里不存在“立刻插入当前请求”。[`Inbox.claim()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/inbox.ts#L103-L114) 在 Step 准备阶段领取一批消息，领取完成以后才到达的输入，只能等待后面的边界。这避免了模型请求已经构造一半，历史内容却又被并发修改。

## Turn 结束前还会再检查一次

模型给出普通回答、达到输出 Token 上限，或者工具要求结束 Turn 时，Agent Loop 会得到一个候选的结束原因。

它不会马上写入 `turn/end`。如果 `next-step` 已经有消息，循环直接继续；如果队列为空，则先触发 [`agent/turn-stopping`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts#L363-L381)。

监听器可以在这个事件里调用 `agent.steer()`。Agent Loop 等监听器执行完，再重新检查 Inbox：出现新的 `next-step` 消息就继续运行，没有新消息才关闭 Turn。

Turn 最终会记录明确的结束原因，包括 `completed`、`max-tokens`、`blocked`、`aborted` 和 `error`。进程崩溃后恢复未闭合的 Turn，还会使用 `interrupted` 补上结束边界。

## 取消和错误也必须留下完整边界

`agent.cancel()` 会中止当前活动，默认也会清空两个 Inbox 队列。模型已经流式展示的文本会作为带有 `interrupted: true` 的 `assistant/message` 保存；尚未分发的工具调用则获得合成错误结果，避免回放时只有 Tool Call，没有对应结果。

请求失败会经过 `agent/request-error`。插件返回 `retry`，Agent Loop 在原 Step 内重试；无人处理，Turn 以 `error` 结束。

无论正常完成、取消还是失败，`step/end` 和 `turn/end` 都会尽量补齐。Session 因而保留的不只是聊天内容，还包括任务走到了哪个 Turn、哪个 Step，以及为什么停下来。

## Agent Loop 把“继续执行”变成了明确判断

如果只看最外层代码，Agent Loop 确实有一个持续运行的循环。但任务不会因为循环还在就盲目继续。

每次推进都有具体条件：Inbox 有没有待处理消息，`agent/pre-step` 是否接纳本次输入，模型是否返回 Tool Call，工具是否要求结束，`agent/turn-stopping` 之后有没有新的 steering，以及取消信号是否已经触发。

这些边界也带来了额外复杂度。消息可能在 Step 领取前后到达；重试不能重复写入用户消息；Turn 准备结束时还可能被插件继续推进。扩展插件分不清这些时机，就可能让消息提前生效、重复生效，或者一直留在 Inbox 里。

回到开头那个查错任务，模型负责决定下一步要搜索、读取还是回答。Agent Loop 负责把每次决定放进可记录的 Step，把处理同一请求的多个 Step 放进 Turn，并在每个边界重新判断任务要不要继续。

最终留下的不是一串随意连接的模型调用，而是一段能够被介入、取消、记录和恢复的执行过程。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/agent-assembly) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/stop-condition)
