# DeepSeek Harness：多个子 Agent 是怎么被 Workflow 组织起来的？

![DeepSeek Harness：多个子 Agent 是怎么被 Workflow 组织起来的？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/2d0ff2bccdeb404ea027d1f9a9d32c8a.png)

只有一两个子任务时，父 Agent 连续调用几次 `subagent`，过程还算清楚。

任务一多，情况就变了。哪些子任务可以同时做，哪些必须等上一步完成，某个子任务失败后还要不要继续，最后又该怎样把十几个结果整理成一个返回值，这些都成了新的调度问题。

如果每个结果都先回到父 Agent，再让模型决定下一步，模型实际上还要兼职做任务调度。调用次数变多以后，父级上下文里会不断加入委派和结算消息，原本可以固定下来的等待关系，也要反复交给模型判断。

DeepSeek Harness 提供了另一条路径：模型写一段 JavaScript 编排脚本，Workflow Engine 负责运行脚本，具体工作仍然交给子 Agent。任务怎么拆由模型表达，并发限制、结果等待、取消和资源释放则交给 Runtime 执行。

## Workflow 没有进入 Agent Loop 主干

Workflow 在 Harness 中是一项可选能力，由 `ctx.workflowEngine` 提供。它不属于 Agent Loop 的固定流程，也不会替换父 Agent 正在执行的 Turn 和 Step。[Workflow 子系统说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/workflow.zh.md)把它定义为一个独立的执行 seam：工具或其他插件提交脚本，引擎返回一次可管理的 `WorkflowRun`。

对父 Agent 来说，`workflow` 仍然是一次普通 Tool Call。Tools Runtime 进入工具执行后，工具再调用 `ctx.workflowEngine.start()`。脚本执行完毕，工具结果回到当前 Step，Agent Loop 才继续下一次模型请求。

这层拆分让 Agent Loop 不需要理解 `parallel()`、`pipeline()`，也不需要知道一次脚本启动了多少个子 Agent。Agent Loop 只等待工具结束；Workflow Engine 管理这次编排；Subagent Runtime 管理每个子级的创建和回收。

![DeepSeek Harness Workflow 组织多个子 Agent 的执行流程](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/8bff41d0df80446e84b7e205af5deb22.png)

## 模型提交的是一段可执行的编排

一次 Workflow 调用主要包含三部分：描述身份的 `meta`、JavaScript `script`，以及可选的 JSON `args`。脚本支持顶层 `await`，最后用 `return` 交出一个 JSON 值。

脚本可以使用几个由 Runtime 提供的函数：

```javascript
phase('review')

const reviews = await parallel([
  () => agent('检查 src/a.ts 的正确性'),
  () => agent('检查 src/b.ts 的正确性'),
])

return { reviews }
```

`agent()` 负责委派子任务，`parallel()` 组织并行工作，`pipeline()` 让每个输入依次经过多个阶段，`phase()` 和 `log()` 用于报告进度。模型写的是任务之间的关系，不需要自己实现子 Agent 的创建协议。

