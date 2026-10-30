<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { activeTenant, api, post, selectTenant } from "../api";
import { date, useWorkspace, type Row } from "../workspace";
const props = defineProps<{ code?: string }>();
const { identity, data, busy, go, act, load } = useWorkspace();
const access = computed(() => !!identity.value?.platform_admin);
const creating = computed(() => props.code === "new");
const rows = ref<Row[]>([]),
  detail = ref<Row | null>(null);
const search = ref(""),
  status = ref(""),
  admin = ref(""),
  page = ref(0);
const loading = ref(false),
  error = ref(""),
  resetting = ref(false),
  newPassword = ref("");
const form = ref({
  username: "",
  display_name: "",
  password: "",
  status: 1,
  platform_admin: false,
});
const self = computed(
  () => detail.value?.user_code === identity.value?.user_code,
);
let alive = true,
  requestNumber = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
async function refresh() {
  if (!access.value || creating.value) return;
  const request = ++requestNumber;
  loading.value = true;
  try {
    const params = new URLSearchParams({
      search: search.value,
      limit: "50",
      offset: String(page.value * 50),
    });
    if (status.value !== "") params.set("status", status.value);
    if (admin.value !== "") params.set("platform_admin", admin.value);
    const value = await api<Row | Row[]>(
      "/api/v1/users" + (props.code ? "/" + props.code : "?" + params),
    );
    if (!alive || request !== requestNumber) return;
    if (props.code) {
      detail.value = value as Row;
      form.value = {
        username: detail.value.username || "",
        display_name: detail.value.display_name,
        password: "",
        status: detail.value.status,
        platform_admin: detail.value.platform_admin,
      };
    } else rows.value = value as Row[];
    error.value = "";
  } catch (e) {
    if (alive && request === requestNumber)
      error.value = e instanceof Error ? e.message : "用户信息暂不可用";
  } finally {
    if (alive && request === requestNumber) loading.value = false;
  }
}
async function syncSelf() {
  try {
    identity.value = await api<Row>("/api/v1/me");
  } catch (e) {
    // A platform administrator may have selected a tenant they do not belong to.
    // After demotion, resolve their own default tenant instead.
    if (!identity.value || !activeTenant.value) throw e;
    selectTenant("");
    identity.value = await api<Row>("/api/v1/me");
  }
  selectTenant(identity.value.tenant_code || "");
  if (identity.value.tenant_code) await load();
  else data.value = {};
  if (!identity.value.platform_admin) go("overview");
}

