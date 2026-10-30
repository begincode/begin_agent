# DeepSeek Harness 的插件化是怎么实现的？

![DeepSeek Harness 的插件化是怎么实现的？](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/acab12f302e14c62b473e581ca67fe07.png)

Agent Runtime 很容易越写越大。

刚开始只有模型调用，后来加上工具、Session、文件系统、Shell、审批和持久化。每增加一种能力，主流程就多一段初始化代码和一批直接依赖。等到想替换模型适配器，改动往往会一路碰到 Runtime 核心。

DeepSeek Harness 选择了另一种组织方式。官方把它称为“everything is a plugin”：模型适配器、工具注册表、Session、Agent Loop，甚至负责加载其他插件的模块，本身都在同一棵插件树里运行。

但把代码拆成多个包，还不能叫插件化。插件怎样找到依赖、参与流程、限制生效范围，又怎样在卸载时清理注册项，这些才是实现时要处理的问题。

Harness 底层使用 Cordis，把这些问题放进 Context、Service、Event、Fiber 和 Scope 几个机制里处理。

## 插件不是外挂，而是应用本身

普通应用通常先有一套固定主程序，再留几个扩展点给外部插件。Harness 的结构不太一样。它启动时先创建一个空的 Context，再根据 Profile、Bundle 和补丁文件组装插件树。

