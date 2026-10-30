<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, post, selectTenant } from "../api";
import { date, useWorkspace, type Row } from "../workspace";
const props = defineProps<{ code?: string }>();
const { identity, busy, readonly, can, go, act, load } = useWorkspace();
const rows = ref<Row[]>([]),
  roles = ref<Row[]>([]),
  permissions = ref<Record<string, string>>({}),
  detail = ref<Row | null>(null);
const search = ref(""),
  role = ref(""),
  status = ref(""),
  page = ref(0),
  showNew = ref(false),
  loading = ref(false),
  error = ref("");
const form = ref({ username: "", role_code: "MEMBER", status: 1 });
const access = computed(() => can("members.manage"));
const selected = computed(() =>
  roles.value.find((r) => r.role_code === form.value.role_code),
);
const states: Record<string, string> = {
  ACTIVE: "可访问",
  ACCOUNT_DISABLED: "账号已停用",
  MEMBER_DISABLED: "成员已停用",
  ROLE_DISABLED: "角色已停用",
};
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
      role_code: role.value,
      limit: "50",
      offset: String(page.value * 50),
    });
    if (status.value !== "") params.set("status", status.value);
    const [directory, value] = await Promise.all([
      api<Row>("/api/v1/members/roles"),
      api<Row | Row[]>(
        "/api/v1/members" + (props.code ? "/" + props.code : "?" + params),
      ),
    ]);
    if (!alive || request !== requestNumber) return;
    roles.value = directory.roles;
    permissions.value = directory.permissions;
    if (props.code) {
      detail.value = value as Row;
      form.value = {
        username: detail.value.username || "",
        role_code: detail.value.role_code,
        status: detail.value.status,
      };
    } else rows.value = value as Row[];
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "成员信息暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
function start() {
  form.value = {
    username: "",
    role_code:
      roles.value.find((r) => r.role_code === "MEMBER" && r.assignable)
        ?.role_code ||
      roles.value.find((r) => r.assignable)?.role_code ||
      "",
    status: 1,
  };
  showNew.value = true;
}
async function save() {
  if (
    busy.value ||
    readonly.value ||
    !access.value ||
    !selected.value?.assignable
  )
    return;
  await act(async () => {
    const member = await post<Row>("/api/v1/members", {
      ...(detail.value
        ? { user_code: detail.value.user_code }
        : { username: form.value.username }),
      display_name: detail.value?.display_name || form.value.username,
      role_code: form.value.role_code,
      status: form.value.status,
    });
    showNew.value = false;
    if (
      member.user_code === identity.value?.user_code &&
      !identity.value?.platform_admin
    ) {
      selectTenant("");
      identity.value = await api<Row>("/api/v1/me");
      selectTenant(identity.value.tenant_code || "");
      if (identity.value.tenant_code) await load();
      go("overview");
    } else if (props.code) await refresh();
    else go("members/" + member.member_code);
  }, "成员设置已保存");
}
watch([search, role, status], () => {
  if (props.code) return;
  clearTimeout(timer);
  page.value = 0;
  timer = setTimeout(() => void refresh(), 250);
});
watch(
  () => props.code,
  () => {
    detail.value = null;
    showNew.value = false;
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
  <main class="platform-main members-workspace">
    <div class="page-heading">
      <div>
        <button v-if="code" class="text-button" @click="go('members')">
          ← 成员管理</button
        ><span v-else class="eyebrow">ADMINISTRATION / MEMBERS</span>
        <h1>{{ code ? "成员权限" : "成员管理" }}</h1>
        <p class="muted">角色决定功能权限，账号与成员状态分别管理。</p>
      </div>
      <div v-if="access" class="row-actions">
        <button
          v-if="!code && !readonly"
          class="primary-button"
          :disabled="busy || loading"
          @click="start"
        >
          ＋ 添加成员</button
        ><button
          class="secondary-button"
          :disabled="loading || busy"
          @click="refresh"
        >
          刷新成员
        </button>
      </div>
    </div>
    <p v-if="!access" class="inline-error">没有成员管理权限。</p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}，可刷新重试。
      </p>
      <p
        v-if="loading && !detail && !rows.length"
        class="blank-state"
        role="status"
      >
        正在读取成员…
      </p>
      <section v-if="detail" class="workspace-panel">
        <h2>
          {{ detail.display_name }}
          <span class="status-badge">{{ states[detail.access_state] }}</span>
        </h2>
        <p class="muted">
          {{ detail.username || detail.user_code }} · {{ detail.role_name }} ·
          {{ date(detail.created_at) }} 加入
        </p>
        <p class="muted">
          账号{{ detail.account_status === 1 ? "启用" : "停用" }} · 成员{{
            detail.status === 1 ? "启用" : "停用"
          }}
          · 角色{{ detail.role_status === 1 ? "启用" : "停用" }}
        </p>
        <p v-if="detail.platform_admin" class="muted">
          平台管理员使用平台管理权限，成员角色启停不撤销其平台身份。
        </p>
        <p class="code-text">
          {{ detail.member_code }} · {{ detail.user_code }}
        </p>
        <h3>当前实际权限</h3>
        <p class="muted">
          权限来源：{{ detail.permission_source }}。{{
            detail.tenant_readonly ? "租户已停用，写操作仍受限制。" : ""
          }}
        </p>
        <div class="permission-chips">
          <span
            v-for="permission in detail.effective_permissions"
            :key="permission"
            class="status-badge"
            >{{ permissions[permission] || permission }}</span
          >
        </div>
        <p v-if="!detail.effective_permissions.length" class="muted">
          当前访问状态不提供租户权限。
        </p>
        <p class="muted">
          读取范围为当前租户内获准接口；任务操作仍遵循创建者或任务管理权限。
        </p>
      </section>
      <section v-if="detail || showNew" class="workspace-panel compact-panel">
        <div class="section-heading">
          <h2>{{ detail ? "编辑成员设置" : "添加已有账号" }}</h2>
          <button
            v-if="!detail"
            class="secondary-button"
            @click="showNew = false"
          >
            关闭
          </button>
        </div>
        <form class="stack-form" @submit.prevent="save">
          <fieldset
            class="plain-fieldset form-fields"
            :disabled="
              busy || readonly || (!!detail && detail.account_status !== 1)
            "
          >
            <label v-if="!detail"
              >用户名<input
                v-model="form.username"
                required
                minlength="3"
                maxlength="32"
                pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,31}"
                autocomplete="off"
                placeholder="已由平台管理员创建的账号"
            /></label>
            <label
              >分配角色<select
                v-model="form.role_code"
                aria-label="分配角色"
                required
              >
                <option
                  v-for="r in roles"
                  :key="r.role_code"
                  :value="r.role_code"
                  :disabled="!r.assignable"
                >
                  {{ r.role_name }}{{ r.assignable ? "" : "（不可授予）" }}
                </option>
              </select></label
            >
            <p v-if="selected && !selected.assignable" class="muted">
              {{ selected.blocked_reason }}
            </p>
            <div class="role-preview">
              <h3>所选角色权限</h3>
              <div class="permission-chips">
                <span
                  v-for="permission in selected?.permissions || []"
                  :key="permission"
                  class="status-badge"
                  >{{ permissions[permission] || permission }}</span
                >
              </div>
              <p v-if="selected && !selected.permissions.length" class="muted">
                该角色没有功能权限。
              </p>
            </div>
            <label
              >成员状态<select
                v-model.number="form.status"
                aria-label="成员状态"
              >
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <button class="primary-button" :disabled="!selected?.assignable">
              保存成员设置
            </button>
          </fieldset>
        </form>
        <p v-if="detail?.account_status === 0" class="muted">
          账号已停用，请由平台管理员恢复后再变更成员设置。
        </p>
        <p v-if="!detail" class="muted">
          这里只分配租户角色，不修改账号资料。需要新账号时请联系平台管理员。
        </p>
      </section>
      <section v-if="!code" class="workspace-panel list-panel">
        <div class="list-toolbar member-filters">
          <label class="search-field"
            ><span class="visually-hidden">搜索成员</span
            ><input
              v-model="search"
              aria-label="搜索成员"
              maxlength="200"
              placeholder="搜索姓名、账号或角色…" /></label
          ><label
            ><span class="visually-hidden">筛选角色</span
            ><select v-model="role" aria-label="筛选角色">
              <option value="">全部角色</option>
              <option
                v-for="r in roles"
                :key="r.role_code"
                :value="r.role_code"
              >
                {{ r.role_name }}
              </option>
            </select></label
          ><label
            ><span class="visually-hidden">筛选成员状态</span
            ><select v-model="status" aria-label="筛选成员状态">
              <option value="">全部成员状态</option>
              <option value="1">启用成员</option>
              <option value="0">停用成员</option>
            </select></label
          >
        </div>
        <div v-if="!loading && !rows.length" class="blank-state">
          {{
            search || role || status
              ? "没有匹配的成员，请调整筛选。"
              : "暂无成员，可添加已有平台账号。"
          }}
        </div>
        <article v-for="row in rows" :key="row.member_code" class="admin-row">
          <div>
            <h3>
              {{ row.display_name }}
              <span class="status-badge">{{ states[row.access_state] }}</span>
            </h3>
            <p class="muted">
              {{ row.username || row.user_code }} · {{ row.role_name }}
            </p>
            <p class="muted">
              {{ date(row.created_at) }} 加入 · 成员{{
                row.status === 1 ? "启用" : "停用"
              }}
            </p>
          </div>
          <button
            class="secondary-button"
            @click="go('members/' + row.member_code)"
          >
            成员权限
          </button>
        </article>
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