async function save() {
  if (
    !access.value ||
    busy.value ||
    loading.value ||
    (!creating.value && !detail.value?.username)
  )
    return;
  await act(async () => {
    const f = form.value;
    const value = await post<Row>(
      "/api/v1/users" + (creating.value ? "" : "/" + props.code),
      creating.value
        ? {
            username: f.username,
            display_name: f.display_name,
            password: f.password,
            platform_admin: f.platform_admin,
          }
        : {
            display_name: f.display_name,
            status: f.status,
            platform_admin: f.platform_admin,
          },
      creating.value ? "POST" : "PATCH",
    );
    form.value.password = "";
    if (self.value) {
      await syncSelf();
      if (!access.value) return;
    }
    if (creating.value) go("users/" + value.user_code);
    else await refresh();
  }, "账号设置已保存");
}
async function resetPassword() {
  if (
    !access.value ||
    busy.value ||
    !detail.value?.username ||
    newPassword.value.length < 12
  )
    return;
  await act(async () => {
    await post("/api/v1/users/" + props.code + "/password", {
      password: newPassword.value,
    });
    newPassword.value = "";
    resetting.value = false;
    if (self.value) await syncSelf();
  }, "密码已重置，该账号原有登录已撤销");
}
watch([search, status, admin], () => {
  if (props.code) return;
  clearTimeout(timer);
  page.value = 0;
  timer = setTimeout(() => void refresh(), 250);
});
watch(
  () => props.code,
  () => {
    requestNumber++;
    loading.value = false;
    detail.value = null;
    error.value = "";
    resetting.value = false;
    newPassword.value = "";
    form.value = {
      username: "",
      display_name: "",
      password: "",
      status: 1,
      platform_admin: false,
    };
    void refresh();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  alive = false;
  requestNumber++;
  clearTimeout(timer);
  form.value.password = "";
  newPassword.value = "";
});
</script>
<template>
  <main class="platform-main users-workspace">
    <div class="page-heading">
      <div>
        <button v-if="code" class="text-button" @click="go('users')">
          ← 平台用户</button
        ><span v-else class="eyebrow">ADMINISTRATION / USERS</span>
        <h1>{{ creating ? "创建账号" : code ? "用户详情" : "平台用户" }}</h1>
        <p class="muted">账号属于平台，加入租户和分配角色请使用成员管理。</p>
      </div>
      <div v-if="access" class="row-actions">
        <button
          v-if="!code"
          class="primary-button"
          :disabled="busy"
          @click="go('users/new')"
        >
          ＋ 创建账号
        </button>
        <button
          v-if="!creating"
          class="secondary-button"
          :disabled="busy || loading"
          @click="refresh"
        >
          刷新用户
        </button>
      </div>
    </div>
    <p v-if="!access" class="inline-error">需要平台管理员权限。</p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        {{ error }}，可刷新重试。
      </p>
      <p
        v-if="loading && !detail && !rows.length"
        class="blank-state"
        role="status"
      >
        正在读取用户…
      </p>
      <section v-if="detail" class="workspace-panel">
        <h2>
          {{ detail.display_name }}
          <span class="status-badge">{{
            detail.status === 1 ? "启用" : "停用"
          }}</span>
        </h2>
        <p class="muted">
          {{ detail.username || "历史开发身份" }} ·
          {{ detail.platform_admin ? "平台管理员" : "普通账号" }}
        </p>
        <p class="code-text">
          {{ detail.user_code }} · {{ date(detail.created_at) }} 创建
        </p>
        <p class="muted">
          平台账号停用会阻止登录并撤销会话；租户成员和角色状态分别管理。
        </p>
        <p v-if="!detail.username" class="muted">
          历史开发身份没有内置登录用户名，此页只读；请创建独立账号后再加入租户。
        </p>
      </section>
      <section
        v-if="creating || detail?.username"
        class="workspace-panel compact-panel"
      >
        <h2>{{ creating ? "设置新账号" : "编辑账号资料" }}</h2>
        <form class="stack-form" @submit.prevent="save">
          <fieldset
            class="plain-fieldset form-fields"
            :disabled="busy || loading"
          >
            <label
              >用户名<input
                v-model="form.username"
                :readonly="!creating"
                required
                minlength="3"
                maxlength="32"
                pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,31}"
                autocomplete="off"
            /></label>
            <label
              >显示名称<input
                v-model="form.display_name"
                required
                maxlength="128"
            /></label>
            <label v-if="creating"
              >初始密码<input
                v-model="form.password"
                type="password"
                required
                minlength="12"
                maxlength="128"
                autocomplete="new-password"
            /></label>
            <label class="check-label"
              ><input
                v-model="form.platform_admin"
                type="checkbox"
              />平台管理员（管理所有租户与账号）</label
            >
            <label v-if="!creating"
              >账号状态<select v-model.number="form.status" :disabled="self">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <p v-if="self" class="muted">
              当前登录账号不能停用；最后一位平台管理员不能降权。
            </p>
            <button class="primary-button">
              {{ creating ? "创建账号" : "保存账号资料" }}
            </button>
          </fieldset>
        </form>
      </section>
      <section v-if="detail?.username" class="workspace-panel compact-panel">
        <h2>登录凭据</h2>
        <p class="muted">
          重置后该账号所有设备的登录都会失效。{{
            self ? "重置本人密码后需要重新登录。" : ""
          }}
        </p>
        <button
          v-if="!resetting"
          class="secondary-button"
          :disabled="busy"
          @click="resetting = true"
        >
          重置密码
        </button>
        <form v-else class="stack-form" @submit.prevent="resetPassword">
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
              确认重置密码</button
            ><button
              class="secondary-button"
              type="button"
              :disabled="busy"
              @click="
                resetting = false;
                newPassword = '';
              "
            >
              取消重置
            </button>
          </div>
        </form>
      </section>
      <section v-if="!code" class="workspace-panel list-panel">
        <div class="list-toolbar member-filters">
          <label class="search-field"
            ><span class="visually-hidden">搜索用户</span
            ><input
              v-model="search"
              aria-label="搜索用户"
              maxlength="200"
              placeholder="搜索账号、姓名或编码…"
          /></label>
          <label
            ><span class="visually-hidden">筛选账号状态</span
            ><select v-model="status" aria-label="筛选账号状态">
              <option value="">全部账号状态</option>
              <option value="1">启用账号</option>
              <option value="0">停用账号</option>
            </select></label
          >
          <label
            ><span class="visually-hidden">筛选平台身份</span
            ><select v-model="admin" aria-label="筛选平台身份">
              <option value="">全部平台身份</option>
              <option value="true">平台管理员</option>
              <option value="false">普通账号</option>
            </select></label
          >
        </div>
        <p v-if="!loading && !rows.length" class="blank-state">
          {{
            search || status || admin
              ? "没有匹配的用户，请调整筛选。"
              : "暂无账号，可创建独立账号。"
          }}
        </p>
        <article v-for="row in rows" :key="row.user_code" class="admin-row">
          <div>
            <h3>
              {{ row.display_name }}
              <span class="status-badge">{{
                row.status === 1 ? "启用" : "停用"
              }}</span>
            </h3>
            <p class="muted">
              {{ row.username || "历史开发身份" }} ·
              {{ row.platform_admin ? "平台管理员" : "普通账号" }}
            </p>
            <p class="muted">{{ date(row.created_at) }} 创建</p>
          </div>
          <button
            class="secondary-button"
            @click="go('users/' + row.user_code)"
          >
            用户详情
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
