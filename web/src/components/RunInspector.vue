<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";
import { useWorkspace, label, date, duration, type Row } from "../workspace";
import MarkdownContent from "./MarkdownContent.vue";
const props = defineProps<{ code: string }>();
const { can, go } = useWorkspace();
const detail = ref<Row | null>(null),
  loading = ref(false),
  error = ref("");
const stepType = ref(""),
  stepStatus = ref("");
const steps = computed(() =>
  (detail.value?.steps ?? []).filter(
    (s: Row) =>
      (!stepType.value || s.step_type === stepType.value) &&
      (!stepStatus.value || s.status === stepStatus.value),
  ),
);
const types = computed<string[]>(() => [
  ...new Set<string>((detail.value?.steps ?? []).map((s: Row) => s.step_type)),
]);
let timer: ReturnType<typeof setTimeout> | undefined,
  requestNumber = 0,
  alive = true;
async function refresh() {
  const request = ++requestNumber;
  clearTimeout(timer);
  loading.value = true;
  try {
    const value = await api<Row>(`/api/v1/runs/${props.code}`);
    if (!alive || request !== requestNumber) return;
    detail.value = value;
    error.value = "";
    if (["PENDING", "RUNNING"].includes(value.status))
      timer = setTimeout(() => void refresh(), 1800);
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "运行详情暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
watch(
  () => props.code,
  () => {
    detail.value = null;
    stepType.value = stepStatus.value = "";
    void refresh();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  alive = false;
  requestNumber++;
  clearTimeout(timer);
});
</script>
<template>
  <main class="platform-main">
    <div class="page-heading">
      <div>
        <button class="text-button" @click="go('runs')">← 运行记录</button>
        <h1>运行详情</h1>
        <p class="code-text">{{ code }}</p>
      </div>
      <div class="row-actions">
        <button
          v-if="detail && can('tasks.read')"
          class="secondary-button"
          @click="go('tasks/' + detail.task_code)"
        >
          打开任务
        </button>
        <button class="secondary-button" :disabled="loading" @click="refresh">
          {{ loading ? "正在更新…" : "刷新详情" }}
        </button>
      </div>
    </div>
    <p v-if="error" class="inline-error" role="alert">
      {{ error }}，可点击刷新重试。
    </p>
    <p v-if="loading && !detail" class="blank-state" role="status">
      正在加载运行…
    </p>
    <template v-if="detail">
      <section class="workspace-panel">
        <h2>{{ detail.task_title || detail.task_code }}</h2>
        <p class="muted">
          {{ detail.agent_name || detail.agent_code }} · 第
          {{ detail.turn_no }} 轮 · 深度 {{ detail.run_depth }}
        </p>
        <div class="run-summary">
          <span class="status-badge" :data-status="detail.status">{{
            label(detail.status)
          }}</span>
          <span>耗时 {{ duration(detail.elapsed_ms) }}</span>
          <span
            >已上报 Token {{ detail.tokens ?? "—"
            }}<template v-if="detail.unreported_calls">
              · {{ detail.unreported_calls }} 次未上报</template
            ></span
          >
          <span
            >{{ detail.steps_count }} 个步骤 ·
            {{ detail.model_calls }} 次模型调用</span
          >
          <span>{{ detail.finish_reason || "尚未结束，将自动更新" }}</span>
        </div>
        <p class="code-text">
          固定版本 {{ detail.agent_version_code }} ·
          {{ date(detail.created_at) }}
        </p>
        <div class="row-actions">
          <a
            v-if="detail.parent_run_code"
            :href="'#/runs/' + detail.parent_run_code"
            >父运行 ↗</a
          >
          <a
            v-if="
              detail.root_run_code &&
              detail.root_run_code !== code &&
              detail.root_run_code !== detail.parent_run_code
            "
            :href="'#/runs/' + detail.root_run_code"
            >根运行 ↗</a
          >
        </div>
      </section>
      <div class="detail-columns">
        <section class="workspace-panel">
          <h2>执行步骤</h2>
          <div class="list-toolbar run-filters">
            <label
              ><span class="visually-hidden">步骤类型</span
              ><select v-model="stepType" aria-label="步骤类型">
                <option value="">全部类型</option>
                <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
              </select></label
            >
            <label
              ><span class="visually-hidden">步骤状态</span
              ><select v-model="stepStatus" aria-label="步骤状态">
                <option value="">全部步骤</option>
                <option value="FAILED">仅失败</option>
                <option value="SUCCEEDED">仅成功</option>
              </select></label
            >
          </div>
          <p v-if="!steps.length" class="muted">
            {{
              detail.steps.length
                ? "没有匹配的步骤，请调整筛选。"
                : "暂无步骤，等待执行。"
            }}
          </p>
          <details v-for="s in steps" :key="s.step_code" class="trace-step">
            <summary>
              <b>{{ s.step_no }}</b
              ><span class="step-name">{{ s.name }}</span
              ><span class="status-badge" :data-status="s.status">{{
                label(s.status)
              }}</span>
            </summary>
            <p>{{ s.step_type }} · {{ s.error_code || "正常" }}</p>
            <pre>{{
              JSON.stringify({ input: s.input, output: s.output }, null, 2)
            }}</pre>
          </details>
        </section>
        <section class="workspace-panel">
          <h2>上下文消息</h2>
          <p v-if="!detail.messages.length" class="muted">尚无消息。</p>
          <details
            v-for="m in detail.messages"
            :key="m.message_code"
            class="trace-step"
          >
            <summary>
              {{ m.role }} <span class="muted">{{ date(m.created_at) }}</span>
            </summary>
            <MarkdownContent v-if="m.content" :content="m.content" />
            <pre v-if="m.metadata?.tool_calls">{{
              JSON.stringify(m.metadata.tool_calls, null, 2)
            }}</pre>
          </details>
        </section>
      </div>
    </template>
  </main>
</template>
