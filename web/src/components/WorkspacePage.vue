<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { api } from "../api";
import {
  useWorkspace,
  titleOf,
  keyOf,
  label,
  date,
  duration,
  endpoint,
  type Row,
} from "../workspace";
import RunInspector from "./RunInspector.vue";
import OperationsOverview from "./OperationsOverview.vue";
const props = defineProps<{ kind: string; code?: string }>();
const { data, busy, readonly, can, go, act } = useWorkspace();
const rows = ref<Row[]>([]),
  overview = ref<Row>({}),
  search = ref(""),
  status = ref(""),
  mine = ref(""),
  group = ref(""),
  groups = ref<string[]>([]),
  loading = ref(true),
  page = ref(0),
  days = ref(7),
  runAgent = ref(""),
  runTask = ref(""),
  runDays = ref(""),
  roots = ref("");
let alive = true;
let requestNumber = 0;
let filterTimer: ReturnType<typeof setTimeout> | undefined;
const serverList = computed(
  () =>
    ["tasks", "agents", "runs", "tools", "mcps", "skills"].includes(
      props.kind,
    ) && !props.code,
);
const names: Record<string, string> = {
  overview: "工作空间概览",
  tasks: "任务",
  agents: "Agent",
  runs: "运行记录",
  models: "模型",
  tools: "工具",
  mcps: "MCP 服务",
  skills: "Skill",
};
const descriptions: Record<string, string> = {
  tasks: "每个任务保留连续对话，每一轮执行都可以追溯。",
  agents: "配置、调试和发布你的智能协作成员。",
  runs: "查看执行链路、模型消息和工具调用。",
  models: "管理模型连接，为 Agent 提供推理能力。",
  tools: "按分组管理 Agent 可以调用的工具。",
  mcps: "连接 MCP 服务，发现并授权远程工具。",
  skills: "将可复用的指令沉淀为 Agent 的技能。",
};
const filtered = computed(() =>
  serverList.value
    ? rows.value
    : rows.value.filter(
        (r) =>
          `${titleOf(props.kind, r)} ${r.group_name ?? ""} ${r.description ?? ""}`
            .toLowerCase()
            .includes(search.value.toLowerCase()) &&
          (!status.value ||
            (status.value === "active"
              ? r.status === 1 || r.enabled === true
              : status.value === "inactive"
                ? r.status === 0 || r.enabled === false
                : r.status === status.value)),
      ),
);
const manage = computed(
  () =>
    !readonly.value &&
    can(
      props.kind === "tasks"
        ? "tasks.create"
        : props.kind === "agents"
          ? "agents.manage"
          : "resources.manage",
    ),
);
async function refresh() {
  const request = ++requestNumber;
  loading.value = true;
  try {
    if (!names[props.kind]) return;
    if (props.kind === "overview") {
      const value = await api<Row>(
        "/api/v1/overview" + (days.value === 7 ? "" : "?days=" + days.value),
      );
      if (alive && request === requestNumber) overview.value = value;
    } else {
      const value = await api<Row[]>(
        endpoint(props.kind) +
          (serverList.value
            ? `?limit=50&offset=${page.value * 50}${props.kind === "runs" ? `&agent_code=${encodeURIComponent(runAgent.value)}&task_code=${encodeURIComponent(runTask.value)}&days=${runDays.value || 0}&root_only=${roots.value === "root"}` : ""}${props.kind === "tasks" && mine.value ? "&mine=true" : ""}${props.kind === "tools" && group.value ? "&group_name=" + encodeURIComponent(group.value) : ""}&search=${encodeURIComponent(search.value)}${status.value ? "&status=" + (["agents", "tools", "mcps", "skills"].includes(props.kind) ? (status.value === "active" ? "1" : "0") : status.value) : ""}`
            : ""),
      );
      if (alive && request === requestNumber) rows.value = value;
    }
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
watch([search, status, mine, group, runAgent, runTask, runDays, roots], () => {
  if (!serverList.value) return;
  clearTimeout(filterTimer);
  page.value = 0;
  filterTimer = setTimeout(() => {
    if (alive) void act(refresh, "", true);
  }, 250);
});
onMounted(
  () =>
    void act(
      async () => {
        if (props.kind === "tools")
          groups.value = await api<string[]>("/api/v1/tools/groups");
        await refresh();
      },
      "",
      true,
    ),
);
onBeforeUnmount(() => {
  alive = false;
  clearTimeout(filterTimer);
});
</script>
<template>
  <RunInspector v-if="kind === 'runs' && code" :code="code" />
  <main v-else class="platform-main">
    <template v-if="kind === 'overview'">
      <div class="page-heading">
        <div>
          <span class="eyebrow">WORKSPACE / OVERVIEW</span>
          <h1>让想法，开始执行。</h1>
          <p class="muted">
            在一个工作空间里，连接模型、组织 Agent、追踪每次协作。
          </p>
        </div>
        <button
          v-if="can('tasks.create') && !readonly"
          class="primary-button"
          @click="go('tasks/new')"
        >
          ＋ 新建任务
        </button>
      </div>
      <div class="metrics-grid">
        <article
          v-for="[key, text, icon] in [
            ['agents', 'Agent 数量', '◇'],
            ['tasks', '全部任务', '▤'],
            ['running', '正在进行', '◷'],
            ['succeeded', '成功完成', '✓'],
          ]"
          :key="key"
          class="metric"
        >
          <span
            >{{ text }} <i>{{ icon }}</i></span
          ><strong>{{ overview[key] ?? "—" }}</strong
          ><small>{{
            key === "running" ? "包含等待执行的任务" : "当前租户的真实数据"
          }}</small>
        </article>
      </div>
      <OperationsOverview
        v-if="overview.activity && (can('tasks.read') || can('runs.read'))"
        :activity="overview.activity"
        :days="days"
        :busy="busy"
        @change="
          (value) =>
            act(async () => {
              days = value;
              await refresh();
            })
        "
      />
      <section class="workspace-panel onboarding">
        <div>
          <span class="eyebrow">从这里开始</span>
          <h2>搭建你的第一个 Agent 工作流</h2>
          <p class="muted">连接模型 → 配置 Agent → 发布版本 → 创建任务</p>
        </div>
        <div class="row-actions">
          <button
            v-if="can('resources.manage') && !readonly"
            class="secondary-button"
            @click="go('models')"
          >
            配置模型</button
          ><button
            v-if="can('agents.manage') && !readonly"
            class="primary-button"
            @click="go('agents/new')"
          >
            创建 Agent
          </button>
        </div>
      </section>
      <section class="workspace-panel">
        <div class="section-heading">
          <h2>最近任务</h2>
          <button class="secondary-button" @click="go('tasks')">
            查看全部 →
          </button>
        </div>
        <div v-if="!overview.recent_tasks?.length" class="blank-state">
          <span class="blank-symbol">▤</span>
          <h3>还没有任务</h3>
          <p>发布一个 Agent 后，就可以开始你的第一次协作。</p>
          <button
            v-if="can('tasks.create') && !readonly"
            class="secondary-button"
            @click="go('tasks/new')"
          >
            新建任务
          </button>
        </div>
        <a
          v-for="r in overview.recent_tasks"
          :key="r.task_code"
          :href="'#/tasks/' + r.task_code"
          class="list-row"
          ><div>
            <strong>{{ r.title }}</strong
            ><small>{{ date(r.created_at) }}</small>
          </div>
          <span class="status-badge" :data-status="r.status">{{
            label(r.status)
          }}</span></a
        >
      </section>
    </template>
    <template v-else>
      <div class="page-heading">
        <div>
          <span class="eyebrow"
            >{{
              ["agents", "tasks", "runs"].includes(kind)
                ? "WORKSPACE"
                : "RESOURCES"
            }}
            / {{ kind.toUpperCase() }}</span
          >
          <h1>
            {{ names[kind] || "页面未找到" }}
            <span class="count-badge">{{ rows.length }}</span>
          </h1>
          <p class="muted">{{ descriptions[kind] }}</p>
        </div>
        <div class="row-actions">
          <button
            v-if="kind === 'models' && !readonly"
            class="secondary-button"
            @click="go('model-chat')"
          >
            模型调试</button
          ><button
            v-if="kind !== 'runs' && manage"
            class="primary-button"
            @click="go(kind + '/new')"
          >
            ＋ 新建{{ names[kind] }}
          </button>
        </div>
      </div>
      <section class="workspace-panel list-panel">
        <div class="list-toolbar" :class="{ 'run-list-toolbar': kind === 'runs' }">
          <template v-if="kind === 'runs'">
            <label v-if="can('agents.read')"
              ><span class="visually-hidden">执行 Agent</span
              ><select v-model="runAgent" aria-label="执行 Agent">
                <option value="">全部 Agent</option>
                <option
                  v-for="a in data.agents"
                  :key="a.agent_code"
                  :value="a.agent_code"
                >
                  {{ a.agent_name }}
                </option>
              </select></label
            >
            <label
              ><span class="visually-hidden">任务编码</span
              ><input
                v-model="runTask"
                aria-label="任务编码"
                placeholder="按任务编码筛选"
                maxlength="40"
            /></label>
            <label
              ><span class="visually-hidden">运行日期</span
              ><select v-model="runDays" aria-label="运行日期">
                <option value="">全部日期</option>
                <option value="7">最近 7 天</option>
                <option value="30">最近 30 天</option>
              </select></label
            >
            <label
              ><span class="visually-hidden">运行层级</span
              ><select v-model="roots" aria-label="运行层级">
                <option value="">全部运行</option>
                <option value="root">仅根运行</option>
              </select></label
            >
          </template>
          <label v-if="kind === 'tools'"
            ><span class="visually-hidden">工具分组</span
            ><select v-model="group">
              <option value="">全部分组</option>
              <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
            </select></label
          >
          <label v-if="kind === 'tasks'"
            ><span class="visually-hidden">任务范围</span
            ><select v-model="mine" aria-label="任务范围">
              <option value="">全部任务</option>
              <option value="mine">我的任务</option>
            </select></label
          >
          <label class="search-field"
            ><span class="visually-hidden">搜索{{ names[kind] }}</span
            ><input
              v-model="search"
              :placeholder="
                kind === 'runs' ? '搜索任务、Agent 或编码…' : '搜索名称或分组…'
              "
              aria-label="搜索记录" /></label
          ><label
            ><span class="visually-hidden">状态筛选</span
            ><select v-model="status" aria-label="状态筛选">
              <option value="">全部状态</option>
              <template v-if="['tasks', 'runs'].includes(kind)"
                ><option
                  v-for="s in [
                    'PENDING',
                    'RUNNING',
                    'SUCCEEDED',
                    'FAILED',
                    'TIMEOUT',
                    'CANCELLED',
                  ]"
                  :key="s"
                  :value="s"
                >
                  {{ label(s) }}
                </option></template
              ><template v-else
                ><option value="active">启用</option>
                <option value="inactive">停用</option></template
              >
            </select></label
          ><button
            class="secondary-button"
            :disabled="busy"
            @click="act(refresh)"
          >
            刷新
          </button>
        </div>
        <p v-if="loading" class="blank-state" role="status">正在加载…</p>
        <div v-else-if="!filtered.length" class="blank-state">
          <span class="blank-symbol">{{
            kind === "agents" ? "◇" : kind === "tasks" ? "▤" : "⬡"
          }}</span>
          <h3>
            {{
              search || status || runAgent || runTask || runDays || roots
                ? "没有匹配的结果"
                : "还没有" + names[kind]
            }}
          </h3>
          <p>
            {{
              search || status || runAgent || runTask || runDays || roots
                ? "调整搜索或筛选条件后重试。"
                : kind === "tasks"
                  ? "先发布一个 Agent，再提交你的任务。"
                  : "添加一个" + names[kind] + "，开始构建你的工作空间。"
            }}
          </p>
          <button
            v-if="manage && kind !== 'runs'"
            class="secondary-button"
            @click="go(kind + '/new')"
          >
            新建{{ names[kind] }}
          </button>
        </div>
        <div
          v-else
          class="entity-grid"
          :class="{ 'table-grid': ['tasks', 'runs'].includes(kind) }"
        >
          <a
            v-for="r in filtered"
            :key="r[keyOf(kind)]"
            :href="'#/' + kind + '/' + r[keyOf(kind)]"
            class="resource-card"
            ><div class="resource-top">
              <span class="resource-icon">{{
                kind === "agents"
                  ? "◇"
                  : kind === "tools"
                    ? "⌘"
                    : kind === "mcps"
                      ? "⇄"
                      : kind === "skills"
                        ? "✧"
                        : "▤"
              }}</span
              ><span class="status-badge" :data-status="r.status">{{
                typeof r.status === "string"
                  ? label(r.status)
                  : r.status === 0 || r.enabled === false
                    ? "已停用"
                    : kind === "agents"
                      ? r.current_version_code
                        ? "已发布"
                        : "待发布"
                      : "启用"
              }}</span>
            </div>
            <h2>
              {{
                kind === "runs" ? r.task_title || r.task_code : titleOf(kind, r)
              }}
            </h2>
            <p>
              {{
                kind === "runs"
                  ? (r.agent_name || r.agent_code) +
                    " · 第 " +
                    r.turn_no +
                    " 轮 · " +
                    (r.parent_run_code ? "子运行" : "根运行")
                  : kind === "agents"
                    ? label(r.agent_type)
                    : kind === "models"
                      ? r.provider_code + " · " + r.model_code
                      : kind === "tools"
                        ? r.group_name +
                          " · " +
                          r.connection_config?.implementation
                        : kind === "mcps"
                          ? r.checked_at
                            ? r.tools.length + " 个已发现工具"
                            : "尚未检测连接"
                          : kind === "skills"
                            ? r.description || "可复用指令"
                            : r.agent_code || r.task_code
              }}
            </p>
            <div v-if="kind === 'runs'" class="run-list-metrics">
              <span>耗时 {{ duration(r.elapsed_ms) }}</span>
              <span
                >已上报 Token {{ r.tokens ?? "—"
                }}<template v-if="r.unreported_calls">
                  · {{ r.unreported_calls }} 次未上报</template
                ></span
              >
              <small class="code-text">{{ r.run_code }}</small>
            </div>
            <div class="resource-footer">
              <span>{{ date(r.created_at) }}</span
              ><span>查看 →</span>
            </div></a
          >
        </div>
        <div v-if="serverList" class="pagination">
          <button
            class="secondary-button"
            :disabled="busy || !page"
            @click="
              act(async () => {
                page--;
                await refresh();
              })
            "
          >
            上一页</button
          ><span>第 {{ page + 1 }} 页 · 每页最多 50 条</span
          ><button
            class="secondary-button"
            :disabled="busy || rows.length < 50"
            @click="
              act(async () => {
                page++;
                await refresh();
              })
            "
          >
            下一页
          </button>
        </div>
      </section>
    </template>
  </main>
</template>
