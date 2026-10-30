<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { api, post } from "../api";
import { useWorkspace, date, type Row } from "../workspace";
import TenantResources from "./TenantResources.vue";
import MembersWorkspace from "./MembersWorkspace.vue";
import TenantSettings from "./TenantSettings.vue";
import UsersWorkspace from "./UsersWorkspace.vue";
import RolesWorkspace from "./RolesWorkspace.vue";
const props = defineProps<{ kind: string; code?: string }>();
const { identity, busy, readonly, can, go, act, load } = useWorkspace();
const rows = ref<Row[]>([]),
  roles = ref<Row[]>([
    { role_code: "MEMBER", role_name: "成员" },
    { role_code: "TENANT_ADMIN", role_name: "租户管理员" },
  ]),
  permissions = ref<Record<string, string>>({}),
  status = ref<Row>({}),
  search = ref(""),
  edit = ref(""),
  showForm = ref(false),
  resetting = ref(""),
  newPassword = ref(""),
  tenantName = ref("");
const auditActors = ref<Row[]>([]),
  auditDays = ref("0"),
  auditActor = ref(""),
  auditPage = ref(0);
let auditTimer: ReturnType<typeof setTimeout> | undefined;
let auditRequest = 0;
const form = ref<Row>({
  username: "",
  display_name: "",
  password: "",
  platform_admin: false,
  status: 1,
  role_code: "MEMBER",
  role_name: "",
  permissions: [],
});
const titles: Record<string, string> = {
  members: "成员管理",
  roles: "角色权限",
  users: "平台用户",
  resources: "资源授权",
  tenant: "租户设置",
  audit: "审计日志",
  integrations: "系统状态",
};
const filtered = computed(() =>
  props.kind === "audit"
    ? rows.value
    : rows.value.filter((r) =>
        JSON.stringify(r).toLowerCase().includes(search.value.toLowerCase()),
      ),
);
const editable = computed(
  () =>
    !readonly.value ||
    (["users", "tenant"].includes(props.kind) &&
      identity.value?.platform_admin),
);
const allowedPage = computed(() =>
  props.kind === "users"
    ? !!identity.value?.platform_admin
    : can(
        (
          {
            members: "members.manage",
            roles: "roles.manage",
            resources: "tenant.manage",
            tenant: "tenant.manage",
            audit: "audit.read",
            integrations: "tenant.manage",
          } as Record<string, string>
        )[props.kind],
      ),
);
async function refresh() {
  const request = ++auditRequest;
  if (!allowedPage.value) throw new Error("没有此页面的访问权限");
  if (props.kind === "tenant") {
    rows.value = identity.value?.tenants ?? [];
    return;
  }
  if (props.kind === "integrations") {
    status.value = await api<Row>("/api/v1/integration-status");
    return;
  }
  if (props.kind === "roles") {
    const v = await api<Row>("/api/v1/roles");
    rows.value = v.roles;
    roles.value = v.roles;
    permissions.value = v.permissions;
  } else {
    const loaded = await api<Row[]>(
      "/api/v1/" +
        props.kind +
        (props.kind === "audit"
          ? `?search=${encodeURIComponent(search.value)}&actor_code=${encodeURIComponent(auditActor.value)}&days=${auditDays.value}&limit=50&offset=${auditPage.value * 50}`
          : ""),
    );
    if (props.kind === "audit" && request !== auditRequest) return;
    rows.value = loaded;
    if (props.kind === "members" && can("roles.manage")) {
      const v = await api<Row>("/api/v1/roles");
      roles.value = v.roles;
    }
  }
}
function start(r?: Row) {
  edit.value = r?.user_code ?? r?.role_code ?? "";
  form.value = {
    username: r?.username ?? "",
    user_code: r?.user_code,
    display_name: r?.display_name ?? "",
    password: "",
    platform_admin: r?.platform_admin ?? false,
    status: r?.status ?? 1,
    role_code: r?.role_code ?? "MEMBER",
    role_name: r?.role_name ?? "",
    permissions: r?.permissions ? [...r.permissions] : [],
  };
  showForm.value = true;
}
async function save() {
  await act(async () => {
    const f = form.value;
    let payload: Row = {};
    if (props.kind === "users") {
      payload = edit.value
        ? {
            display_name: f.display_name,
            status: f.status,
            platform_admin: f.platform_admin,
          }
        : {
            username: f.username,
            display_name: f.display_name,
            password: f.password,
            platform_admin: f.platform_admin,
          };
    } else if (props.kind === "members")
      payload = {
        ...(f.user_code
          ? { user_code: f.user_code }
          : { username: f.username }),
        display_name: f.display_name || f.username,
        role_code: f.role_code,
        status: f.status,
      };
    else
      payload = {
        role_name: f.role_name,
        permissions: f.permissions,
        status: f.status,
      };
    await post(
      "/api/v1/" +
        props.kind +
        (edit.value && props.kind !== "members" ? "/" + edit.value : ""),
      payload,
      edit.value && props.kind !== "members" ? "PATCH" : "POST",
    );
    form.value.password = "";
    showForm.value = false;
    await refresh();
  }, "设置已保存");
}
async function tenantSave(r?: Row) {
  await act(async () => {
    await post(
      "/api/v1/tenants" + (r ? "/" + r.tenant_code : ""),
      {
        tenant_name: r ? r.tenant_name : tenantName.value,
        status: r ? r.status : 1,
      },
      r ? "PATCH" : "POST",
    );
    tenantName.value = "";
    identity.value = await api<Row>("/api/v1/me");
    await refresh();
    await load();
  }, "租户设置已保存");
}
watch([search, auditDays, auditActor], () => {
  if (props.kind !== "audit") return;
  clearTimeout(auditTimer);
  auditPage.value = 0;
  auditTimer = setTimeout(() => void act(refresh, "", true), 250);
});
onMounted(
  () =>
    !["resources", "members", "tenant", "users", "roles"].includes(
      props.kind,
    ) &&
    void act(
      async () => {
        if (props.kind === "audit")
          auditActors.value = await api<Row[]>("/api/v1/audit/actors");
        await refresh();
      },
      "",
      true,
    ),
);
onBeforeUnmount(() => {
  auditRequest++;
  clearTimeout(auditTimer);
});
</script>
<template>
  <TenantResources v-if="kind === 'resources'" />
  <MembersWorkspace v-else-if="kind === 'members'" :code="code" />
  <TenantSettings v-else-if="kind === 'tenant'" />
  <UsersWorkspace v-else-if="kind === 'users'" :code="code" />
  <RolesWorkspace v-else-if="kind === 'roles'" />
  <main v-else class="platform-main">
    <div class="page-heading">
      <div>
        <span class="eyebrow">ADMINISTRATION</span>
        <h1>{{ titles[kind] }}</h1>
        <p class="muted">
          {{
            kind === "users"
              ? "内置账号独立管理，再通过成员管理分配租户与角色。"
              : kind === "members"
                ? "将已有账号加入当前租户，并授予适当角色。"
                : kind === "roles"
                  ? "用细粒度权限定义工作空间中的职责。"
                  : kind === "resources"
                    ? "撤销资源授权会阻止后续执行使用该资源。"
                    : kind === "audit"
                      ? "查看配置、权限和任务操作的审计记录。"
                      : "管理工作空间与运行状态。"
          }}
        </p>
      </div>
      <button
        v-if="
          allowedPage &&
          editable &&
          ['members', 'roles', 'users'].includes(kind)
        "
        class="primary-button"
        :disabled="busy"
        @click="start()"
      >
        ＋
        {{
          kind === "users"
            ? "创建账号"
            : kind === "roles"
              ? "创建角色"
              : "添加成员"
        }}
      </button>
    </div>
    <p v-if="!allowedPage" class="inline-error">没有此页面的访问权限。</p>
    <template v-else>
      <section
        v-if="showForm && ['users', 'roles', 'members'].includes(kind)"
        class="workspace-panel compact-panel"
      >
        <div class="section-heading">
          <h2>
            {{ edit ? "编辑" : "新建"
            }}{{
              kind === "users" ? "账号" : kind === "roles" ? "角色" : "成员"
            }}
          </h2>
          <button
            class="secondary-button"
            :disabled="busy"
            @click="
              showForm = false;
              form.password = '';
            "
          >
            关闭
          </button>
        </div>
        <form class="stack-form" @submit.prevent="save">
          <fieldset
            class="plain-fieldset form-fields"
            :disabled="busy || !editable"
          >
            <template v-if="kind === 'users' || kind === 'members'"
              ><label
                >用户名<input
                  v-model="form.username"
                  :disabled="!!edit"
                  required
                  minlength="3"
                  maxlength="32"
                  pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,31}"
                  autocomplete="off"
                  placeholder="已有账号用户名" /></label
              ><label
                >显示名称<input
                  v-model="form.display_name"
                  :required="kind === 'users'"
                  maxlength="128" /></label
              ><template v-if="kind === 'users'"
                ><label v-if="!edit"
                  >初始密码<input
                    v-model="form.password"
                    type="password"
                    required
                    minlength="12"
                    maxlength="128"
                    autocomplete="new-password"
                    placeholder="至少 12 位字符" /></label
                ><label class="check-label"
                  ><input
                    v-model="form.platform_admin"
                    type="checkbox"
                  />平台管理员（可以管理所有租户与账号）</label
                ></template
              ><label v-else
                >角色<select v-model="form.role_code">
                  <option
                    v-for="r in roles.filter((r) => r.status !== 0)"
                    :key="r.role_code"
                    :value="r.role_code"
                  >
                    {{ r.role_name }}
                  </option>
                </select></label
              ></template
            >
            <template v-else
              ><label
                >角色名称<input
                  v-model="form.role_name"
                  required
                  maxlength="128"
              /></label>
              <fieldset class="selection-group">
                <legend>权限</legend>
                <label
                  v-for="(text, key) in permissions"
                  :key="key"
                  class="check-label"
                  ><input
                    v-model="form.permissions"
                    type="checkbox"
                    :value="key"
                    :disabled="!can(String(key))"
                  />{{ text }}</label
                >
              </fieldset></template
            >
            <label v-if="edit || kind === 'roles'"
              >状态<select v-model.number="form.status">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
          </fieldset>
          <button class="primary-button" :disabled="busy || !editable">
            保存{{ kind === "users" ? "账号" : "设置" }}
          </button>
        </form>
      </section>
      <section v-if="resetting" class="workspace-panel compact-panel">
        <h2>重置账号密码</h2>
        <p class="muted">保存后该账号所有设备的登录都会失效。</p>
        <form
          class="stack-form"
          @submit.prevent="
            act(async () => {
              await post(`/api/v1/users/${resetting}/password`, {
                password: newPassword,
              });
              resetting = '';
              newPassword = '';
            }, '密码已重置，原有登录已撤销')
          "
        >
          <label
            >新密码<input
              v-model="newPassword"
              type="password"
              required
              minlength="12"
              maxlength="128"
              autocomplete="new-password"
              :disabled="busy"
          /></label>
          <div class="row-actions">
            <button
              class="primary-button"
              :disabled="busy || newPassword.length < 12"
            >
              重置密码</button
            ><button
              class="secondary-button"
              type="button"
              :disabled="busy"
              @click="
                resetting = '';
                newPassword = '';
              "
            >
              取消
            </button>
          </div>
        </form>
      </section>
      <section
        v-if="['users', 'members', 'roles'].includes(kind)"
        class="workspace-panel list-panel"
      >
        <div class="list-toolbar">
          <label class="search-field"
            ><span class="visually-hidden">搜索</span
            ><input v-model="search" placeholder="搜索名称或账号…" /></label
          ><button
            class="secondary-button"
            :disabled="busy"
            @click="act(refresh)"
          >
            刷新
          </button>
        </div>
        <div v-if="!filtered.length" class="blank-state">
          <h3>暂无记录</h3>
          <p>
            点击上方按钮添加{{
              kind === "users" ? "账号" : kind === "roles" ? "角色" : "成员"
            }}。
          </p>
        </div>
        <article
          v-for="r in filtered"
          :key="r.user_code || r.role_code || r.member_code"
          class="admin-row"
        >
          <div>
            <h3>
              {{ r.display_name || r.role_name }}
              <span class="status-badge">{{
                r.status === 0 ? "停用" : "启用"
              }}</span>
            </h3>
            <p class="muted">
              {{
                kind === "users"
                  ? (r.username || "历史开发身份") +
                    (r.platform_admin ? " · 平台管理员" : "")
                  : kind === "members"
                    ? (r.username || r.user_code) +
                      " · " +
                      (roles.find((role) => role.role_code === r.role_code)
                        ?.role_name || r.role_code)
                    : r.permissions
                        .map((p: string) => permissions[p] || p)
                        .join("、")
              }}
            </p>
          </div>
          <div class="row-actions">
            <button
              v-if="editable && !r.builtin && (kind !== 'users' || r.username)"
              class="secondary-button"
              :disabled="busy"
              @click="start(r)"
            >
              编辑</button
            ><button
              v-if="kind === 'users' && r.username && editable"
              class="secondary-button"
              :disabled="busy"
              @click="resetting = r.user_code"
            >
              重置密码</button
            ><span v-if="r.builtin" class="muted">内置角色</span>
          </div>
        </article>
      </section>
      <template v-else-if="kind === 'tenant'"
        ><section
          v-if="identity?.platform_admin"
          class="workspace-panel compact-panel"
        >
          <h2>创建租户</h2>
          <form class="stack-form" @submit.prevent="tenantSave()">
            <label
              >租户名称<input
                v-model="tenantName"
                required
                maxlength="128"
                :disabled="busy" /></label
            ><button
              class="primary-button"
              :disabled="busy || !tenantName.trim()"
            >
              创建租户
            </button>
          </form>
        </section>
        <section class="workspace-panel">
          <article v-for="r in rows" :key="r.tenant_code" class="admin-row">
            <form class="tenant-form" @submit.prevent="tenantSave(r)">
              <label
                >租户名称<input
                  v-model="r.tenant_name"
                  required
                  maxlength="128"
                  :disabled="busy || !identity?.platform_admin" /></label
              ><label
                >状态<select
                  v-model.number="r.status"
                  :disabled="busy || !identity?.platform_admin"
                >
                  <option :value="1">启用</option>
                  <option :value="0">停用</option>
                </select></label
              ><button
                v-if="identity?.platform_admin"
                class="secondary-button"
                :disabled="busy"
              >
                保存</button
              ><code class="code-text">{{ r.tenant_code }}</code>
            </form>
          </article>
        </section></template
      >
      <section v-else-if="kind === 'audit'" class="workspace-panel list-panel">
        <div class="list-toolbar">
          <label
            ><span class="visually-hidden">审计日期</span
            ><select v-model="auditDays">
              <option value="0">全部日期</option>
              <option value="7">最近 7 天</option>
              <option value="30">最近 30 天</option>
            </select></label
          ><label
            ><span class="visually-hidden">审计操作者</span
            ><select v-model="auditActor">
              <option value="">全部操作者</option>
              <option
                v-for="a in auditActors"
                :key="a.user_code"
                :value="a.user_code"
              >
                {{ a.display_name }}
              </option>
            </select></label
          >
          <label class="search-field"
            ><span class="visually-hidden">搜索日志</span
            ><input
              v-model="search"
              placeholder="搜索操作路径、资源或姓名…" /></label
          ><button
            class="secondary-button"
            :disabled="busy"
            @click="act(refresh)"
          >
            刷新
          </button>
        </div>
        <p v-if="!filtered.length" class="blank-state">暂无审计记录。</p>
        <article v-for="r in filtered" :key="r.event_code" class="audit-row">
          <strong>{{ r.action_name || r.action }}</strong>
          <details class="version-detail">
            <summary>操作详情</summary>
            <p class="code-text">{{ r.action }}</p>
            <p class="muted">响应状态 {{ r.details?.status }}</p>
          </details>
          <p class="code-text">
            操作者 {{ r.actor_name || r.actor_code }} · {{ date(r.created_at) }}
          </p>
        </article>
        <div class="pagination">
          <button
            class="secondary-button"
            :disabled="busy || !auditPage"
            @click="
              act(async () => {
                auditPage--;
                await refresh();
              })
            "
          >
            上一页</button
          ><span>第 {{ auditPage + 1 }} 页 · 每页 50 条</span
          ><button
            class="secondary-button"
            :disabled="busy || rows.length < 50"
            @click="
              act(async () => {
                auditPage++;
                await refresh();
              })
            "
          >
            下一页
          </button>
        </div>
        <p class="muted">审计记录不保存密码或密钥。</p>
      </section>
      <section v-else-if="kind === 'integrations'" class="workspace-panel">
        <h2>内置账号</h2>
        <p class="status-badge">
          {{
            status.accounts?.enabled
              ? "账号密码登录已启用"
              : "本地开发免登录模式"
          }}
        </p>
        <p class="muted">用户、密码和登录会话由 Begin Agent 独立管理。</p>
        <h2>PMS 运行适配</h2>
        <p>
          PMS 目录托管：{{
            status.pms?.managed ? "已启用" : "本地运行，尚未登记托管"
          }}
        </p>
        <p>
          Worker：<span
            class="status-badge"
            :data-status="status.pms?.worker_ready ? 'SUCCEEDED' : 'FAILED'"
            >{{ status.pms?.worker_ready ? "就绪" : "未就绪" }}</span
          >
        </p>
        <h2>MCP 网络允许列表</h2>
        <p class="muted">
          {{ status.mcp_allowed_hosts?.join("、") || "尚未配置允许的服务主机" }}
        </p>
      </section>
    </template>
  </main>
</template>
