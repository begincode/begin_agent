# DeepSeek Harness：Skills、MCP 和插件分别解决什么问题？

![DeepSeek Harness：Skills、MCP 和插件分别解决什么问题？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/d9cad75bd9a94d63a645abbc94135631.png)

给 Agent 增加一项能力时，很容易在 Skills、MCP 和插件之间犹豫。

它们看起来都在做“扩展”：Skill 可以告诉模型怎样完成任务，MCP 可以把新的工具交给模型，插件也可以注册工具、提示词和各种运行逻辑。只看最终效果，三者都可能让 Agent 多会做一件事。

但在 DeepSeek Harness 里，它们进入运行时的位置并不一样。Skill 影响模型怎样理解和安排任务，MCP 把进程外的能力转换成 Harness 工具，插件则直接参与 Runtime 的组成和生命周期。

把这三个位置分清楚，很多设计问题也会跟着变得具体：操作说明该放在哪里，外部系统怎样接进来，哪些规则必须由运行时执行，而不能只写给模型看。

## Skill 给模型补充做事方法

Harness 中的 Skill 不是一段启动时全部塞进系统提示词的长文本。

它有自己的注册表 `ctx.skills`。文件系统、本地目录、随包内容或其他 Provider 都可以向注册表提供 Skill。注册表先通过 `list()` 得到名称、描述和调用策略等摘要，真正需要使用时，再通过 `get()` 读取完整内容。[官方 Skills 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.zh.md#摘要候选项与完整定义)把这两种数据明确分成了 `SkillSummary` 和 `SkillDefinition`。

这种拆分直接影响模型上下文。`dsh-tool-skill` 在模型请求前注入的目录只包含可调用 Skill 的名称和描述，不包含正文、绝对路径或 Provider 信息。模型判断某个 Skill 与任务有关以后，再调用 `skill({ name })`，完整的 Markdown 指令才进入工具结果。[`tool-skill` 的实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/tool-skill/src/index.ts#L77-L252)同时检查当前 Agent 的 Scope、工作目录和调用策略，避免把“目录里看得到”和“当前可以加载”混成一件事。

所以 Skill 的主要作用，是把某类任务的做法交给模型：什么时候使用哪些能力、执行顺序是什么、需要检查什么、输出应该采用什么格式。它解决的是模型在当前任务里“应该怎么做”。

Skill 可以引用脚本、参考资料和资产，但加载 Skill 本身不会自动执行这些内容，也不会绕过工具的权限和执行管线。模型读到一条“修改文件前先运行检查”的指令，和 Runtime 强制每次修改前都完成检查，是两种不同的保证。

## MCP 把外部能力接入 Tools Runtime

MCP 处理的不是做事方法，而是能力从哪里来。

Harness 的 MCP Client 连接配置好的 Server，读取它公布的工具列表，为每个工具生成稳定的公开名称，再把远端 Schema 和调用方法包装成 Harness 的 `ToolDefinition`。在 [`syncTools()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/src/tools.ts#L113-L162) 里，这些定义最终仍然通过 `ctx.tools.register()` 进入工具注册表。

这一步很关键。MCP 工具没有在 Harness 旁边另开一条执行通道。模型看到的是普通工具 Schema，发出的 Tool Call 也会回到同一套 Tools Runtime，继续经过 Scope 解析、执行前策略、Guard、审批、结果处理和 Session 记录。

MCP Client 负责处理协议边界上的问题：连接和重连、工具发现、名称映射、请求超时、取消信号以及结果转换。远端工具名会被保留下来用于实际的 `tools/call`，面向模型的名称则带有 Server 命名空间。工具列表更新时，新的定义先完整构建，再替换旧的一代；注册冲突会回滚本次更新，避免模型只看到半套工具。[MCP Client 的设计说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.zh.md#理解实现)对这些约束有完整说明。

MCP 也不只有工具。Server 返回的 instructions 可以加入系统提示词，资源则由 `list_mcp_resources`、`list_mcp_resource_templates` 和 `read_mcp_resource` 三个共享工具按需读取。不过当前实现并不支持 MCP Prompt 模板和资源订阅，[资源模块](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-resources/README.zh.md#已知限制与延后工作)也不会提前把所有资源内容放进上下文。

从 Runtime 的角度看，MCP 是一个适配层：外部 Server 提供协议能力，MCP Client 把它们转换成 Harness 能管理的工具、提示词片段和资源访问入口。

## 插件改变正在运行的 Runtime

Harness 插件的位置更靠里。

插件拿到的是 Cordis `Context`，可以提供 Service、监听 Event、注册 Tool 或 Skill Provider，也可以创建带清理函数的 Effect。它不仅能增加一个模型可调用的能力，还能参与模型请求、工具执行、Session 处理和 Agent 生命周期。

第六篇提到的 `inject`、Fiber、Effect 和 Scope，都属于这一层。`inject` 决定插件依赖哪些 Service，Fiber 管理加载和卸载，Effect 撤销事件监听、工具注册或其他资源，Scope 决定注册内容对哪些 Agent 生效。[官方 README](https://github.com/deepseek-ai/deepseek-harness/blob/master/README.md)所说的“everything-is-a-plugin”，指的正是 Runtime 由这些插件共同组装，而不是存在一个固定核心，再在外围挂几段扩展代码。

Skills 和 MCP 本身也说明了这种关系。提供 `ctx.skills` 的注册表、文件系统 Skill Provider、面向模型的 `skill` 工具，都是插件；MCP Client 同样以插件形式管理连接，再把发现到的定义交给 `ctx.tools`。插件不是 Skills 和 MCP 的第三种替代方案，它是两套子系统能够进入 Harness 的组合机制。

![Skills、MCP 与插件在 Harness Runtime 中的位置](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/e0fcc6693d9e4338b8b50088cdf6f09e.png)

## 它们在不同位置配合

把一次外部操作拆开，就能看出三者的分工。

假设模型需要通过一个远端服务处理文档。Skill 可以描述什么情况下使用这项能力、调用前检查哪些输入、调用后怎样核对结果。MCP Server 暴露实际的文档工具，MCP Client 把它转换成 `ToolDefinition`。插件负责挂载 MCP Client、Skill Provider 以及可能需要的运行策略，并让这些注册跟随作用域和生命周期生效。

模型加载 Skill 后决定发起哪一次 Tool Call；Tools Runtime 再执行由 MCP 接入的工具。Skill 不需要知道传输怎样重连，MCP Server 也不负责决定 Harness 在工具执行前采用哪些 Guard。

这是一条组合关系，而不是三选一：

| 机制 | 进入 Harness 的位置 | 主要负责 | 不负责 |
|---|---|---|---|
| Skill | 模型上下文 | 任务方法、步骤、检查项和输出约定 | 强制执行权限或直接调用外部服务 |
| MCP | 工具与资源协议边界 | 发现并调用外部能力，转换 Schema 和结果 | 改写整个 Agent Runtime 的运行流程 |
| 插件 | 进程内 Runtime | 提供 Service、参与 Event、管理注册与生命周期 | 自动替代模型需要理解的任务说明 |

## 能力应该放在哪里，看它需要哪种保证

一段内容如果只是帮助模型选工具、安排步骤、保持输出格式，放进 Skill 比写死在插件代码里更合适。目录只暴露摘要，正文按需加载，也避免每次请求都携带所有操作说明。

一项能力如果已经存在于独立进程或远端系统中，并且希望以标准工具 Schema 暴露给 Agent，MCP 提供了清楚的协议边界。Harness 负责把发现结果接入 Tools Runtime，外部 Server 负责实现真实操作。两边不需要共享同一种编程语言，也不必把服务实现搬进 Harness 进程。

如果需求必须在模型调用之前或工具执行期间稳定生效，就要落到插件和 Runtime 机制里。比如注册 Service、增加 `tools/pre-execute` 策略、包裹 `tools/execute`、维护连接状态或在卸载时释放资源。这些行为需要明确的调用时机和生命周期，单靠一段模型指令无法保证。

还有一种常见情况是三部分都需要。一个完整扩展可以用插件挂载 MCP Client 和 Skill Provider，用 MCP 提供真实工具，再用 Skill 告诉模型怎样组合这些工具。拆开以后，操作知识、协议接入和运行机制各自有独立的变化边界。

## 放错位置，问题通常不会立刻出现

把强制规则只写进 Skill，正常演示可能完全没问题。到了上下文压缩、Skill 没被加载或模型没有按预期理解时，规则就可能失效。Skill 能提高模型遵循流程的概率，但它不是 Tools Runtime 的拒绝条件。

反过来，把一整套任务说明都塞进工具描述，工具虽然能被调用，模型每次请求却要携带冗长 Schema，使用说明也会和远端协议一起变化。Harness 特意让 Skill 目录只放摘要、正文按需加载，就是在避免所有知识常驻上下文。

把外部能力直接写成进程内插件也不是做不到，但这意味着连接、重连、命名、协议结果和卸载都要由插件自己处理。MCP Client 已经把这组边界集中起来；绕过它以后，得到的不只是更直接的调用，也多了一套需要维护的适配逻辑。

插件本身也有代价。它能进入 Runtime 内部，自然会增加 Service 依赖、Event 顺序、Scope 和清理过程的排查成本。只有需要参与运行机制的部分，才值得放到这一层。

回到开头的问题，Skills、MCP 和插件都能扩展 Agent，但扩展的对象不同。

Skill 补充模型完成任务所需的方法，MCP 把进程外能力转换成 Harness 工具，插件负责这些能力怎样进入并参与 Runtime。把任务知识、外部执行和运行机制分开以后，它们仍然可以一起工作，同时也不必互相承担不擅长的职责。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/tool-call) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/subagent)
