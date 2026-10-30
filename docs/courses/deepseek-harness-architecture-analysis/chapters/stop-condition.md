# Agent 一次对话结束的条件应该是什么？

![Agent 一次对话结束的条件应该是什么？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/a9a31b3b2e0748a9abfbf14c6890ef84.png)

让 Agent 帮忙修一个测试，它可能先读代码，再跑测试，然后改文件、重新执行。中间每次模型输出都结束了，但事情显然还没结束。

这正是 Agent 和普通聊天不太一样的地方。普通聊天里，一条回复生成完，这一轮基本就结束了。Agent 的一次输入却可能触发多次模型请求，还夹着工具执行、补充消息和重试。只看“模型有没有说完”，已经判断不了任务是不是该收尾。

在 DeepSeek Harness 里，这段过程被放进一个 Turn。Turn 里面可以有多个 Step，每个 Step 负责一次模型决策和随后产生的工具调用，请求失败后的重试仍留在这个 Step 里。于是“对话什么时候结束”就变成了一个更具体的问题：当前 Step 给出了什么结果，当前 Turn 还有没有工作，最后又该用什么原因关掉它。

## 一次对话，不等于一次模型请求

最常见的路径是这样：用户给出任务后，模型返回 Tool Call，Agent Loop 执行工具，把结果写进 Session，再发起下一次模型请求。模型可能继续调用工具，也可能根据结果给出最终回答。

所以一次模型请求结束，只代表一个 Step 结束。只要模型还在调用工具，工具结果就要交回模型，Turn 还得继续。

这层区分很重要。工具只负责完成一个局部动作，比如读取文件、执行命令或修改内容。它成功了，不代表用户交代的事情已经完成。真正决定后面还要不要继续的是 Agent Loop，而不是某个工具，也不是某次请求的流式输出结束事件。

当前默认实现里的 [`step()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts#L353-L499) 会返回两类结果：返回一个结束原因，表示当前 Turn 出现了结束候选；返回 `null`，表示工具执行完以后还要进入下一 Step。Turn 因此不是简单地跟着一次请求结束，而是根据 Step 的结果逐步推进。

## 模型不再调用工具，只是第一个结束信号

模型返回一条没有 Tool Call 的消息时，`step()` 会给出 `completed`。通常这就是最终回答，所以 Agent Loop 可以准备结束当前 Turn。

这里用“准备结束”更准确。因为 `completed` 只说明当前 Step 没有留下需要继续执行的工具，它还没有证明当前 Turn 里没有别的输入等着处理。

反过来看，只要响应里还有普通 Tool Call，Agent Loop 就会执行工具。工具成功后，结果进入 Session，随后开启下一 Step。哪怕模型在文字里写了“任务已经完成”，只要同一条响应仍然带着工具调用，运行时就不会把这句话当成可靠的结束条件。

这是一种很实用的边界：自然语言可以表达意图，结构化结果才能控制流程。Harness 不去理解“好了”“完成了”这些文字，而是看模型有没有继续发出 Tool Call。

![Agent 一次对话结束前的判断流程](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/393c072c4811426fad14fbb113e2e15d.png)

## 工具可以提出收尾，但不能直接关掉 Turn

有些工具一旦成功，继续请求模型反而没有意义。比如某个工具已经提交了最终结果，后面不需要模型再补一段解释。

DeepSeek Harness 给工具提供了 [`concludeTurn()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L401-L417)。工具调用它以后，这次成功结果会带上 `concludesTurn` 标记，Agent Loop 随之得到一个 `completed` 结束候选。

这个标记有两个很明确的限制。

它跟着本次工具执行结果走，不是一个可以随意修改的全局开关；而且只有成功结果能携带它，[失败结果的类型](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L554-L579)明确不允许 `concludesTurn`。工具执行失败时，模型通常还需要看到错误，决定重试、换一种做法，或者向用户解释，运行时不能把失败顺手当成完成。

更重要的是，工具只能提出“这轮可以收尾”，不能直接写入 `turn/end`。关闭 Turn 的决定仍然留在 Agent Loop。这样一来，工具知道自己的业务动作是否已经完成，Agent Loop 则掌握整轮运行是否还有待处理内容，两边不会互相越权。

