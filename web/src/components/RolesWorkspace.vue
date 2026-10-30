<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, post } from "../api";
import { useWorkspace, type Row } from "../workspace";
const { identity, busy, readonly, can, go, act, load } = useWorkspace();
const access = computed(() => can("roles.manage"));
const rows = ref<Row[]>([]),
  permissions = ref<Record<string, string>>({});
const search = ref(""),
  status = ref(""),
  page = ref(0),
  loading = ref(false),
  error = ref("");
const showForm = ref(false),
  editing = ref<Row | null>(null);
const form = ref({ role_name: "", status: 1, permissions: [] as string[] });
const self = computed(
  () =>
    !identity.value?.platform_admin &&
    editing.value?.role_code === identity.value?.role,
);
const groups = computed(() => {
  const names: Record<string, string> = {
    tasks: "任务",
    agents: "Agent",
    runs: "运行",
    resources: "资源",
    members: "成员",
    roles: "角色",
    tenant: "租户",
    audit: "审计",
  };
  return Object.entries(names)
    .map(([prefix, name]) => ({
      name,
      entries: Object.entries(permissions.value).filter(([p]) =>
        p.startsWith(prefix + "."),
      ),
    }))
    .filter((g) => g.entries.length);
});
let alive = true,
  requestNumber = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
