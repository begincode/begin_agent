<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { api, post } from "../api";
import { LiveAccessError, readTaskEvents } from "../taskLive";
import { useWorkspace, label, date, type Row } from "../workspace";
import MarkdownContent from "./MarkdownContent.vue";
import ExecutionPanel from "./ExecutionPanel.vue";
import TaskArtifacts from "./TaskArtifacts.vue";
const props = defineProps<{ code: string }>();
const { data, identity, busy, readonly, can, go, act, load } = useWorkspace();
const detail = ref<Row | null>(null),
  prompt = ref(""),
  title = ref(""),
  agent = ref(""),
  pane = ref("conversation"),
  scroll = ref<HTMLElement | null>(null);
const execution = ref<Row | null>(null),
  traceError = ref("");
const traceOpen = ref(localStorage.getItem("agent.execution") !== "collapsed");
function toggleTrace() {
  traceOpen.value = !traceOpen.value;
  localStorage.setItem(
    "agent.execution",
    traceOpen.value ? "expanded" : "collapsed",
  );
}
const running = computed(() =>
  ["PENDING", "RUNNING"].includes(detail.value?.status ?? ""),
);
const owner = computed(
  () =>
    detail.value?.created_by === identity.value?.user_code ||
    can("tasks.manage"),
);
const canControl = computed(() => can("tasks.create") || can("tasks.manage"));
const available = computed(() =>
  (data.value.agents ?? []).filter(
    (r) => r.status === 1 && r.current_version_code,
  ),
);
const canSend = computed(
  () =>
    !busy.value &&
    !readonly.value &&
    can("tasks.create") &&
    !!prompt.value.trim() &&
    (props.code === "new" ? !!agent.value : !running.value && owner.value),
);
let rerunSource = "",
  rerunKey = "";
async function rerun() {
  if (
    busy.value ||
    readonly.value ||
    !owner.value ||
    !canControl.value ||
    detail.value?.status !== "SUCCEEDED" ||
    detail.value?.input?.test_case_code
  )
    return;
  const source = detail.value?.turns?.at(-1)?.turn_code;
  if (!source) return;
  if (rerunSource !== source) {
    rerunSource = source;
    rerunKey = crypto.randomUUID();
  }
  await act(async () => {
    await post(`/api/v1/tasks/${props.code}/rerun`, {
      source_turn_code: source,
      idempotency_key: rerunKey,
    });
    await refresh();
  }, "已重新提交最近一轮要求，沿用任务上下文和固定版本");
}
function onPromptKeydown(event: KeyboardEvent) {
  if (
    event.key === "Enter" &&
    (event.ctrlKey || event.metaKey) &&
    !event.isComposing
  ) {
    event.preventDefault();
    void send();
  }
}
const live = ref<Row | null>(null),
  streamState = ref("");
const turnPreview = (turn: Row) =>
  live.value?.turn_no === turn.turn_no
    ? live.value?.preview
    : (turn.preview ?? null);

let timer: ReturnType<typeof setTimeout> | undefined,
  reconnectTimer: ReturnType<typeof setTimeout> | undefined,
  traceTimer: ReturnType<typeof setTimeout> | undefined,
  controller: AbortController | null = null,
  alive = true,
  accessLost = false,
  failures = 0,
  traceSignature = "",
  traceRequest = 0;
