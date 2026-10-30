<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue"
import MarkdownContent from "./components/MarkdownContent.vue"
import { apiFetch } from "./api"
const props = withDefaults(defineProps<{ canManage?: boolean; unified?: boolean; embedded?: boolean }>(), { canManage: true, unified: false, embedded: false })

type ProviderCode = "deepseek" | "glm" | "minimax" | "kimi"
interface ProviderInfo { code: ProviderCode; name: string; default_model: string; base_url: string }
type ThinkingMode = "auto" | "enabled" | "disabled"
type ReasoningEffort = "low" | "medium" | "high" | "xhigh" | "max"
interface GenerationOptions { thinking: ThinkingMode; reasoning_effort: ReasoningEffort | ""; streaming: boolean }
interface ProfileInfo { profile_code: string; provider_code: ProviderCode; display_name: string; model_code: string; base_url: string; enabled: boolean; capabilities: { thinking_modes: ThinkingMode[]; reasoning_efforts: ReasoningEffort[]; streaming: boolean; reasoning_content: boolean } }
interface ChatMessage { role: "user" | "assistant"; content: string; reasoning_content?: string; reasoning_open?: boolean; pending?: boolean; settings?: GenerationOptions; usage?: Record<string, number>; error?: string; incomplete?: boolean }
interface ModelResponse { message: { content?: string | null; reasoning_content?: string | null }; finish_reason: string; usage?: Record<string, number> | null }
interface StreamEvent { type: string; delta?: string; message?: string; usage?: Record<string, number>; response?: ModelResponse }
const providers = ref<ProviderInfo[]>([])
const profiles = ref<ProfileInfo[]>([])
const selectedProfile = ref("")
const messages = ref<ChatMessage[]>([])
const conversations = new Map<string, ChatMessage[]>()
const generation = reactive<GenerationOptions>({ thinking: "auto", reasoning_effort: "", streaming: true })
const profileOptions = new Map<string, GenerationOptions>()
const thinkingLabels = { auto: "模型默认", enabled: "开启思考", disabled: "关闭思考" }
const effortLabels = { low: "低 · low", medium: "中 · medium", high: "高 · high", xhigh: "更高 · xhigh", max: "最高 · max" }
const input = ref("")
const loading = ref(false)
const generationOpen = ref(false)
const viewportHeight = ref(0)
const loadingProviders = ref(true)
const pageError = ref("")
const chatBody = ref<HTMLElement | null>(null)
const configOpen = ref(false)
const configBusy = ref(false)
const configAction = ref<"save" | "verify" | null>(null)
const configMessage = ref("")
const editingCode = ref("")
const adminToken = ref("")
const form = reactive({ provider_code: "deepseek" as ProviderCode, display_name: "", model_code: "", base_url: "", api_key: "", enabled: true })
let abortController: AbortController | null = null
const currentProfile = computed(() => profiles.value.find(p => p.profile_code === selectedProfile.value))
const capabilities = computed(() => currentProfile.value?.capabilities)
const availableEfforts = computed(() => generation.thinking === "disabled" ? [] : capabilities.value?.reasoning_efforts ?? [])
const canSend = computed(() => !loading.value && !configBusy.value && !!input.value.trim() && !!currentProfile.value?.enabled && !!capabilities.value?.thinking_modes.length)
watch(selectedProfile, (code, old) => {
  if (old) { conversations.set(old, messages.value); profileOptions.set(old, { ...generation }) }
  messages.value = conversations.get(code) ?? []
  Object.assign(generation, profileOptions.get(code) ?? { thinking: "auto", reasoning_effort: "", streaming: true })
})
watch([capabilities, () => generation.thinking], () => {
  if (!capabilities.value?.thinking_modes.includes(generation.thinking)) generation.thinking = "auto"
  if (!availableEfforts.value.includes(generation.reasoning_effort as ReasoningEffort)) generation.reasoning_effort = ""
})
onMounted(loadProviders)
function updateViewport() { viewportHeight.value = window.visualViewport?.height ?? window.innerHeight }
onMounted(() => {
  updateViewport()
  window.addEventListener("resize", updateViewport)
  window.visualViewport?.addEventListener("resize", updateViewport)
})
onBeforeUnmount(() => {
  abortController?.abort()
  window.removeEventListener("resize", updateViewport)
  window.visualViewport?.removeEventListener("resize", updateViewport)
})
function focusComposer() { if (window.matchMedia("(max-width: 680px)").matches) generationOpen.value = false }
async function api(path: string, options: RequestInit = {}) {
  const response = await apiFetch(path, options)
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败（${response.status}）`)
  return data
}
async function loadProviders() {
  loadingProviders.value = true
  pageError.value = ""
  try {
    providers.value = await api("/api/v1/models")
    profiles.value = await api("/api/v1/model-profiles")
    if (!profiles.value.some(p => p.profile_code === selectedProfile.value)) {
      selectedProfile.value = profiles.value.find(p => p.enabled)?.profile_code ?? profiles.value[0]?.profile_code ?? ""
    }
    if (!profiles.value.length && props.canManage) openConfig()
  } catch (e) { pageError.value = e instanceof Error ? e.message : "配置加载失败" }
  finally { loadingProviders.value = false }
}
function providerDefaults() {
  const p = providers.value.find(p => p.code === form.provider_code)
  if (p) { form.display_name = p.name; form.model_code = p.default_model; form.base_url = p.base_url }
}
function openConfig(profile?: ProfileInfo) {
  editingCode.value = profile?.profile_code ?? ""
  form.api_key = ""
  configMessage.value = ""
  if (profile) Object.assign(form, profile)
  else { form.provider_code = "deepseek"; form.enabled = true; providerDefaults() }
  configOpen.value = true
}
function adminHeaders() { return { "Content-Type": "application/json", ...(adminToken.value ? { Authorization: `Bearer ${adminToken.value}` } : {}) } }
async function saveConfig() {
  if (configBusy.value) return
  configBusy.value = true; configAction.value = "save"; configMessage.value = ""
  try {
    const body: Record<string, unknown> = { display_name: form.display_name, model_code: form.model_code, base_url: form.base_url, enabled: form.enabled }
    if (!editingCode.value) body.provider_code = form.provider_code
    if (form.api_key) body.api_key = form.api_key
    const saved = await api(`/api/v1/model-profiles${editingCode.value ? "/" + editingCode.value : ""}`, {
      method: editingCode.value ? "PATCH" : "POST", headers: adminHeaders(), body: JSON.stringify(body),
    })
    form.api_key = ""; configOpen.value = false
    conversations.delete(saved.profile_code)
    if (selectedProfile.value === saved.profile_code) messages.value = []
    await loadProviders(); selectedProfile.value = saved.profile_code
  } catch (e) { configMessage.value = e instanceof Error ? e.message : "保存失败" }
  finally { configBusy.value = false; configAction.value = null }
}
async function verifyConfig() {
  if (!editingCode.value || configBusy.value) return
  configBusy.value = true; configAction.value = "verify"; configMessage.value = ""
  try {
    const result = await api(`/api/v1/model-profiles/${editingCode.value}/verify`, { method: "POST", headers: adminHeaders() })
    configMessage.value = result.passed ? "单次文本调用通过；这不代表流式、多轮和工具调用均已验证。" : `调用未通过：${result.finish_reason}`
  } catch (e) { configMessage.value = e instanceof Error ? e.message : "验证失败" }
  finally { configBusy.value = false; configAction.value = null }
}
async function sendMessage() {
  const content = input.value.trim()
  if (!canSend.value) return
  messages.value.push({ role: "user", content }); input.value = ""
  const requestMessages = messages.value.filter(m => !m.error && !m.incomplete).map(m => ({ role: m.role, content: m.content, ...(m.reasoning_content ? { reasoning_content: m.reasoning_content } : {}) }))
  const settings = { ...generation }
  const assistantMessage: ChatMessage = reactive({ role: "assistant", content: "", reasoning_content: "", reasoning_open: true, pending: true, settings })
  messages.value.push(assistantMessage); loading.value = true; pageError.value = ""
  abortController = new AbortController()
  await scrollToBottom()
  try {
    const response = await apiFetch(settings.streaming ? "/api/v1/chat/stream" : "/api/v1/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile_code: selectedProfile.value, messages: requestMessages, thinking: settings.thinking, ...(settings.reasoning_effort ? { reasoning_effort: settings.reasoning_effort } : {}) }),
      signal: abortController.signal,
    })
    if (!response.ok) { const data = await response.json().catch(() => null); throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败（${response.status}）`) }
    if (settings.streaming) {
      if (!response.body) throw new Error("模型服务没有返回数据流")
      await consumeStream(response.body, assistantMessage)
    } else applyResponse(await response.json() as ModelResponse, assistantMessage)
  } catch (e) {
    assistantMessage.incomplete = true
    assistantMessage.error = e instanceof DOMException && e.name === "AbortError" ? "已停止生成" : e instanceof Error ? e.message : "对话请求失败"
  } finally { assistantMessage.pending = false; loading.value = false; abortController = null; await scrollToBottom() }
}
function applyResponse(response: ModelResponse, message: ChatMessage) {
  message.content = response.message.content ?? ""
  message.reasoning_content = response.message.reasoning_content ?? ""
  message.usage = response.usage ?? undefined
  if (response.finish_reason === "length") { message.incomplete = true; message.error = "达到输出上限，回答不完整" }
  if (response.finish_reason === "tool_calls") { message.incomplete = true; message.error = "模型请求工具调用，当前对话页没有执行工具" }
}
async function consumeStream(stream: ReadableStream<Uint8Array>, message: ChatMessage) {
  const reader = stream.getReader(); const decoder = new TextDecoder(); let buffer = ""; let finished = false
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split("\n\n"); buffer = blocks.pop() ?? ""
      for (const block of blocks) {
        const line = block.split("\n").find(l => l.startsWith("data:"))
        if (!line) continue
        const event = JSON.parse(line.slice(5).trim()) as StreamEvent
        if (event.type === "content" && event.delta) message.content += event.delta
        if (event.type === "reasoning" && event.delta) message.reasoning_content = (message.reasoning_content ?? "") + event.delta
        if (event.type === "usage" && event.usage) message.usage = event.usage
        if (event.type === "error") throw new Error(event.message || "模型服务调用失败")
        if (event.type === "done" && event.response) {
          finished = true
          applyResponse(event.response, message)
        }
        await scrollToBottom()
      }
    }
    if (!finished) throw new Error("模型连接中断，未收到完整结束事件")
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}
function stopGeneration() { abortController?.abort() }
function clearConversation() { if (!loading.value) { messages.value = []; conversations.delete(selectedProfile.value); input.value = ""; pageError.value = "" } }
function handleKeydown(event: KeyboardEvent) { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void sendMessage() } }
async function scrollToBottom() { await nextTick(); if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight }
</script>