async function refresh() {
  if (!access.value) return;
  const request = ++requestNumber;
  loading.value = true;
  try {
    const params = new URLSearchParams({
      search: search.value,
      limit: "50",
      offset: String(page.value * 50),
    });
    if (status.value !== "") params.set("status", status.value);
    const value = await api<Row>("/api/v1/roles?" + params);
    if (!alive || request !== requestNumber) return;
    rows.value = value.roles;
    permissions.value = value.permissions;
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "角色信息暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
function start(row?: Row) {
  if (readonly.value || busy.value || (row && !row.editable)) return;
  editing.value = row || null;
  form.value = {
    role_name: row?.role_name || "",
    status: row?.status ?? 1,
    permissions: [...(row?.permissions || [])],
  };
  showForm.value = true;
}
async function save() {
  if (
    !access.value ||
    readonly.value ||
    busy.value ||
    (editing.value && !editing.value.editable)
  )
    return;
  await act(async () => {
    await post(
      "/api/v1/roles" + (editing.value ? "/" + editing.value.role_code : ""),
      form.value,
      editing.value ? "PATCH" : "POST",
    );
    const changedSelf = self.value;
    showForm.value = false;
    editing.value = null;
    if (changedSelf) {
      identity.value = await api<Row>("/api/v1/me");
      await load();
      if (!access.value) {
        go("overview");
        return;
      }
    }
    await refresh();
  }, "角色设置已保存");
}
watch([search, status], () => {
  clearTimeout(timer);
  page.value = 0;
  timer = setTimeout(() => void refresh(), 250);
});
void refresh();
onBeforeUnmount(() => {
  alive = false;
  requestNumber++;
  clearTimeout(timer);
});
</script>
<template>
  <main class="platform-main roles-workspace">
    <div class="page-heading">
      <div>
        <span class="eyebrow">ADMINISTRATION / ROLES</span>
        <h1>角色权限</h1>
        <p class="muted">
          角色定义功能权限，成员管理负责分配角色；内置角色只读。
        </p>
      </div>
      <div v-if="access" class="row-actions">
        <button
          class="primary-button"
          :disabled="busy || readonly || loading"
          @click="start()"
        >
          ＋ 创建角色</button
        ><button
          class="secondary-button"
          :disabled="busy || loading"
          @click="refresh"
        >
          刷新角色
        </button>
      </div>
    </div>
    <p v-if="!access" class="inline-error">没有角色管理权限。</p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}，可刷新重试。
      </p>
      <section v-if="showForm" class="workspace-panel role-editor">
        <div class="section-heading">
          <h2>{{ editing ? "编辑角色" : "创建角色" }}</h2>
          <button
            class="secondary-button"
            :disabled="busy"
            @click="
              showForm = false;
              editing = null;
            "
          >
            关闭编辑
          </button>
        </div>
        <p v-if="editing" class="muted">
          当前
          {{ editing.member_count ?? "—" }}
          条成员记录使用此角色。保存后影响后续鉴权，历史任务和版本不变。
        </p>
        <form class="stack-form" @submit.prevent="save">
          <fieldset
            class="plain-fieldset form-fields"
            :disabled="busy || readonly"
          >
            <label
              >角色名称<input v-model="form.role_name" required maxlength="128"
            /></label>
            <label
              >角色状态<select v-model.number="form.status" :disabled="self">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <p v-if="self" class="muted">
              不能停用当前使用的角色；撤销角色管理权限后会离开此页面。
            </p>
            <div class="role-permission-groups">
              <fieldset
                v-for="group in groups"
                :key="group.name"
                class="selection-group"
              >
                <legend>{{ group.name }}</legend>
                <label
                  v-for="[key, text] in group.entries"
                  :key="key"
                  class="check-label"
                  ><input
                    v-model="form.permissions"
                    type="checkbox"
                    :value="key"
                    :disabled="!can(key)"
                  />{{ text }}</label
                >
              </fieldset>
            </div>
            <button class="primary-button" :disabled="!form.role_name.trim()">
              保存角色设置
            </button>
          </fieldset>
        </form>
      </section>
      <section class="workspace-panel list-panel">
        <div class="list-toolbar member-filters">
          <label class="search-field"
            ><span class="visually-hidden">搜索角色</span
            ><input
              v-model="search"
              aria-label="搜索角色"
              maxlength="200"
              placeholder="搜索角色名称或编码…" /></label
          ><label
            ><span class="visually-hidden">筛选角色状态</span
            ><select v-model="status" aria-label="筛选角色状态">
              <option value="">全部角色状态</option>
              <option value="1">启用角色</option>
              <option value="0">停用角色</option>
            </select></label
          >
        </div>
        <p class="muted">
          成员统计需成员管理权限，无权限显示“—”。成员数量包括停用成员；“启用账号成员”同时检查账号和成员状态，不表示一定可访问。角色状态、平台身份与租户状态仍影响访问。窄屏可横向滚动查看完整矩阵。
        </p>
        <p v-if="loading && !rows.length" class="blank-state" role="status">
          正在读取角色…
        </p>
        <p v-if="!loading && !rows.length" class="blank-state">
          {{ search || status ? "没有匹配的角色，请调整筛选。" : "暂无角色。" }}
        </p>
        <div
          v-if="rows.length"
          class="roles-matrix-scroll"
          tabindex="0"
          aria-label="角色权限矩阵，可横向滚动"
        >
          <table class="roles-matrix">
            <caption class="visually-hidden">
              当前页角色的权限及成员统计
            </caption>
            <thead>
              <tr>
                <th scope="col">角色 / 状态</th>
                <th scope="col">成员数量</th>
                <th v-for="(text, key) in permissions" :key="key" scope="col">
                  {{ text }}
                </th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.role_code"
                :data-role-code="row.role_code"
              >
                <th scope="row">
                  <strong>{{ row.role_name }}</strong
                  ><span class="status-badge">{{
                    row.status === 1 ? "启用" : "停用"
                  }}</span
                  ><small class="code-text">{{ row.role_code }}</small>
                </th>
                <td>
                  <strong>{{ row.member_count ?? "—" }}</strong
                  ><small
                    >启用账号成员 {{ row.active_member_count ?? "—" }}</small
                  >
                </td>
                <td v-for="(text, key) in permissions" :key="key">
                  <span
                    :class="{ granted: row.permissions.includes(key) }"
                    :aria-label="
                      text +
                      (row.permissions.includes(key) ? '：已授予' : '：未授予')
                    "
                    >{{ row.permissions.includes(key) ? "✓" : "—" }}</span
                  >
                </td>
                <td>
                  <button
                    v-if="row.editable"
                    class="secondary-button"
                    :disabled="busy || readonly"
                    @click="start(row)"
                  >
                    编辑角色</button
                  ><span v-else class="muted">{{ row.blocked_reason }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="pagination">
          <button
            class="secondary-button"
            :disabled="busy || loading || !page"
            @click="
              page--;
              refresh();
            "
          >
            上一页</button
          ><span>第 {{ page + 1 }} 页 · 本页 {{ rows.length }} 条</span
          ><button
            class="secondary-button"
            :disabled="busy || loading || rows.length < 50"
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