async function refreshTrace() {
  if (!can("runs.read")) return;
  const request = ++traceRequest;
  try {
    const trace = await api<Row>(`/api/v1/tasks/${props.code}/execution`);
    if (alive && !accessLost && request === traceRequest) {
      execution.value = trace;
      traceError.value = "";
    }
  } catch (e) {
    if (alive && !accessLost && request === traceRequest)
      traceError.value = e instanceof Error ? e.message : "执行记录暂不可用";
  }
}
async function refresh() {
  const value = await api<Row>(`/api/v1/tasks/${props.code}`);
  if (!alive || accessLost) return;
  detail.value = value;
  await refreshTrace();
  if (alive && !controller && !accessLost) void connect();
}
function fallback() {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    if (!alive || accessLost) return;
    try {
      await refresh();
    } catch {
      /* Connection status remains visible during recovery. */
    }
    if (alive && !accessLost && running.value) fallback();
  }, 2000);
}
async function connect() {
  if (!alive || controller || accessLost || props.code === "new") return;
  clearTimeout(reconnectTimer);
  const own = new AbortController();
  controller = own;
  streamState.value = failures
    ? "连接中断，正在重连（暂用轮询）"
    : "正在连接实时输出…";
  let complete = false;
  try {
    const result = await readTaskEvents(
      `/api/v1/tasks/${props.code}/stream`,
      own.signal,
      async (value) => {
        if (
          !alive ||
          own.signal.aborted ||
          value.revision === live.value?.revision
        )
          return;
        const follow =
          !!scroll.value &&
          scroll.value.scrollHeight -
            scroll.value.clientHeight -
            scroll.value.scrollTop <
            80;
        live.value = value;
        if (
          detail.value &&
          value.turn_no === detail.value.turns?.at(-1)?.turn_no
        ) {
          detail.value.status = value.status;
          detail.value.error_code = value.error_code;
          detail.value.turns.at(-1).status = value.status;
        }
        const signature = JSON.stringify(value.activity);
        if (signature !== traceSignature) {
          traceSignature = signature;
          clearTimeout(traceTimer);
          traceTimer = setTimeout(() => void refreshTrace(), 100);
        }
        if (follow) {
          await nextTick();
          if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight;
        }
      },
      () => {
        if (alive) {
          streamState.value = "实时连接正常";
          failures = 0;
          clearTimeout(timer);
        }
      },
    );
    complete = result === "complete";
    if (complete && alive && !own.signal.aborted) {
      await refresh();
      streamState.value = "已同步最新执行结果";
    }
  } catch (e) {
    if (!alive || own.signal.aborted) return;
    if (e instanceof LiveAccessError) {
      accessLost = true;
      live.value = null;
      detail.value = null;
      execution.value = null;
      streamState.value = e.message;
      clearTimeout(timer);
      clearTimeout(traceTimer);
      if (e.status === 401)
        window.dispatchEvent(new Event("agent-auth-expired"));
      return;
    }
    failures++;
    streamState.value = "连接中断，正在重连（暂用轮询）";
    fallback();
  } finally {
    if (controller === own) controller = null;
    if (alive && !accessLost && (!complete || running.value)) {
      reconnectTimer = setTimeout(
        () => void connect(),
        complete ? 0 : Math.min(8000, 500 * 2 ** Math.min(failures, 4)),
      );
    }
  }
}
async function send() {
  if (!canSend.value) return;
  await act(async () => {
    const key = crypto.randomUUID();
    if (props.code === "new") {
      const task = await post<Row>("/api/v1/tasks", {
        agent_code: agent.value,
        title: title.value || prompt.value.slice(0, 80),
        prompt: prompt.value,
        idempotency_key: key,
      });
      await load();
      go("tasks/" + task.task_code);
    } else {
      await post(`/api/v1/tasks/${props.code}/messages`, {
        prompt: prompt.value,
        idempotency_key: key,
      });
      prompt.value = "";
      await refresh();
      await nextTick();
      if (scroll.value) scroll.value.scrollTop = scroll.value.scrollHeight;
    }
  }, "任务已提交，等待执行");
}
onMounted(() => {
  agent.value = available.value[0]?.agent_code ?? "";
  if (props.code !== "new") void act(refresh, "", true);
});
onBeforeUnmount(() => {
  alive = false;
  clearTimeout(timer);
  clearTimeout(reconnectTimer);
  clearTimeout(traceTimer);
  traceRequest++;
  controller?.abort();
});
</script>
<template>
  <main v-if="code === 'new'" class="platform-main">
    <div class="page-heading">
      <div>
        <button class="text-button" @click="go('tasks')">← 任务</button>
        <h1>开始一项新任务</h1>
        <p class="muted">选择 Agent，告诉它你想完成什么。</p>
      </div>
    </div>
    <section class="workspace-panel task-create">
      <div v-if="!available.length" class="blank-state">
        <h2>需要一个已发布的 Agent</h2>
        <p>先完成模型配置与 Agent 发布，再开始执行任务。</p>
        <button
          v-if="can('agents.manage')"
          class="primary-button"
          @click="go('agents/new')"
        >
          创建 Agent
        </button>
      </div>
      <form v-else class="stack-form" @submit.prevent="send">
        <label
          >执行 Agent<select
            v-model="agent"
            required
            :disabled="busy || readonly"
          >
            <option
              v-for="a in available"
              :key="a.agent_code"
              :value="a.agent_code"
            >
              {{ a.agent_name }}
            </option>
          </select></label
        ><label
          >任务标题<input
            v-model="title"
            maxlength="200"
            :disabled="busy || readonly"
            placeholder="可选，默认使用任务要求作为标题" /></label
        ><label
          >任务要求<textarea
            v-model="prompt"
            @keydown="onPromptKeydown"
            required
            rows="7"
            maxlength="30000"
            :disabled="busy || readonly"
            placeholder="描述目标、背景和你期望的结果…"
          />
        </label>
        <div class="row-actions">
          <button
            class="primary-button"
            :disabled="
              busy ||
              readonly ||
              !can('tasks.create') ||
              !prompt.trim() ||
              !agent
            "
          >
            提交任务 →</button
          ><button type="button" class="secondary-button" @click="go('tasks')">
            取消
          </button>
        </div>
      </form>
    </section>
  </main>
  <main v-else class="task-workspace">
    <header class="task-heading">
      <div>
        <button class="text-button" @click="go('tasks')">← 全部任务</button>
        <h1>{{ detail?.title || "加载任务…" }}</h1>
        <p class="muted">
          {{
            data.agents?.find((a) => a.agent_code === detail?.agent_code)
              ?.agent_name || "Agent"
          }}
          · 固定版本执行
        </p>
      </div>
      <div v-if="detail" class="row-actions">
        <button
          v-if="can('runs.read') && pane === 'conversation'"
          class="secondary-button trace-toggle"
          :aria-expanded="traceOpen"
          @click="toggleTrace"
        >
          {{ traceOpen ? "隐藏执行链" : "显示执行链" }}
        </button>
        <button
          v-if="
            detail.status === 'SUCCEEDED' &&
            !detail.input?.test_case_code &&
            owner &&
            canControl &&
            !readonly
          "
          class="secondary-button"
          :disabled="busy"
          title="沿用任务上下文和固定版本，重新执行最近一轮要求"
          @click="rerun"
        >
          重新执行
        </button>
        <span class="status-badge" :data-status="detail.status">{{
          label(detail.status)
        }}</span
        ><button
          v-if="running && owner && canControl"
          class="secondary-button"
          :disabled="busy"
          @click="
            act(async () => {
              await post(`/api/v1/tasks/${code}/cancel`, {});
              await refresh();
            }, '任务已取消')
          "
        >
          取消执行</button
        ><button
          v-else-if="
            ['FAILED', 'TIMEOUT', 'CANCELLED'].includes(detail.status) &&
            owner &&
            canControl &&
            !readonly
          "
          class="secondary-button"
          :disabled="busy"
          @click="
            act(async () => {
              await post(`/api/v1/tasks/${code}/retry`, {});
              await refresh();
            }, '已提交重试')
          "
        >
          重试
        </button>
      </div>
    </header>
    <p v-if="accessLost" class="inline-error task-stream-error" role="alert">
      {{ streamState }}
    </p>
    <nav class="detail-tabs">
      <button
        v-for="[key, text] in [
          ['conversation', '持续对话'],
          ['runs', '运行链路'],
          ['artifacts', '任务产物'],
        ]"
        :key="key"
        class="tab-button"
        :class="{ active: pane === key }"
        @click="pane = key"
      >
        {{ text }}
      </button>
    </nav>
    <div
      class="task-layout"
      :class="{
        'with-execution':
          pane === 'conversation' && traceOpen && can('runs.read'),
      }"
    >
      <div class="task-chat-column">
        <div ref="scroll" class="task-messages">
          <template v-if="pane === 'conversation' && detail"
            ><div
              v-for="turn in detail.turns"
              :key="turn.turn_code"
              class="turn"
            >
              <div class="turn-user">
                <small>你 · 第 {{ turn.turn_no }} 轮</small>
                <p>{{ turn.prompt }}</p>
              </div>
              <article class="turn-assistant">
                <header>
                  <span class="resource-icon">◇</span><strong>Agent</strong
                  ><span class="status-badge" :data-status="turn.status">{{
                    label(turn.status)
                  }}</span
                  ><a v-if="turn.run_code" :href="'#/runs/' + turn.run_code"
                    >查看 Run ↗</a
                  >
                </header>
                <MarkdownContent
                  v-if="turn.output?.text"
                  :content="turn.output.text"
                />
                <div
                  v-else-if="turnPreview(turn)?.content"
                  class="task-live-draft"
                >
                  <p class="muted">
                    {{
                      ["RUNNING", "PENDING"].includes(turn.status)
                        ? "实时草稿 · 尚未完成"
                        : "中断草稿 · 非最终结果"
                    }}
                    · 第 {{ turnPreview(turn).call_no }} 次模型调用
                  </p>
                  <MarkdownContent :content="turnPreview(turn).content" />
                </div>
                <p v-else class="muted">
                  {{
                    turn.status === "RUNNING"
                      ? "正在执行，结果将自动更新…"
                      : turn.status === "PENDING"
                        ? "已加入队列，等待 Worker…"
                        : "本轮未产生成功结果，可查看运行链路。"
                  }}
                </p>
                <details
                  v-if="turnPreview(turn)?.reasoning"
                  class="task-reasoning"
                >
                  <summary>思考过程</summary>
                  <MarkdownContent :content="turnPreview(turn).reasoning" />
                </details>
                <p v-if="turnPreview(turn)?.truncated" class="muted">
                  实时预览已达上限，正式结果以成功后的正文和产物为准。
                </p>
                <p
                  v-if="
                    turnPreview(turn)?.phase &&
                    ['RUNNING', 'PENDING'].includes(turn.status)
                  "
                  class="muted"
                >
                  当前阶段：{{ turnPreview(turn).phase.name }} ·
                  {{ label(turnPreview(turn).phase.status) }}
                </p>
                <p
                  v-if="
                    turn.turn_no === detail.turns?.at(-1)?.turn_no &&
                    streamState
                  "
                  class="muted"
                  role="status"
                >
                  {{ streamState }}
                </p>
                <p v-if="turn.output?.test_result" class="status-badge">
                  用例{{ turn.output.test_result.passed ? "通过" : "未通过" }}
                </p>
              </article>
            </div></template
          >
          <template v-if="pane === 'runs'"
            ><div class="conversation-width">
              <ExecutionPanel
                v-if="can('runs.read')"
                :execution="execution"
                :activity="live?.activity"
                :error="traceError"
                @refresh="act(refresh)"
              />
              <p v-else class="muted">当前账号没有运行记录读取权限。</p>
            </div></template
          >
          <TaskArtifacts
            v-if="pane === 'artifacts' && detail"
            :artifacts="detail.artifacts"
          />
        </div>
        <footer v-if="pane === 'conversation'" class="task-composer">
          <form @submit.prevent="send">
            <label class="visually-hidden" for="task-prompt">继续对话</label
            ><textarea
              id="task-prompt"
              v-model="prompt"
              @keydown="onPromptKeydown"
              rows="2"
              maxlength="30000"
              :disabled="
                busy || running || readonly || !owner || !can('tasks.create')
              "
              :placeholder="
                readonly
                  ? '租户已停用，仅可查看历史'
                  : !owner || !can('tasks.create')
                    ? '当前账号没有继续对话权限'
                    : running
                      ? '本轮执行中，结束后可以继续对话…'
                      : '继续补充要求，开始下一轮执行…'
              "
            /><button
              class="primary-button"
              :disabled="
                busy ||
                running ||
                readonly ||
                !owner ||
                !can('tasks.create') ||
                !prompt.trim()
              "
            >
              发送 ↑
            </button>
          </form>
          <small>{{
            readonly
              ? "租户已停用，仅可查看历史"
              : !owner || !can("tasks.create")
                ? "当前账号没有继续对话权限"
                : "Enter 换行 · Ctrl/⌘ + Enter 发送；每轮使用任务固定版本。"
          }}</small>
        </footer>
      </div>
      <aside
        v-if="pane === 'conversation' && traceOpen && can('runs.read')"
        class="task-execution-aside"
      >
        <ExecutionPanel
          :execution="execution"
          :activity="live?.activity"
          :error="traceError"
          @refresh="act(refresh)"
        />
      </aside>
    </div>
  </main>
</template>
