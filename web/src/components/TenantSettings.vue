<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api, post, selectTenant } from "../api";
import { date, useWorkspace, type Row } from "../workspace";
const { identity, busy, readonly, can, go, act, load } = useWorkspace();
const snapshot = ref<Row | null>(null),
  loading = ref(false),
  error = ref(""),
  newName = ref("");
const form = ref({ tenant_name: "", status: 1 });
const admin = computed(() => !!identity.value?.platform_admin),
  access = computed(() => can("tenant.manage"));
let alive = true,
  requestNumber = 0;
async function refresh() {
  if (!access.value || !identity.value?.tenant_code) return;
  const request = ++requestNumber;
  loading.value = true;
  try {
    const value = await api<Row>("/api/v1/tenant");
    if (!alive || request !== requestNumber) return;
    snapshot.value = value;
    form.value = {
      tenant_name: value.tenant.tenant_name,
      status: value.tenant.status,
    };
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "租户信息暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
async function sync() {
  identity.value = await api<Row>("/api/v1/me");
  selectTenant(identity.value.tenant_code || "");
  if (identity.value.tenant_code) await load();
  await refresh();
}
async function save() {
  if (
    !access.value ||
    busy.value ||
    !snapshot.value ||
    (!admin.value && readonly.value) ||
    !form.value.tenant_name.trim()
  )
    return;
  await act(async () => {
    await post(
      admin.value
        ? "/api/v1/tenants/" + snapshot.value!.tenant.tenant_code
        : "/api/v1/tenant",
      admin.value ? form.value : { tenant_name: form.value.tenant_name },
      "PATCH",
    );
    await sync();
  }, "租户设置已保存");
}
async function create() {
  if (!admin.value || busy.value || !newName.value.trim()) return;
  await act(async () => {
    await post("/api/v1/tenants", { tenant_name: newName.value, status: 1 });
    newName.value = "";
    await sync();
  }, "租户已创建，可从顶部切换");
}
onMounted(() => void refresh());
onBeforeUnmount(() => {
  alive = false;
  requestNumber++;
});
</script>
<template>
  <main class="platform-main tenant-settings">
    <div class="page-heading">
      <div>
        <span class="eyebrow">ADMINISTRATION / TENANT</span>
        <h1>租户设置</h1>
        <p class="muted">管理当前租户的信息，资源和成员分别在对应页面配置。</p>
      </div>
      <div v-if="access" class="row-actions">
        <button
          v-if="can('resources.read')"
          class="secondary-button"
          @click="go('resources')"
        >
          资源概况</button
        ><button
          v-if="can('audit.read')"
          class="secondary-button"
          @click="go('audit')"
        >
          审计记录</button
        ><button
          class="secondary-button"
          :disabled="loading || busy"
          @click="refresh"
        >
          刷新租户
        </button>
      </div>
    </div>
    <p v-if="!access" class="inline-error">没有租户管理权限。</p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}，请刷新重试。
      </p>
      <p v-if="loading && !snapshot" class="blank-state" role="status">
        正在读取租户…
      </p>
      <template v-if="snapshot">
        <section class="workspace-panel">
          <h2>{{ snapshot.tenant.tenant_name }}</h2>
          <p class="code-text">
            {{ snapshot.tenant.tenant_code }} ·
            {{ date(snapshot.tenant.created_at) }} 创建
          </p>
          <span
            class="status-badge"
            :data-status="
              snapshot.tenant.status === 1 ? 'SUCCEEDED' : 'CANCELLED'
            "
            >{{
              snapshot.tenant.status === 1 ? "正常" : "停用，仅可查看历史"
            }}</span
          >
        </section>
        <div class="metrics-grid">
          <article
            v-for="[key, text] in [
              ['members', '成员数量'],
              ['agents', 'Agent 数量'],
              ['resources', '资源总量'],
              ['available_resources', '可配置能力'],
            ]"
            :key="key"
            class="metric"
          >
            <span>{{ text }}</span
            ><strong>{{ snapshot[key] ?? "—" }}</strong
            ><small>{{
              snapshot[key] == null
                ? "当前权限不提供此项统计"
                : "当前租户的真实数据"
            }}</small>
          </article>
        </div>
        <p class="muted">
          启用的租户管理员成员 {{ snapshot.administrators ?? "—" }} 位 ·
          已有发布版本的 Agent {{ snapshot.published_agents ?? "—" }} 个
        </p>
        <section class="workspace-panel compact-panel">
          <h2>当前租户基本信息</h2>
          <form class="stack-form" @submit.prevent="save">
            <label
              >租户名称<input
                v-model="form.tenant_name"
                required
                maxlength="128"
                :disabled="busy || (readonly && !admin)" /></label
            ><label
              >租户编码<input :value="snapshot.tenant.tenant_code" readonly
            /></label>
            <p class="muted">编码不可修改，用于当前租户的数据隔离。</p>
            <label v-if="admin"
              >租户状态<select v-model.number="form.status" :disabled="busy">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <p v-else class="muted">
              租户启停由平台管理员管理；停用后保留历史只读。
            </p>
            <button
              class="primary-button"
              :disabled="
                busy || (readonly && !admin) || !form.tenant_name.trim()
              "
            >
              保存当前租户
            </button>
          </form>
        </section>
      </template>
      <p v-else-if="!identity?.tenant_code" class="blank-state">
        尚未选择租户。{{
          admin ? "可创建一个租户后从顶部切换。" : "请联系管理员加入租户。"
        }}
      </p>
      <details v-if="admin" class="workspace-panel tenant-create-section">
        <summary>创建新的租户</summary>
        <p class="muted">创建后可从顶栏切换，当前租户设置保持独立。</p>
        <form class="stack-form compact-panel" @submit.prevent="create">
          <label
            >新租户名称<input
              v-model="newName"
              required
              maxlength="128"
              :disabled="busy" /></label
          ><button class="primary-button" :disabled="busy || !newName.trim()">
            创建租户
          </button>
        </form>
      </details>
    </template>
  </main>
</template>