<template>
  <main class="app-shell" :style="viewportHeight ? { '--app-height': `${viewportHeight}px` } : undefined">
    <header v-if="!embedded" class="app-header">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" fill-rule="evenodd" d="M12 3 3.5 21H7l1.7-4h6.6l1.7 4h3.5L12 3Zm-2 10.8L12 8.9l2 4.9h-4Z" /></svg></span>
        <div class="brand-copy">
          <strong>Begin Agent</strong>
          <span>统一模型对话</span>
        </div>
      </div>
      <button class="secondary-button" :disabled="loading || configBusy || messages.length === 0" @click="clearConversation">
        新对话
      </button>
    </header>

    <section class="chat-card">
      <div class="model-bar">
        <label class="model-field"><span class="model-label">模型配置<span class="connection-state">{{ currentProfile ? (currentProfile.enabled ? "已保存" : "已停用") : "待配置" }}</span></span>
          <select v-model="selectedProfile" :disabled="loadingProviders || loading || configBusy">
            <option v-for="profile in profiles" :key="profile.profile_code" :value="profile.profile_code">{{ profile.display_name }} · {{ profile.model_code }}{{ profile.enabled ? "" : "（停用）" }}</option>
          </select>
        </label>
        <button v-if="canManage" class="secondary-button" :disabled="loading || configBusy" @click="openConfig()">新增模型</button>
        <button v-if="canManage" class="secondary-button" :disabled="loading || configBusy || !currentProfile" @click="openConfig(currentProfile)">配置</button>
        <button v-if="embedded" class="secondary-button" :disabled="loading || configBusy || !messages.length" @click="clearConversation">新对话</button>
      </div>
      <button v-if="currentProfile" class="generation-toggle" type="button" :aria-expanded="generationOpen" aria-controls="generation-controls" @click="generationOpen = !generationOpen">
        <span>对话设置</span><span class="generation-summary">{{ thinkingLabels[generation.thinking] }} · {{ generation.reasoning_effort || '默认级别' }} · {{ generation.streaming ? '流式' : '非流式' }}</span><span aria-hidden="true">{{ generationOpen ? '收起 ▴' : '展开 ▾' }}</span>
      </button>
      <div v-if="currentProfile" id="generation-controls" class="generation-controls" :class="{ 'is-open': generationOpen }">
        <label>思考模式<select v-model="generation.thinking" :disabled="loading || configBusy || !currentProfile?.enabled || !capabilities?.thinking_modes.length">
          <option v-for="mode in capabilities?.thinking_modes" :key="mode" :value="mode">{{ thinkingLabels[mode] }}</option>
        </select></label>
        <label>思考级别<select v-model="generation.reasoning_effort" :disabled="loading || configBusy || !currentProfile?.enabled || !availableEfforts.length">
          <option value="">模型默认</option>
          <option v-for="effort in availableEfforts" :key="effort" :value="effort">{{ effortLabels[effort] }}</option>
        </select></label>
        <label class="stream-toggle"><input v-model="generation.streaming" type="checkbox" :disabled="loading || configBusy || !currentProfile?.enabled || !capabilities?.streaming" /> 流式输出</label>
        <span class="generation-hint">{{ !capabilities?.thinking_modes.length ? '此模型尚不在适配范围内' : generation.thinking === 'disabled' ? '已关闭思考，不发送思考级别' : !capabilities?.thinking_modes.includes('disabled') ? '此模型始终开启思考' : '按模型能力调用' }}</span>
      </div>
      <form v-if="configOpen" class="model-config" :aria-busy="configBusy" @submit.prevent="saveConfig">
        <h2>{{ editingCode ? "编辑模型配置" : "新增模型配置" }}</h2>
        <div class="config-grid">
          <label>供应商<select v-model="form.provider_code" :disabled="!!editingCode || configBusy" @change="providerDefaults"><option v-for="p in providers" :key="p.code" :value="p.code">{{ p.name }}</option></select></label>
          <label>配置名称<input v-model="form.display_name" :disabled="configBusy" required maxlength="128" /></label>
          <label>模型编码<input v-model="form.model_code" :disabled="configBusy" required maxlength="128" /></label>
          <label>API 地址<input v-model="form.base_url" :disabled="configBusy" required type="url" /></label>
          <label>API Key<input v-model="form.api_key" :disabled="configBusy" type="password" autocomplete="new-password" :required="!editingCode" :placeholder="editingCode ? '留空保留原密钥' : '输入供应商 API Key'" /></label>
          <label v-if="!unified">管理令牌（本机开发可留空）<input v-model="adminToken" :disabled="configBusy" type="password" autocomplete="off" /></label>
        </div>
        <label class="enabled-field"><input v-model="form.enabled" type="checkbox" :disabled="configBusy" /> 启用</label>
        <p>密钥加密保存到数据库，不会回显。修改配置后请保存，再测试调用。</p>
        <p v-if="configBusy" class="form-status" role="status">{{ configAction === 'save' ? '正在保存配置…' : '正在测试已保存的配置…' }}</p>
        <p v-if="configMessage" role="status">{{ configMessage }}</p>
        <div class="config-actions">
          <button class="send-button" type="submit" :disabled="configBusy">{{ configAction === 'save' ? '保存中…' : '保存' }}</button>
          <button v-if="editingCode" class="secondary-button" type="button" :disabled="configBusy" @click="verifyConfig">{{ configAction === 'verify' ? '测试中…' : '测试已保存配置' }}</button>
          <button class="secondary-button" type="button" :disabled="configBusy" @click="configOpen = false; form.api_key = ''">关闭</button>
        </div>
      </form>

      <div ref="chatBody" class="chat-body">
        <div v-if="messages.length === 0" class="empty-state">
          <span class="empty-icon">✦</span>
          <h1>开始一次对话</h1>
          <p>选择模型后发送消息。多轮上下文由浏览器随每次请求一起提交。</p>
          <div class="provider-list">
            <span v-for="provider in providers" :key="provider.code" >
              {{ provider.name }}
            </span>
          </div>
        </div>

        <div v-else class="message-list">
          <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
            <div class="message-main" :class="{ error: message.error }">
              <div class="message-header">
                <div class="message-meta"><span class="message-avatar">{{ message.role === "user" ? "我" : "AI" }}</span>{{ message.role === "user" ? "你" : currentProfile?.display_name }}</div>
                <span v-if="message.usage?.total_tokens !== undefined" class="token-badge">{{ message.usage.total_tokens }} tokens</span>
              </div>
              <div v-if="message.settings" class="request-settings">{{ thinkingLabels[message.settings.thinking] }} · {{ message.settings.reasoning_effort ? effortLabels[message.settings.reasoning_effort] : '默认级别' }} · {{ message.settings.streaming ? '流式' : '非流式' }}</div>
              <details v-if="message.reasoning_content" class="reasoning-panel" :open="message.reasoning_open" @toggle="message.reasoning_open = ($event.target as HTMLDetailsElement).open">
                <summary>思考内容 · {{ message.pending && !message.content ? '思考中' : message.pending ? '正在回答' : message.error ? '已中断' : '已结束' }}<span>{{ message.reasoning_content.length }} 字符</span></summary>
                <div class="reasoning-note">模型返回的思考信息</div>
                <div class="reasoning-content">{{ message.reasoning_content }}</div>
              </details>
              <div v-if="message.content || message.pending" class="message-content" :class="{ error: message.error }">
                <MarkdownContent v-if="message.content && message.role === 'assistant'" :content="message.content" />
                <span v-else-if="message.content">{{ message.content }}</span>
                <span v-else-if="message.pending" class="waiting-state"><span class="typing"><i></i><i></i><i></i></span>{{ message.settings?.streaming ? (message.reasoning_content ? '正在思考…' : '等待模型响应…') : '等待完整回复…' }}</span>
              </div>
              <div v-if="message.role === 'assistant' && !message.pending && !message.reasoning_content && !message.error" class="reasoning-absence">{{ message.settings?.thinking === 'disabled' ? '本次已关闭思考' : '模型本次未返回思考内容' }}</div>
              <div v-if="message.usage" class="usage-info">输入 {{ message.usage.prompt_tokens ?? '—' }} · 输出 {{ message.usage.completion_tokens ?? '—' }}<span v-if="message.usage.reasoning_tokens !== undefined"> · 思考 {{ message.usage.reasoning_tokens }}</span> tokens</div>
              <p v-if="message.error" class="page-error">{{ message.error }}</p>
            </div>
          </article>
        </div>
      </div>

      <div class="composer-area">
        <div v-if="pageError" class="page-error">{{ pageError }}</div>
        <div v-if="!currentProfile?.enabled && !loadingProviders" class="config-hint">
          {{ currentProfile ? '当前模型配置已停用，请在“配置”中启用，或选择其他模型。' : '请通过“新增模型”填写 API Key，保存到数据库后使用。' }}
        </div>
        <div class="composer">
          <textarea
            v-model="input"
            rows="2"
            :disabled="loadingProviders"
            placeholder="输入消息…"
            aria-label="对话消息"
            @focus="focusComposer"
            @keydown="handleKeydown"
          ></textarea>
          <button v-if="loading" class="stop-button" @click="stopGeneration">停止</button>
          <button v-else class="send-button" :disabled="!canSend" @click="sendMessage">发送</button>
        </div>
        <p class="composer-note"><span class="keyboard-hint">Ctrl/⌘ + Enter 发送 · </span>模型可能生成不准确内容，请核对重要信息。</p>
      </div>
    </section>
  </main>
</template>