Profile 决定使用哪些 Bundle，Bundle 带来可以继续覆盖的插件配置，Loader 再把配置项挂到运行中的插件树。官方[架构说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md#profiles-and-bundles)里提到，`web`、`headless`、`sdk` 和 `acp` 这些运行形态，底层仍然是不同的插件组合。

插件可以是函数、带 `apply()` 的对象或者 Service 子类。它们最终都会由 [`ctx.plugin()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/registry.ts#L293-L335) 创建一个 Fiber，并获得自己的 `ctx`。

所以 Harness 不是运行起来以后再“附加”插件。当前应用具备哪些能力，本来就由这棵插件树决定。

## Context 让插件只依赖能力名

插件之间当然还要协作。Agent Loop 需要模型、Session、工具和系统提示词，如果仍然直接导入这些模块的具体实现，拆成插件也只是换了文件位置。

Cordis 的 Context 可以理解为运行期的能力目录。Service 在 Context 中占用一个稳定名称，例如 `ctx.llm`、`ctx.sessions`、`ctx.tools`。消费者只通过这个名称取得能力，不需要知道背后是哪一个实现类。

Service 子类调用 `super(ctx, name)` 后，实例会被注册到 Context。相关逻辑在 [`Service`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/service.ts#L11-L58) 和 [`provide()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/reflect.ts#L267-L304) 中。这个注册归当前插件所有，卸载时会一起撤销。

[Tools Runtime](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/tools/src/index.ts#L807-L859)声明依赖 `systemPrompt`，再以 `tools` 为名称进入 Context。Agent Loop 则依赖 `agents`、`sessions`、`llm`、`tools`、`systemPrompt` 和 `sessionProjections`。[这组声明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/src/index.ts#L330-L372)没有绑定具体模型或持久化实现。

这才是能力能够替换的基础：稳定的是 Service 名称和接口，变化的是由哪个插件提供实现。

![DeepSeek Harness 插件从组合到运行和卸载的机制](https://www.begincode.net/img/640/uploads/courses/deepseek-harness-architecture-analysis/2026/10/df518747280c494fb2d0d873112bf896.png)

## inject 决定插件什么时候启动

插件写在配置文件的前后位置，并不能保证启动顺序。Cordis Loader 会并发挂载配置项，真正决定插件什么时候进入运行状态的是 `inject`。

插件通过 `inject` 声明自己需要哪些 Service。Fiber 创建以后会逐项检查这些 Service 是否已经存在：依赖不完整时保持 `PENDING`；全部满足后进入 `LOADING`，执行插件代码，再转为 `ACTIVE`。这段判断在 [`Fiber._refresh()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/fiber.ts#L611-L638) 中完成。

Service 被卸载或替换时，Cordis 还会通知依赖它的 Fiber。消费者先卸载，等新实现出现后再重新加载，不会继续持有已经失效的旧对象。

这种做法省掉了手工排列初始化顺序，也支持运行期替换。不过它有一个容易误判的表现：缺少依赖的插件会停在 `PENDING`，不会执行 `apply()`。如果只盯着业务日志，很容易把“正在等待依赖”当成“插件没有被加载”。

## Service 和 Event 处理两种协作

Service 适合直接调用。Agent Loop 要执行工具，就调用 Tools Runtime；要生成模型请求，就使用 LLM Service。调用方清楚自己需要哪种能力，也期待一个明确结果。

Event 处理的是另一类关系：某个阶段发生了，但生产者不应该知道有哪些插件会参与。Harness 把模型请求、Agent 状态和工具执行过程都暴露成有类型的事件。插件通过 `ctx.on()` 注册监听器，事件生产者继续按照约定的模式分发。

Event 不全是普通广播。Cordis 提供 `emit`、`parallel`、`serial`、`bail` 和 `waterfall`，用来约定是否等待、是否返回结果以及能否中断后续处理。分发逻辑集中在 [`EventsService`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/events.ts#L99-L238)。

例如工具执行前后的扩展点使用 Waterfall。监听器可以调用 `next()` 把控制权交给下一个插件，也可以直接返回结果，结束后面的链路。这样策略插件能够拒绝一次调用，转换插件也能包住后续执行，而 Tools Runtime 不需要导入任何具体插件。

Service 提供可调用的能力，Event 提供可参与的过程。Harness 同时使用两者，避免所有扩展都挤进一组回调接口。

## Fiber 把注册和清理放在一起

插件能被加载，还不算难。真正容易出问题的是卸载。

插件注册了事件监听器、工具、提示词片段、定时器或者文件监听以后，如果卸载时漏掉其中一个，旧逻辑还会继续运行。热更新几次，同一个监听器甚至可能被重复注册。

Cordis 给每次插件加载创建一个 Fiber。插件通过 `ctx.effect()` 创建资源，并返回清理函数。Fiber 保存这些函数，卸载时按注册的逆序启动清理；多个异步清理可能并行收尾。`ctx.on()`、Service 注册和 `ctx.plugin()` 本身也建立在 Effect 之上，监听器、服务和子插件都会跟随所属 Fiber。[`Fiber.effect()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/vendor/cordis/src/fiber.ts#L402-L560)负责这组动作。

这也是配置热更新的前提。旧插件先释放注册内容，新插件再按当前依赖加载。热更新沿着同一套生命周期完成卸载和重建。

如果多个资源存在严格的清理顺序，插件仍然需要把它们放进同一个 Effect，自己按顺序释放。Fiber 能保证资源有人负责，但不会替插件猜测业务上的先后关系。

## Scope 让同一个 Runtime 装出不同 Agent

Context 中的全局工具和提示词会被所有 Agent 看到，但实际使用时，经常只想给某个 Agent 增加一项能力。

Harness 在 Cordis 之上增加了 Agent Scope。创建 Agent 时，Agent Loop 会调用 [`createScope()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/scope/src/index.ts#L137-L147)，为这个 Agent 创建一个带有 Scope Key 的子 Context。通过 `agent.ctx` 注册的工具和提示词会进入对应的 Scope Layer；事件监听器则带着这个 Context，由 [`scopeTarget()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/scope/src/index.ts#L170-L184) 在分发时过滤。

读取工具和提示词时，`ScopedLayers` 会合并全局层、当前 Scope 及其父级注册项，局部同名能力可以覆盖全局能力。Agent 销毁时，Scope 背后的 Fiber 被释放，独有注册项也随之撤销。[`ScopedLayers.effect()`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/scope/src/store.ts#L226-L266)把可见范围和生命周期所有权放在同一个 Context 上。

这里还要区分 Cordis 的 `isolate()`。`isolate()`为某个 Service 名称创建独立的解析空间，可以让两个插件组使用不同的 Service 实例；Agent Scope 管的是工具、提示词和事件等注册项对哪个 Agent 可见。它们都处理“范围”，但不是同一层机制，也都不能自动替代权限校验或进程隔离。

## 插件化也把复杂度带到了运行时

新增模型提供方、工具或持久化后端时，Harness 通常增加 Service Provider，或者在已有 Event 上注册行为，不必继续修改 Agent Loop。

代价也很具体。一个能力从哪里来，不再只看 import；要结合当前 Profile、Bundle、补丁、Service 依赖和 Scope 才能确定。Waterfall 监听器的注册顺序会影响执行结果，忘记调用 `next()` 还可能截断其他插件。缺少 Service 时，Fiber 可能长期停在 `PENDING`。Scope 层级越多，排查“为什么这个 Agent 看得到，另一个看不到”也会更费劲。

因此，DeepSeek Harness 的插件化不是一个单独的 Plugin 接口。Profile 和 Bundle 决定装哪些插件，Context 提供能力目录，Service 建立稳定依赖，Event 开放运行过程，Fiber 管理加载与清理，Scope 控制注册项的生效范围。

这些机制合在一起，才让模型、工具、Session 和 Agent Loop 能够在不互相写死的情况下组成一个正在运行的 Agent。

---

[返回课程目录](https://www.begincode.net/courses/deepseek-harness-architecture-analysis) · [上一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/session) · [下一章](https://www.begincode.net/courses/deepseek-harness-architecture-analysis/chapters/tool-call)
