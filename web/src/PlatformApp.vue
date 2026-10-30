<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import ChatView from "./App.vue";
import WorkspacePage from "./components/WorkspacePage.vue";
import AgentEditor from "./components/AgentEditor.vue";
import TaskWorkspace from "./components/TaskWorkspace.vue";
import ResourceEditor from "./components/ResourceEditor.vue";
import AdminView from "./components/AdminView.vue";
import { activeTenant, api, post, selectTenant } from "./api";
import { workspaceKey, type Row } from "./workspace";
import "./platform.css";
const identity = ref<Row | null>(null),
  data = ref<Record<string, Row[]>>({}),
  busy = ref(false),
  loading = ref(true);
const error = ref(""),
  notice = ref(""),
  mode = ref("password"),
  setup = ref(false),
  needsLogin = ref(false);
const collapsed = ref(localStorage.getItem("agent.sidebar") === "collapsed");
const path = ref(location.hash.slice(2) || "overview"),
  parts = computed(() => path.value.split("/")),
  section = computed(() => parts.value[0] || "overview");
const login = ref({ username: "", password: "", display_name: "" }),
  password = ref({ old_password: "", password: "" });
const readonly = computed(
  () =>
    identity.value?.tenants.find(
      (t: Row) => t.tenant_code === activeTenant.value,
    )?.status === 0,
);
const can = (permission: string) => {
  const p = identity.value;
  return (
    !!p &&
    (p.platform_admin ||
      p.role === "TENANT_ADMIN" ||
      p.permissions?.includes(permission) ||
      (!p.permissions &&
        [
          "tasks.read",
          "tasks.create",
          "agents.read",
          "runs.read",
          "resources.read",
        ].includes(permission)))
  );
};
const menus = [
  {
    section: "工作空间",
    items: [
      ["overview", "◈", "概览", ""],
      ["tasks", "▤", "任务", "tasks.read"],
      ["agents", "◇", "Agent", "agents.read"],
      ["runs", "▷", "运行记录", "runs.read"],
    ],
  },
  {
    section: "能力资源",
    items: [
      ["models", "⬡", "模型", "resources.read"],
      ["tools", "⌘", "工具", "resources.read"],
      ["mcps", "⇄", "MCP 服务", "resources.read"],
      ["skills", "✧", "Skill", "resources.read"],
    ],
  },
  {
    section: "组织管理",
    items: [
      ["members", "♙", "成员", "members.manage"],
      ["roles", "⚑", "角色权限", "roles.manage"],
      ["resources", "▦", "资源概况", "resources.read"],
      ["tenant", "⚙", "租户设置", "tenant.manage"],
      ["audit", "≡", "审计日志", "audit.read"],
      ["users", "♧", "平台用户", "platform"],
      ["integrations", "⇥", "系统状态", "tenant.manage"],
    ],
  },
];
const foldedGroups = ref<string[]>(readFoldedGroups());
function readFoldedGroups(): string[] {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem("agent.nav-groups") ?? "[]",
    );
    return Array.isArray(value)
      ? value.filter(
          (v): v is string =>
            typeof v === "string" && menus.some((m) => m.section === v),
        )
      : [];
  } catch {
    return [];
  }
}
const visibleMenus = computed(() =>
  menus
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          !item[3] ||
          (item[3] === "platform"
            ? identity.value?.platform_admin
            : can(item[3])),
      ),
    }))
    .filter((group) => group.items.length),
);
function toggleGroup(name: string) {
  foldedGroups.value = foldedGroups.value.includes(name)
    ? foldedGroups.value.filter((v) => v !== name)
    : [...foldedGroups.value, name];
  localStorage.setItem("agent.nav-groups", JSON.stringify(foldedGroups.value));
}
watch(section, (value) => {
  const group = menus.find((m) => m.items.some((item) => item[0] === value));
  if (group && foldedGroups.value.includes(group.section))
    toggleGroup(group.section);
});
const title = computed(
  () =>
    menus.flatMap((m) => m.items).find((m) => m[0] === section.value)?.[2] ??
    (section.value === "account" ? "我的账号" : "模型调试"),
);
const roleLabel = computed(() => {
  if (identity.value?.platform_admin) return "平台管理员";
  return (
    identity.value?.role_name ||
    ({ TENANT_ADMIN: "租户管理员", MEMBER: "成员" } as Record<string, string>)[
      identity.value?.role ?? ""
    ] ||
    "自定义角色"
  );
});
function go(value: string) {
  location.hash = "/" + value;
  path.value = value;
  error.value = "";
  notice.value = "";
}
function route() {
  const next = location.hash.slice(2) || "overview";
  if (next !== path.value) {
    path.value = next;
    error.value = "";
    notice.value = "";
  }
}
function expired() {
  identity.value = null;
  data.value = {};
  needsLogin.value = true;
}
let pendingActions = 0;
async function act(fn: () => Promise<void>, message = "", initialize = false) {
  if (busy.value && !initialize) return;
  pendingActions++;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await fn();
    notice.value = message;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    pendingActions--;
    busy.value = pendingActions > 0;
  }
}
async function catalog(kind: string, url: string) {
  const first = await api<Row[]>(url);
  if (
    !["tools", "mcps", "skills", "resources", "agents"].includes(kind) ||
    first.length < 50
  )
    return first;
  const all = [...first];
  const seen = new Set(
    first.map(
      (r) =>
        r.tool_code ||
        r.mcp_code ||
        r.skill_code ||
        r.resource_code ||
        r.agent_code,
    ),
  );
  let offset = first.length;
  while (true) {
    const page = await api<Row[]>(url + `?limit=100&offset=${offset}`);
    if (!page.length) break;
    const fresh = page.filter(
      (r) =>
        !seen.has(
          r.tool_code ||
            r.mcp_code ||
            r.skill_code ||
            r.resource_code ||
            r.agent_code,
        ),
    );
    if (!fresh.length) throw new Error("资源分页未前进，请重新加载");
    for (const r of fresh) {
      all.push(r);
      seen.add(
        r.tool_code ||
          r.mcp_code ||
          r.skill_code ||
          r.resource_code ||
          r.agent_code,
      );
    }
    offset += page.length;
    if (page.length < 100) break;
  }
  return all;
}
async function load() {
  const requests: [string, string][] = [];
  if (can("agents.read")) requests.push(["agents", "/api/v1/agents"]);
  if (can("tasks.read")) requests.push(["tasks", "/api/v1/tasks"]);
  if (can("resources.read"))
    for (const kind of ["models", "tools", "mcps", "skills", "resources"])
      requests.push([
        kind,
        "/api/v1/" + (kind === "models" ? "model-profiles" : kind),
      ]);
  const result = await Promise.all(
    requests.map(
      async ([kind, url]) => [kind, await catalog(kind, url)] as const,
    ),
  );
  data.value = Object.fromEntries(result);
}
async function boot() {
  loading.value = true;
  error.value = "";
  needsLogin.value = false;
  try {
    const config = await api<Row>("/api/v1/auth/config");
    mode.value = config.mode;
    setup.value = !!config.setup_required;
    if (setup.value) {
      identity.value = null;
      return;
    }
    try {
      identity.value = await api<Row>("/api/v1/me");
    } catch (e) {
      if (!activeTenant.value || needsLogin.value) throw e;
      selectTenant("");
      identity.value = await api<Row>("/api/v1/me");
    }
    selectTenant(identity.value!.tenant_code ?? "");
    if (activeTenant.value) await load();
    if (path.value === "login") go("overview");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
async function authenticate() {
  await act(async () => {
    const values = { ...login.value };
    await post(
      "/api/v1/auth/" + (setup.value ? "setup" : "login"),
      setup.value
        ? values
        : { username: values.username, password: values.password },
    );
    login.value.password = "";
    selectTenant("");
    await boot();
    if (identity.value) go("overview");
  });
}
async function changeTenant(event: Event) {
  await act(async () => {
    selectTenant((event.target as HTMLSelectElement).value);
    data.value = {};
    identity.value = await api<Row>("/api/v1/me");
    await load();
    go("overview");
  });
}
async function logout(all = false) {
  await act(async () => {
    await post("/api/v1/auth/" + (all ? "logout-all" : "logout"), {});
    selectTenant("");
    expired();
    go("login");
  });
}
function toggle() {
  collapsed.value = !collapsed.value;
  localStorage.setItem(
    "agent.sidebar",
    collapsed.value ? "collapsed" : "expanded",
  );
}
provide(workspaceKey, { identity, data, busy, readonly, can, go, load, act });
onMounted(() => {
  window.addEventListener("hashchange", route);
  window.addEventListener("agent-auth-expired", expired);
  void boot();
});
onBeforeUnmount(() => {
  window.removeEventListener("hashchange", route);
  window.removeEventListener("agent-auth-expired", expired);
});
</script>
<template>
  <div v-if="loading" class="auth-shell">
    <p role="status">正在加载 Begin Agent…</p>
  </div>
  <div v-else-if="!identity" class="auth-shell">
    <div class="auth-brand">
      <span class="platform-logo">B</span><strong>Begin Agent</strong>
    </div>
    <section class="auth-card">
      <span class="eyebrow">{{ setup ? "开始使用" : "欢迎回来" }}</span>
      <h1>
        {{
          setup
            ? "创建你的管理员账号"
            : needsLogin || !error
              ? "登录工作空间"
              : "工作台暂不可用"
        }}
      </h1>
      <p class="muted">
        {{
          setup
            ? "设置平台首个管理员，现有租户和模型配置将保留。"
            : "使用 Begin Agent 的独立账号继续。"
        }}
      </p>
      <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
      <form
        v-if="mode === 'password' && (setup || needsLogin || !error)"
        class="stack-form"
        @submit.prevent="authenticate"
      >
        <label v-if="setup"
          >显示名称<input
            v-model="login.display_name"
            required
            maxlength="128"
            autocomplete="name"
            :disabled="busy"
        /></label>
        <label
          >用户名<input
            v-model="login.username"
            required
            pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,31}"
            minlength="3"
            maxlength="32"
            autocomplete="username"
            :disabled="busy"
            placeholder="3–32 位字母、数字、点、下划线或横线"
        /></label>
        <label
          >密码<input
            v-model="login.password"
            type="password"
            required
            minlength="12"
            maxlength="128"
            :autocomplete="setup ? 'new-password' : 'current-password'"
            :disabled="busy"
            placeholder="至少 12 位字符"
        /></label>
        <button
          class="primary-button"
          :disabled="
            busy ||
            !login.username ||
            login.password.length < 12 ||
            (setup && !login.display_name)
          "
        >
          {{ busy ? "正在处理…" : setup ? "创建账号并进入" : "登录" }}
        </button>
      </form>
      <button
        v-if="error && !needsLogin && !setup"
        class="secondary-button"
        @click="boot"
      >
        重新加载
      </button>
      <p class="auth-note">
        {{
          setup
            ? "仅首次使用需要初始化。请妥善保存管理员密码。"
            : "忘记密码请联系平台管理员重置。"
        }}
      </p>
    </section>
  </div>
  <div v-else class="platform-shell" :class="{ collapsed }">
    <aside class="sidebar">
      <a href="#/overview" class="platform-brand" aria-label="Begin Agent 首页"
        ><span class="platform-logo">B</span
        ><strong>Begin Agent<small>智能协作工作空间</small></strong></a
      >
      <nav class="sidebar-nav" aria-label="工作台导航">
        <div
          v-for="group in visibleMenus"
          :key="group.section"
          class="nav-group"
        >
          <button
            class="nav-caption nav-group-toggle"
            :aria-expanded="!foldedGroups.includes(group.section)"
            :aria-controls="'nav-group-' + group.section"
            :aria-label="
              (foldedGroups.includes(group.section) ? '展开' : '收起') +
              group.section
            "
            @click="toggleGroup(group.section)"
          >
            <span>{{ group.section }}</span>
            <svg
              class="nav-chevron"
              width="14"
              height="14"
              viewBox="0 0 16 16"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="m4 6 4 4 4-4"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <div
            :id="'nav-group-' + group.section"
            class="nav-group-items"
            :class="{ 'is-collapsed': foldedGroups.includes(group.section) }"
          >
            <a
              v-for="item in group.items"
              :key="item[0]"
              :href="'#/' + item[0]"
              :title="item[2]"
              :class="{ active: section === item[0] }"
              :aria-current="section === item[0] ? 'page' : undefined"
              ><span class="nav-icon">{{ item[1] }}</span
              ><span class="nav-label">{{ item[2] }}</span></a
            >
          </div>
        </div>
      </nav>
      <button
        class="sidebar-collapse"
        :aria-label="collapsed ? '展开导航' : '收起导航'"
        @click="toggle"
      >
        <span>{{ collapsed ? "»" : "«" }}</span
        ><span class="nav-label">收起导航</span>
      </button>
      <a href="#/account" class="sidebar-user" title="我的账号"
        ><span class="user-avatar">{{
          (identity.username || "B").slice(0, 1).toUpperCase()
        }}</span
        ><span class="nav-label"
          >{{ identity.username || "本地开发者"
          }}<small>{{ roleLabel }}</small></span
        ></a
      >
    </aside>
    <div class="workspace-body">
      <header class="platform-header">
        <div class="breadcrumb">
          工作空间 <span>/</span> <strong>{{ title }}</strong>
        </div>
        <div class="platform-account">
          <label class="visually-hidden" for="tenant-switch">当前租户</label
          ><select
            id="tenant-switch"
            :value="activeTenant"
            :disabled="busy"
            @change="changeTenant"
          >
            <option v-if="!identity.tenants.length" value="">
              尚未加入租户
            </option>
            <option
              v-for="t in identity.tenants"
              :key="t.tenant_code"
              :value="t.tenant_code"
            >
              {{ t.tenant_name }}
            </option></select
          ><button
            class="secondary-button account-button"
            @click="go('account')"
            aria-label="我的账号"
          >
            <span>{{ identity.username || "账号" }}</span>
          </button>
        </div>
      </header>
      <p v-if="readonly" class="platform-feedback">
        租户已停用，可查看历史配置、任务和运行记录。
      </p>
      <p v-if="error" class="platform-feedback failure" role="alert">
        {{ error }}
      </p>
      <p v-if="notice" class="platform-feedback" role="status">{{ notice }}</p>
      <div
        v-if="
          !activeTenant &&
          section !== 'account' &&
          section !== 'tenant' &&
          section !== 'users'
        "
        class="platform-main"
      >
        <div class="workspace-panel">
          <h1>尚未加入租户</h1>
          <p class="muted">
            请联系管理员，将账号 {{ identity.username }} 加入工作空间。
          </p>
          <button
            v-if="identity?.platform_admin"
            class="primary-button"
            @click="go('tenant')"
          >
            创建租户
          </button>
        </div>
      </div>
      <main v-else-if="section === 'account'" class="platform-main">
        <div class="page-heading">
          <div>
            <span class="eyebrow">ACCOUNT</span>
            <h1>我的账号</h1>
            <p class="muted">
              {{ identity.username || "本地开发者" }} · 独立账号体系
            </p>
          </div>
        </div>
        <section class="workspace-panel compact-panel">
          <template v-if="mode === 'password'"
            ><h2>修改密码</h2>
            <form
              class="stack-form"
              @submit.prevent="
                act(async () => {
                  await post('/api/v1/auth/password', password);
                  password = { old_password: '', password: '' };
                }, '密码已修改，其他设备的登录已失效')
              "
            >
              <label
                >原密码<input
                  v-model="password.old_password"
                  type="password"
                  autocomplete="current-password"
                  required
                  :disabled="busy" /></label
              ><label
                >新密码<input
                  v-model="password.password"
                  type="password"
                  autocomplete="new-password"
                  minlength="12"
                  maxlength="128"
                  required
                  :disabled="busy"
                  placeholder="至少 12 位字符" /></label
              ><button
                class="primary-button"
                :disabled="
                  busy ||
                  password.password.length < 12 ||
                  !password.old_password
                "
              >
                修改密码
              </button>
            </form>
            <div class="row-actions">
              <button
                class="secondary-button"
                :disabled="busy"
                @click="logout()"
              >
                退出登录</button
              ><button
                class="secondary-button"
                :disabled="busy"
                @click="logout(true)"
              >
                退出全部设备
              </button>
            </div></template
          >
          <p v-else class="muted">当前为显式开启的本地开发免登录模式。</p>
        </section>
      </main>
      <div v-else-if="section === 'model-chat'" class="platform-chat">
        <p v-if="readonly">租户已停用，模型调用已停止。</p>
        <ChatView
          v-else
          :key="activeTenant"
          :can-manage="can('resources.manage')"
          :unified="mode !== 'local'"
          embedded
        />
      </div>
      <TaskWorkspace
        v-else-if="section === 'tasks' && parts[1]"
        :key="activeTenant + path"
        :code="parts[1]"
      />
      <AgentEditor
        v-else-if="section === 'agents' && parts[1]"
        :key="activeTenant + path"
        :code="parts[1]"
        :tab="parts[2] || 'config'"
      />
      <ResourceEditor
        v-else-if="
          ['models', 'tools', 'mcps', 'skills'].includes(section) && parts[1]
        "
        :key="activeTenant + path"
        :kind="section"
        :code="parts[1]"
      />
      <AdminView
        v-else-if="
          [
            'members',
            'roles',
            'users',
            'resources',
            'tenant',
            'audit',
            'integrations',
          ].includes(section)
        "
        :key="activeTenant + path"
        :kind="section"
        :code="parts[1]"
      />
      <WorkspacePage
        v-else
        :key="activeTenant + path"
        :kind="section"
        :code="parts[1]"
      />
    </div>
  </div>
</template>