脚本也不是拿来以后立即执行。[`PtcWorkflowEngine.start()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/index.ts#L134-L189)会先校验 `meta`、解析脚本，确认 Subagent Provider 已经注册，并计算本次运行允许创建的子 Agent 总数。请求不合法时，运行还没有发布就会失败，工具可以把具体错误交给模型修正。

Provider、最大并发数、子 Agent 总数和单次组合器的条目上限来自引擎配置。调用方可以降低本次运行的 `maxTotalAgents`，脚本本身不能把这些限制调高。这样，模型可以决定怎么编排任务，却不能在脚本里改写宿主给这次执行设定的上限。

## 脚本里的 agent() 仍然走 Subagent Runtime

Workflow 没有再造一套子 Agent 实现。

在 PTC 进程里，`agent()` 先经过 guest 适配器，再通过 Host Binding 调用 `startChild`。Host 侧的 [`PtcWorkflowRun.startChild()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/host.ts#L200-L229)最终调用的仍然是 `subagents.startActivation()`。

这次 Activation 会带上固定的父 Agent、Workflow 捕获的工作目录、选定的 Provider，以及脚本传入的 Prompt、模型和结构化输出 Schema。子级启动后，Workflow 保存 `callId` 与 `childId` 的对应关系，再单独等待 Activation 的结果。

子 Agent 完成时，结果先回到脚本。没有 Schema 时，`agent()` 返回子级的最终文本；提供 Schema 时，它返回经过 Provider 校验的结构化值。普通子 Agent 没有正常完成，`agent()` 返回 `null`，由脚本决定丢弃、保留还是继续处理。[`WorkflowExecution.agent()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/runtime.ts#L163-L229)同时保证结果读取完成后释放本次子级句柄。

这里和普通 `subagent` 工具的结果路径不同。普通委派会把 `subagent-settled` 消息交给父 Agent；Workflow 的子级结果由脚本收集，不会逐条进入父级对话。父模型最后看到的是 Workflow 汇总后的返回值。

## parallel 和 pipeline 把等待关系写进代码

`parallel()` 接收一组没有参数的函数，并用 `Promise.all` 等待它们。返回数组的顺序由脚本里的任务顺序决定，不取决于哪个子 Agent 先完成。[实现中的异常处理](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/runtime.ts#L285-L308)也有明确区分：普通任务错误变成 `null`，参数错误、超限和 Provider 故障等致命 `WorkflowError` 会终止整个脚本。

`pipeline()` 处理的是另一种关系。每个输入项会依次经过多个阶段，不同输入项之间可以同时推进。某个输入在一个普通阶段出错，只会让这一项变成 `null`，并跳过它剩余的阶段；致命错误仍然结束整个 Workflow。[这段实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/runtime.ts#L311-L340)没有设置跨输入的阶段屏障，因此一个输入完成第一阶段后，可以直接进入自己的第二阶段。

脚本表达了并行，不代表子 Agent 可以无限启动。每次 `agent()` 都要先获取并发槽，[`acquireSlot()` 与 `releaseSlot()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/runtime.ts#L143-L160)用先进先出的等待队列控制同时运行的数量。除此之外，还有一次运行最多创建多少个子 Agent、一次 `parallel()` 或 `pipeline()` 最多接收多少项等限制。

因此，脚本负责描述依赖关系，Runtime 负责让这些关系在给定容量内运行。模型不需要在每个子任务结束后重新推理一次“接下来轮到谁”。

## 父 Agent 收到的是一次 Workflow 结果

前台执行时，[`workflow` 工具](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/tool-workflow/src/index.ts#L406-L480)会等待 `run.result`。正常完成后，它向父 Agent 返回 `runId`、实际启动的子 Agent 数量和脚本最终返回的 JSON。

运行期间产生的 `workflow/start`、`workflow/phase`、`workflow/log`、`workflow/agent-start`、`workflow/agent-end` 和 `workflow/end` 事件，用于界面和其他插件观察进度。事件携带运行信息，不暴露活动的 `WorkflowRun` 句柄，监听器不能借此取消或释放运行；某个监听器抛错，也只会记日志，不会改变 Workflow 的执行结果。

根级工具调用还会向父 Session 写入运行开始、成员开始、成员结束和运行结束记录。`phase()` 与 `log()` 没有逐条写入前台 Session，它们主要服务于实时观察。父级对话因此不会被每个子 Agent 的中间内容填满，但仍然能够看到这次 Workflow 启动过哪些成员、怎样结束。

工具也支持 `run_in_background`。这条路径不是 Workflow Engine 自己变成持久任务系统，而是 [`startBackgroundRun()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/tool-workflow/src/index.ts#L262-L313)把运行注册到 `ctx.jobs`。工具立即返回 `jobId` 和 `runId`，Job 接管取消和结果交付，`phase()`、`log()` 与成员事件则被镜像到任务输出中。

## 取消以后还要等子级停稳

父 Agent 中止当前 Step 时，Workflow Tool 会把 `exec.signal` 桥接到 `run.cancel()`。后台执行没有这个 Step 信号，它由 `job_kill` 或 Owner 生命周期取消。

[`PtcWorkflowRun.cancel()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/host.ts#L151-L156)会中止 PTC 进程，并开始释放已经创建的子级。但发出取消信号不等于清理已经完成：有些子 Agent 可能正在启动，有些 Activation 已经发布，还有一些 Host Binding 调用尚未返回。

所以 [`drive()` 的 finally](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/workflow/workflow-ptc/src/host.ts#L282-L316)还会释放已知子级、等待进行中的 Host 调用结束，再处理取消过程中刚刚发布的子级。仍未闭合的成员事件会以 `cancelled` 补齐。最后，工具自己的 `finally` 调用 `run.dispose()`，等脚本进程和子 Agent 都停稳以后，才结束运行记录。

这部分代码看起来比“停止一段脚本”复杂，是因为 Workflow 同时持有两类资源：一个执行 JavaScript 的 PTC 进程，以及脚本已经启动的多个 Subagent Activation。只停掉其中一边，都会留下仍在运行或者没有释放的工作。

## Workflow 适合有明确依赖关系的大批量委派

Workflow 解决的不是所有多 Agent 任务。

只有一两个独立子任务时，直接调用 `subagent` 更简单。任务数量较多，并且并行、分阶段处理和结果汇总关系能够用代码明确表达时，Workflow 才能减少父 Agent 反复调度的负担。

当前实现也有清楚的范围。脚本和中间值没有检查点，进程重启后不能从中间继续；工作流不能嵌套调用另一个工作流；引擎限制子 Agent 数量和并发，但没有统计所有子 Agent 的总 Token 预算。PTC Workflow 也没有自己的整体执行时限，运行会持续到完成、失败或被调用方取消。

它的设计重点，是把两种工作分开：模型负责写出任务拆分和依赖关系，Runtime 负责按照脚本执行、控制并发、收集结果，并在失败或取消后完成清理。

父 Agent 不再逐个接收和调度中间结果，只需要发起一次 Workflow，再处理最终返回值。子 Agent 仍然各自运行，Workflow 把它们之间可以确定的协作关系变成了一段可以执行、可以限制、也可以完整结束的过程。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/subagent) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/context-compaction)