## 真正关掉 Turn 前，还要确认没有待处理输入

结束候选出现以后，[`turn()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts#L270-L351) 会先看 `next-step` Inbox。

这里存放的是应该在当前 Turn 的下一 Step 生效的消息。工具执行期间补入的上下文，或者通过 `steer()` 送进来的新要求，都可能已经在里面排队。只要 `next-step` 还有内容，Agent Loop 就不会结束，而是把这些消息带进下一 Step。

当 `next-step` 暂时为空时，Agent Loop 还会触发一次 [`agent/turn-stopping`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts#L363-L381)。监听器可以在这个时点再次 `steer()`。事件执行完以后，Agent Loop 会重新检查 `next-step`；仍然为空，才真正退出循环并记录 `turn/end`。

这相当于把结束拆成了两次确认：当前执行已经可以停，并且当前 Turn 没有新工作。

需要分清的是，`next-turn` 消息不会阻止当前 Turn 收尾。它代表下一轮要处理的输入，当前 Turn 结束后，Agent 可以再开启新的 Turn。只有明确属于当前运行过程的 `next-step` 内容，才会把这轮继续往前推。

## 结束不等于完成，原因必须留下来

一轮对话停下来，不一定是任务做完了。

模型输出达到 `maxTokens` 时，当前 Step 会返回 `max-tokens`。它表示这次输出被上限截断，而不是任务自然完成。并且 `maxTokens` 在官方配置里是[单次请求的输出上限](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.zh.md#%E9%85%8D%E7%BD%AE%E5%A3%B0%E6%98%8E%E5%BC%8F-agent)，不是整个 Turn 的 Token 预算。一次输出被截断后，如果 `next-step` 又出现消息，Turn 仍可能继续；最终结束原因依然保留 `max-tokens`，不会被后面的正常 Step 改写成 `completed`。

取消是另一条路径。[`agent.cancel()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/agent.ts#L149-L155) 会中止当前活动，Turn 以 `aborted` 结束。已经流式输出的内容不会假装从未发生，而是作为被中断的 Assistant 消息留在 Session 里。

请求失败时，`agent/request-error` 可以要求重试；没有监听器接管，Turn 以 `error` 结束。`agent/pre-step` 拒绝进入执行时，原因是 `blocked`。进程崩溃留下没有闭合的 Turn，恢复时还会补上 `interrupted`。

[Session 定义的 `TurnEndReason`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/session/src/types.ts#L200-L224)没有把这些情况压成一个模糊的“已结束”。这是因为结束是运行状态，完成是业务判断。后续恢复、展示或排查时，系统需要知道这轮到底做完了、被截断了、被拦住了，还是执行中断了。

## “可以结束”和“必须停止”是两个问题

把上面的路径放在一起，一次 Turn 自然结束需要满足两个条件：当前 Step 已经给出结束候选，并且 `next-step` 没有待处理消息。随后，Session 还要记录一个明确的结束原因。

这套判断解决的是“现在是否可以结束”。还有另一个容易混在一起的问题：如果模型一直返回 Tool Call，最多允许它执行多少步？

DeepSeek Harness 的默认 Agent Loop [没有内置 Turn 总预算](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.zh.md#%E5%B7%B2%E7%9F%A5%E9%99%90%E5%88%B6%E4%B8%8E%E5%BB%B6%E6%9C%9F%E5%B7%A5%E4%BD%9C)。`maxTokens` 只管一次请求；`agent/turn-stopping` 也只在已经出现结束候选、并且 `next-step` 为空时触发。模型如果持续发出普通 Tool Call，这个事件并不会经过。

因此，默认实现能识别自然完成，也能响应取消、错误和拦截，但不会自动规定“一次 Turn 最多 N 个 Step”。要限制一直运行的工具循环，需要另外选择一个能够观察每个 Step 的位置，再在达到预算时主动取消。这个限制不该伪装成任务完成，它应当有自己的终止原因和记录。

回到标题里的问题：Agent 一次对话结束，不能只看模型有没有停止生成。更可靠的判断是，当前执行已经给出可结束的结果，当前 Turn 里没有新的工作，同时系统能说清楚它为什么结束。至于运行多久以后必须停，那是另一条保护边界，应该单独设计。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/agent-loop) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/session)
