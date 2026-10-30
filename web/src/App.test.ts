import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import App from "./App.vue"

const capabilities = { thinking_modes: ["auto", "enabled", "disabled"], reasoning_efforts: ["low", "high", "max"], streaming: true, reasoning_content: true }
const profile = { profile_code: "ui_enabled", provider_code: "deepseek", display_name: "测试模型", model_code: "deepseek-flash", base_url: "https://api.deepseek.com", enabled: true, capabilities }
const profiles = [profile, { ...profile, profile_code: "ui_disabled", display_name: "停用模型", enabled: false }]
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
const sse = (event: unknown) => `data: ${JSON.stringify(event)}\n\n`
const result = (finish_reason = "stop") => ({ message: { content: "完整回复", reasoning_content: "思考信息" }, finish_reason })
const wrappers: ReturnType<typeof mount>[] = []

beforeEach(() => { vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false }))) })
afterEach(async () => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); await flushPromises(); vi.unstubAllGlobals() })

async function boot(handler?: (path: string, options?: RequestInit) => Response | Promise<Response> | undefined) {
  const fetchMock = vi.fn(async (path: string, options?: RequestInit) => {
    const custom = handler?.(path, options)
    if (custom) return custom
    if (path === "/api/v1/models") return json([{ code: "deepseek", name: "DeepSeek", default_model: "deepseek-flash", base_url: profile.base_url }])
    if (path === "/api/v1/model-profiles") return json(profiles)
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const wrapper = mount(App, { attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, fetchMock }
}
function button(wrapper: ReturnType<typeof mount>, text: string) {
  const match = wrapper.findAll("button").find(b => b.text() === text)
  if (!match) throw new Error(`Missing button: ${text}`)
  return match
}

async function send(wrapper: ReturnType<typeof mount>, text: string) {
  await wrapper.get("textarea").setValue(text)
  await button(wrapper, "发送").trigger("click")
  await flushPromises()
}

describe("page interaction states", () => {
  it("explains disabled profiles accurately and prevents sending", async () => {
    const { wrapper, fetchMock } = await boot()
    await wrapper.get(".model-bar select").setValue("ui_disabled")
    await wrapper.get("textarea").setValue("不能发送")
    expect(wrapper.get(".config-hint").text()).toContain("已停用")
    expect(wrapper.get(".config-hint").text()).not.toContain("API Key")
    expect(button(wrapper, "发送").attributes("disabled")).toBeDefined()
    expect(wrapper.get(".connection-state").text()).toBe("已停用")
    expect(wrapper.findAll(".generation-controls select").every(e => (e.element as HTMLSelectElement).disabled)).toBe(true)
    expect(fetchMock.mock.calls.some(([path]) => path.startsWith("/api/v1/chat"))).toBe(false)
  })

  it.each(["save", "verify"])("locks editable fields and navigation during %s", async action => {
    let complete!: (response: Response) => void
    const pending = new Promise<Response>(resolve => { complete = resolve })
    const { wrapper, fetchMock } = await boot((path, options) => {
      if (action === "verify" && path.endsWith("/verify")) return pending
      if (action === "save" && options?.method === "PATCH") return pending
    })
    await button(wrapper, "配置").trigger("click")
    if (action === "save") await wrapper.get("form").trigger("submit")
    else await button(wrapper, "测试已保存配置").trigger("click")
    await flushPromises()
    expect(wrapper.get(".model-config").attributes("aria-busy")).toBe("true")
    expect(wrapper.findAll(".model-config input").every(e => (e.element as HTMLInputElement).disabled)).toBe(true)
    expect(wrapper.get(".model-bar select").attributes("disabled")).toBeDefined()
    expect(button(wrapper, "新增模型").attributes("disabled")).toBeDefined()
    expect(wrapper.get(".form-status").text()).toContain(action === "save" ? "正在保存" : "正在测试")
    await wrapper.get("form").trigger("submit")
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST" || options?.method === "PATCH")).toHaveLength(1)
    complete(json(action === "save" ? profile : { passed: true }))
    await flushPromises()
    if (action === "save") expect(wrapper.find("form").exists()).toBe(false)
    else expect(wrapper.get(".model-config").text()).toContain("单次文本调用通过")
  })

  it("keeps partial content after stop and excludes it from the next request", async () => {
    let calls = 0
    const { wrapper, fetchMock } = await boot((path, options) => {
      if (path !== "/api/v1/chat/stream") return
      calls++
      if (calls > 1) return new Response(sse({ type: "done", response: result() }))
      const stream = new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(sse({ type: "content", delta: "已返回片段" })))
        options?.signal?.addEventListener("abort", () => controller.error(new DOMException("Stopped", "AbortError")), { once: true })
      } })
      return new Response(stream)
    })
    await send(wrapper, "第一次")
    expect(wrapper.get(".message.assistant").text()).toContain("已返回片段")
    expect(wrapper.get(".model-bar select").attributes("disabled")).toBeDefined()
    await button(wrapper, "停止").trigger("click")
    await flushPromises()
    expect(wrapper.get(".message.assistant").text()).toContain("已停止生成")
    expect(wrapper.get(".message.assistant").text()).toContain("已返回片段")
    await send(wrapper, "继续")
    const chats = fetchMock.mock.calls.filter(([path]) => path === "/api/v1/chat/stream")
    expect(JSON.parse(chats[1][1]?.body as string).messages.some((m: { role: string }) => m.role === "assistant")).toBe(false)
    expect(wrapper.findAll(".message.assistant")[1].text()).toContain("完整回复")
  })

  it.each(["error", "length"])("retains partial output and visibly marks %s", async state => {
    const response = state === "error"
      ? sse({ type: "content", delta: "已收到的正文" }) + sse({ type: "error", message: "模拟上游错误" })
      : sse({ type: "done", response: { ...result("length"), message: { content: "已收到的正文" } } })
    const { wrapper } = await boot(path => path === "/api/v1/chat/stream" ? new Response(response) : undefined)
    await send(wrapper, state)
    expect(wrapper.get(".message.assistant").text()).toContain("已收到的正文")
    expect(wrapper.get(".message.assistant .page-error").text()).toContain(state === "error" ? "模拟上游错误" : "回答不完整")
    expect(button(wrapper, "发送").exists()).toBe(true)
  })
})
