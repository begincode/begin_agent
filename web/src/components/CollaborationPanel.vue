<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, post } from "../api";
import { useWorkspace, type Row } from "../workspace";
const props = defineProps<{ code: string }>();
const { data, busy, readonly, can, go, act } = useWorkspace();
const rows = ref<Row[]>([]),
  error = ref(""),
  loading = ref(false),
  editing = ref("");
const initial = () => ({
  call_mode: "ON_DEMAND",
  timeout_seconds: 120,
  enabled: true,
});
const addition = ref({ sub_agent_code: "", ...initial() }),
  settings = ref(initial());
const editable = computed(() => can("agents.manage") && !readonly.value);
const candidates = computed(() =>
  (data.value.agents ?? []).filter(
    (a) =>
      a.agent_code !== props.code &&
      a.status === 1 &&
      a.current_version_code &&
      !rows.value.some((r) => r.sub_agent_code === a.agent_code),
  ),
);
const modes: Record<string, string> = {
  ON_DEMAND: "模型按需调用",
  SERIAL: "预先串行执行",
  PARALLEL: "预先并行执行",
};
let alive = true,
  requestNumber = 0;
async function refresh() {
  const request = ++requestNumber;
  loading.value = true;
  try {
    const value = await api<Row[]>(`/api/v1/agents/${props.code}/relations`);
    if (!alive || request !== requestNumber) return;
    rows.value = value;
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "协作配置暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
function start(row: Row) {
  editing.value = row.relation_code;
  settings.value = {
    call_mode: row.call_mode,
    timeout_seconds: row.timeout_seconds,
    enabled: row.enabled,
  };
}
async function save() {
  if (!editable.value || busy.value) return;
  await act(async () => {
    if (editing.value)
      await post(
        `/api/v1/agents/${props.code}/relations/${editing.value}`,
        settings.value,
        "PATCH",
      );
    else await post(`/api/v1/agents/${props.code}/relations`, addition.value);
    editing.value = "";
    addition.value = { sub_agent_code: "", ...initial() };
    await refresh();
  }, "协作配置已保存，重新发布主 Agent 后生效");
}
async function remove(row: Row) {
  if (!editable.value || busy.value || editing.value) return;
  await act(async () => {
    await api(`/api/v1/agents/${props.code}/relations/${row.relation_code}`, {
      method: "DELETE",
    });
    await refresh();
  }, "协作关系已移除，重新发布主 Agent 后生效");
}
watch(
  () => props.code,
  () => {
    editing.value = "";
    rows.value = [];
    void refresh();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  alive = false;
  requestNumber++;
});
</script>
<template>
  <section class="workspace-panel">
    <div class="section-heading">
      <h2>子 Agent 协作</h2>
      <button
        class="secondary-button"
        :disabled="busy || loading || !!editing"
        @click="refresh"
      >
        刷新协作
      </button>
    </div>
    <p class="muted">
      调用方式、超时和关系启停在重新发布主 Agent
      后生效。历史版本和已提交任务保留原快照；子 Agent 必须启用且已有发布版本。
    </p>
    <button
      v-if="can('agents.publish') && !readonly"
      class="secondary-button"
      @click="go('agents/' + code + '/config')"
    >
      前往基本配置发布 →
    </button>
    <p v-if="error" class="inline-error" role="alert">
      {{ error }}，可刷新重试。
    </p>
    <p v-if="loading && !rows.length" class="blank-state" role="status">
      正在读取协作关系…
    </p>
    <form
      v-if="editable && !editing"
      class="stack-form collaboration-create"
      @submit.prevent="save"
    >
      <h3>添加协作关系</h3>
      <fieldset class="plain-fieldset form-fields" :disabled="busy || loading">
        <label
          >子 Agent<select v-model="addition.sub_agent_code" required>
            <option value="">选择已发布的子 Agent</option>
            <option
              v-for="a in candidates"
              :key="a.agent_code"
              :value="a.agent_code"
            >
              {{ a.agent_name }}
            </option>
          </select></label
        >
        <label
          >调用方式<select v-model="addition.call_mode">
            <option v-for="(text, key) in modes" :key="key" :value="key">
              {{ text }}
            </option>
          </select></label
        >
        <label
          >单次超时秒数<input
            v-model.number="addition.timeout_seconds"
            type="number"
            required
            min="1"
            max="1800"
        /></label>
        <button class="primary-button" :disabled="!addition.sub_agent_code">
          添加协作
        </button>
      </fieldset>
      <p v-if="!candidates.length" class="muted">
        没有可新增的 Agent。请先发布并启用其他 Agent，已有停用关系可在下方编辑。
      </p>
    </form>
    <div v-if="!rows.length && !loading" class="blank-state">
      暂无协作关系。
    </div>
    <article
      v-for="row in rows"
      :key="row.relation_code"
      class="collaboration-card"
    >
      <div class="section-heading">
        <h3>
          {{ row.sub_agent_name || row.sub_agent_code }}
          <span class="status-badge">{{
            row.enabled ? "关系启用" : "关系停用"
          }}</span>
        </h3>
        <div
          v-if="editable && editing !== row.relation_code"
          class="row-actions"
        >
          <button
            class="secondary-button"
            :disabled="busy || !!editing"
            @click="start(row)"
          >
            编辑协作</button
          ><button
            class="secondary-button"
            :disabled="busy || !!editing"
            @click="remove(row)"
          >
            移除关系
          </button>
        </div>
      </div>
      <p class="muted">
        {{ modes[row.call_mode] }} · 单次超时 {{ row.timeout_seconds }} 秒 · 子
        Agent {{ row.sub_agent_status === 1 ? "启用" : "停用" }} · 当前发布版本
        {{
          row.sub_agent_version_no ? "v" + row.sub_agent_version_no : "未发布"
        }}
      </p>
      <p class="code-text">
        {{ row.sub_agent_code }} ·
        {{ row.sub_agent_version_code || "尚无版本" }}
      </p>
      <a :href="'#/agents/' + row.sub_agent_code + '/versions'"
        >查看子 Agent 版本 ↗</a
      >
      <form
        v-if="editing === row.relation_code"
        class="stack-form collaboration-edit"
        @submit.prevent="save"
      >
        <fieldset
          class="plain-fieldset form-fields"
          :disabled="busy || !editable"
        >
          <label
            >调用方式<select v-model="settings.call_mode">
              <option v-for="(text, key) in modes" :key="key" :value="key">
                {{ text }}
              </option>
            </select></label
          >
          <label
            >单次超时秒数<input
              v-model.number="settings.timeout_seconds"
              type="number"
              required
              min="1"
              max="1800"
          /></label>
          <label class="check-label"
            ><input
              v-model="settings.enabled"
              type="checkbox"
            />启用关系（重新发布后生效）</label
          >
          <div class="row-actions">
            <button class="primary-button">保存协作</button
            ><button
              type="button"
              class="secondary-button"
              @click="editing = ''"
            >
              取消编辑
            </button>
          </div>
        </fieldset>
      </form>
    </article>
  </section>
</template>
