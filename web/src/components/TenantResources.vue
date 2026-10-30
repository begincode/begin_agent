<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api, post } from "../api";
import { date, useWorkspace, type Row } from "../workspace";
const { busy, readonly, can, go, act, load } = useWorkspace();
const summary = ref<Row | null>(null),
  rows = ref<Row[]>([]),
  loading = ref(false),
  error = ref("");
const search = ref(""),
  kind = ref(""),
  availability = ref(""),
  page = ref(0);
const access = computed(() => can("resources.read"));
const names: Record<string, string> = {
  MODEL: "模型",
  TOOL: "工具",
  MCP: "MCP 服务",
  SKILL: "Skill",
};
const routes: Record<string, string> = {
  MODEL: "models",
  TOOL: "tools",
  MCP: "mcps",
  SKILL: "skills",
};
const states: Record<string, string> = {
  AVAILABLE: "可配置",
  REVOKED: "已撤销授权",
  INACTIVE: "资源已停用",
  UNCHECKED: "待检测或无工具",
};
let alive = true,
  requestNumber = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
async function refresh() {
  if (!access.value) return;
  const request = ++requestNumber;
  loading.value = true;
  try {
    const query = new URLSearchParams({
      search: search.value,
      limit: "50",
      offset: String(page.value * 50),
    });
    if (kind.value) query.set("resource_kind", kind.value);
    if (availability.value) query.set("availability", availability.value);
    const [overview, records] = await Promise.all([
      api<Row>("/api/v1/resources/overview"),
      api<Row[]>("/api/v1/resources?" + query),
    ]);
    if (!alive || request !== requestNumber) return;
    summary.value = overview;
    rows.value = records;
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "资源目录暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
async function toggle(row: Row) {
  if (busy.value || readonly.value || !can("tenant.manage")) return;
  await act(async () => {
    await post(
      "/api/v1/resources/grant",
      {
        resource_kind: row.resource_kind,
        resource_code: row.resource_code,
        enabled: !row.enabled,
      },
      "PUT",
    );
    await Promise.all([refresh(), load()]);
  }, "租户资源授权已更新");
}
watch([search, kind, availability], () => {
  clearTimeout(timer);
  page.value = 0;
  timer = setTimeout(() => void refresh(), 250);
});
onMounted(() => void refresh());
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
        <span class="eyebrow">TENANT / RESOURCES</span>
        <h1>租户资源概况</h1>
        <p class="muted">资源启用、租户授权和 MCP 检测共同决定可配置状态。</p>
      </div>
      <div v-if="access" class="row-actions">
        <button
          v-if="can('audit.read')"
          class="secondary-button"
          @click="go('audit')"
        >
          查看审计记录</button
        ><button
          class="secondary-button"
          :disabled="loading || busy"
          @click="refresh"
        >
          刷新资源
        </button>
      </div>
    </div>
    <p v-if="!access" class="inline-error">没有资源读取权限。</p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}，请刷新重试。
      </p>
      <section v-if="summary" class="workspace-panel">
        <h2>{{ summary.tenant.tenant_name }}</h2>
        <p class="code-text">
          {{ summary.tenant.tenant_code }} ·
          {{ date(summary.tenant.created_at) }}
        </p>
        <span class="status-badge">{{
          summary.tenant.status === 0 ? "租户已停用，仅可查看" : "租户正常"
        }}</span>
      </section>
      <div v-if="summary" class="metrics-grid">
        <article
          v-for="[key, text] in [
            ['total', '资源总量'],
            ['available', '可配置资源'],
            ['agents', 'Agent 数量'],
            ['members', '成员数量'],
          ]"
          :key="key"
          class="metric"
        >
          <span>{{ text }}</span
          ><strong>{{ summary[key] ?? "—" }}</strong
          ><small>{{
            summary[key] == null
              ? "当前权限不提供此项统计"
              : "当前租户的真实数据"
          }}</small>
        </article>
      </div>
      <div v-if="summary" class="resource-overview-grid">
        <article
          v-for="group in summary.groups"
          :key="group.resource_kind"
          class="workspace-panel"
        >
          <h2>{{ names[group.resource_kind] }}</h2>
          <p>{{ group.available }} 个可配置 · 共 {{ group.total }} 个</p>
          <p class="muted">
            {{ group.revoked }} 个撤销授权 ·
            {{ group.inactive }} 个停用<template
              v-if="group.resource_kind === 'MCP'"
            >
              · {{ group.unchecked }} 个待检测或无工具</template
            >
          </p>
          <div class="row-actions">
            <button
              class="secondary-button"
              @click="kind = group.resource_kind"
            >
              查看本类</button
            ><button
              class="secondary-button"
              @click="go(routes[group.resource_kind])"
            >
              管理{{ names[group.resource_kind] }} →
            </button>
          </div>
        </article>
      </div>
      <section class="workspace-panel list-panel">
        <div class="list-toolbar resource-filters">
          <label class="search-field"
            ><span class="visually-hidden">搜索资源</span
            ><input
              v-model="search"
              aria-label="搜索资源"
              placeholder="搜索资源名称或编码…"
              maxlength="200"
          /></label>
          <label
            ><span class="visually-hidden">资源类型</span
            ><select v-model="kind" aria-label="资源类型">
              <option value="">全部类型</option>
              <option v-for="(name, key) in names" :key="key" :value="key">
                {{ name }}
              </option>
            </select></label
          >
          <label
            ><span class="visually-hidden">可用状态</span
            ><select v-model="availability" aria-label="可用状态">
              <option value="">全部状态</option>
              <option v-for="(text, key) in states" :key="key" :value="key">
                {{ text }}
              </option>
            </select></label
          >
        </div>
        <p v-if="loading" class="blank-state" role="status">正在读取资源…</p>
        <div v-else-if="!rows.length" class="blank-state">
          <h3>
            {{ search || kind || availability ? "没有匹配的资源" : "暂无资源" }}
          </h3>
          <p>可调整筛选条件，或到对应资源页面配置。</p>
        </div>
        <article
          v-for="row in loading ? [] : rows"
          :key="row.resource_code"
          class="admin-row"
        >
          <div>
            <h3>
              {{ row.resource_name }}
              <span
                class="status-badge"
                :data-status="row.available ? 'SUCCEEDED' : 'CANCELLED'"
                >{{ states[row.availability] }}</span
              >
            </h3>
            <p class="muted">
              {{ names[row.resource_kind] }} · 资源{{
                row.active ? "启用" : "停用"
              }}
              · 授权{{ row.enabled ? "开启" : "关闭"
              }}<template v-if="row.resource_kind === 'MCP'">
                · {{ row.tools_count }} 个工具</template
              >
            </p>
            <p class="code-text">{{ row.resource_code }}</p>
          </div>
          <div class="row-actions">
            <button
              class="secondary-button"
              @click="go(routes[row.resource_kind] + '/' + row.resource_code)"
            >
              资源详情</button
            ><button
              v-if="can('tenant.manage')"
              class="secondary-button"
              :disabled="busy || readonly"
              @click="toggle(row)"
            >
              {{ row.enabled ? "撤销授权" : "开启授权" }}
            </button>
          </div>
        </article>
        <div class="pagination">
          <button
            class="secondary-button"
            :disabled="loading || busy || page === 0"
            @click="
              page--;
              refresh();
            "
          >
            上一页</button
          ><span>第 {{ page + 1 }} 页 · 每页最多 50 条</span
          ><button
            class="secondary-button"
            :disabled="loading || busy || rows.length < 50"
            @click="
              page++;
              refresh();
            "
          >
            下一页
          </button>
        </div>
      </section>
    </template>
  </main>
</template>
