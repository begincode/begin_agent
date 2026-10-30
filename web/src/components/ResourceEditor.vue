<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, post } from "../api";
import {
  useWorkspace,
  mutate,
  keyOf,
  endpoint,
  date,
  type Row,
} from "../workspace";
const props = defineProps<{ kind: string; code: string }>();
const { data, busy, readonly, can, go, act, load } = useWorkspace();
const form = ref<Row>({
  display_name: "",
  provider_code: "deepseek",
  model_code: "deepseek-flash",
  base_url: "https://api.deepseek.com",
  api_key: "",
  enabled: true,
  tool_name: "",
  group_name: "通用",
  implementation: "text_stats",
  mcp_name: "",
  endpoint: "",
  token: "",
  clear_token: false,
  status: 1,
  skill_name: "",
  description: "",
  instructions: "",
});
const current = ref<Row | null>(null);
const names: Record<string, string> = {
  models: "模型",
  tools: "工具",
  mcps: "MCP 服务",
  skills: "Skill",
};
const editable = computed(() => can("resources.manage") && !readonly.value);
const defaults: Record<string, string[]> = {
  deepseek: ["https://api.deepseek.com", "deepseek-flash"],
  glm: ["https://open.bigmodel.cn/api/paas/v4", "glm-5.3-flash"],
  minimax: ["https://api.minimax.io/v1", "MiniMax-M3.1-Flash"],
  kimi: ["https://api.moonshot.cn/v1", "kimi-k3"],
};
function provider() {
  const p = defaults[form.value.provider_code];
  form.value.base_url = p[0];
  form.value.model_code = p[1];
}
async function read() {
  if (props.code === "new") return;
  current.value = await api<Row>(endpoint(props.kind) + "/" + props.code);
  if (current.value) {
    Object.assign(form.value, current.value);
    if (props.kind === "tools")
      form.value.implementation =
        current.value.connection_config.implementation;
  } else throw new Error("资源不存在或不属于当前租户");
}
async function save() {
  await act(async () => {
    const f = form.value;
    let body: Row = {};
    if (props.kind === "models") {
      body = {
        display_name: f.display_name,
        model_code: f.model_code,
        base_url: f.base_url,
        enabled: f.enabled,
      };
      if (f.api_key) body.api_key = f.api_key;
      if (props.code === "new") body.provider_code = f.provider_code;
    } else if (props.kind === "tools")
      body = {
        tool_name: f.tool_name,
        group_name: f.group_name,
        implementation: f.implementation,
        status: f.status,
      };
    else if (props.kind === "mcps") {
      body = {
        mcp_name: f.mcp_name,
        endpoint: f.endpoint,
        status: f.status,
        clear_token: f.clear_token,
      };
      if (f.token) body.token = f.token;
    } else
      body = {
        skill_name: f.skill_name,
        description: f.description,
        instructions: f.instructions,
        status: f.status,
      };
    const r = await mutate(
      props.kind,
      props.code === "new" ? "" : props.code,
      body,
    );
    f.api_key = "";
    f.token = "";
    await load();
    if (props.code === "new") go(props.kind + "/" + r[keyOf(props.kind)]);
    else await read();
  }, "配置已保存；保存不代表外部接口已验证");
}
onMounted(() => void act(async () => read(), "", true));
</script>
<template>
  <main class="platform-main">
    <div class="page-heading">
      <div>
        <button class="text-button" @click="go(kind)">
          ← {{ names[kind] }}
        </button>
        <h1>{{ code === "new" ? "新建" : "配置" }}{{ names[kind] }}</h1>
        <p class="muted">
          {{
            kind === "models"
              ? "凭据加密保存，不在读取接口中返回。"
              : kind === "mcps"
                ? "通过 Streamable HTTP 连接，保存后检测并发现工具。"
                : kind === "skills"
                  ? "指令在 Agent 发布时固定到版本，并在执行时应用。"
                  : "工具必须绑定到 Agent 的发布版本后才能调用。"
          }}
        </p>
      </div>
      <button
        v-if="kind === 'models' && !readonly"
        class="secondary-button"
        @click="go('model-chat')"
      >
        打开模型调试
      </button>
    </div>
    <section class="workspace-panel compact-panel">
      <form class="stack-form" @submit.prevent="save">
        <fieldset
          :disabled="busy || !editable"
          class="plain-fieldset form-fields"
        >
          <template v-if="kind === 'models'"
            ><label
              >配置名称<input
                v-model="form.display_name"
                required
                maxlength="128" /></label
            ><label
              >模型供应商<select
                v-model="form.provider_code"
                :disabled="code !== 'new'"
                @change="provider"
              >
                <option value="deepseek">DeepSeek</option>
                <option value="glm">GLM / 智谱</option>
                <option value="minimax">MiniMax</option>
                <option value="kimi">Kimi</option>
              </select></label
            ><label
              >模型名称<input
                v-model="form.model_code"
                required
                maxlength="128" /></label
            ><label
              >API 地址<input
                v-model="form.base_url"
                type="url"
                required
                maxlength="256" /></label
            ><label
              >API Key<input
                v-model="form.api_key"
                type="password"
                :required="code === 'new'"
                maxlength="4096"
                autocomplete="new-password"
                :placeholder="
                  code === 'new' ? '输入模型密钥' : '留空保留现有密钥'
                " /></label
            ><label class="check-label"
              ><input v-model="form.enabled" type="checkbox" />启用配置</label
            ></template
          >
          <template v-else-if="kind === 'tools'"
            ><label
              >工具名称<input
                v-model="form.tool_name"
                required
                maxlength="128" /></label
            ><label
              >工具分组<input
                v-model="form.group_name"
                required
                maxlength="64" /></label
            ><label
              >实现<select v-model="form.implementation">
                <option value="text_stats">文本统计 · text_stats</option>
                <option value="utc_now">UTC 时间 · utc_now</option>
              </select></label
            >
            <label
              >状态<select v-model.number="form.status">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <p class="inline-hint">
              工具参数由内置实现的 Schema 定义，模型仅可使用版本中授权的工具。
            </p></template
          >
          <template v-else-if="kind === 'mcps'"
            ><label
              >服务名称<input
                v-model="form.mcp_name"
                required
                maxlength="128" /></label
            ><label
              >Streamable HTTP 地址<input
                v-model="form.endpoint"
                type="url"
                required
                maxlength="512"
                placeholder="https://example.com/mcp" /></label
            ><label
              >Bearer Token<input
                v-model="form.token"
                type="password"
                maxlength="4096"
                autocomplete="new-password"
                :placeholder="
                  current?.has_token
                    ? '留空保留已保存凭据'
                    : '可选，按服务要求填写'
                " /></label
            ><label class="check-label"
              ><input v-model="form.clear_token" type="checkbox" />清除已保存
              Token</label
            ><label
              >状态<select v-model.number="form.status">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
            <p class="inline-hint">
              主机需先由服务端管理员配置到允许列表。更改地址会清除旧凭据和工具目录。
            </p></template
          >
          <template v-else
            ><label
              >Skill 名称<input
                v-model="form.skill_name"
                required
                maxlength="128" /></label
            ><label
              >简介<input v-model="form.description" maxlength="512" /></label
            ><label
              >技能指令<textarea
                v-model="form.instructions"
                rows="12"
                required
                maxlength="20000"
                placeholder="描述适用情景、执行步骤和输出要求…"
              /></label
            ><label
              >状态<select v-model.number="form.status">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            ></template
          >
        </fieldset>
        <div v-if="editable" class="row-actions">
          <button class="primary-button" :disabled="busy">
            {{ busy ? "正在保存…" : "保存配置" }}</button
          ><button type="button" class="secondary-button" @click="go(kind)">
            返回列表
          </button>
        </div>
      </form>
      <template v-if="kind === 'mcps' && current"
        ><div class="section-heading">
          <h2>工具发现</h2>
          <button
            v-if="editable"
            class="secondary-button"
            :disabled="busy || !current.status"
            @click="
              act(async () => {
                await post(`/api/v1/mcps/${code}/check`, {});
                await load();
                await read();
              }, '连接检测通过，工具目录已更新')
            "
          >
            检测连接
          </button>
        </div>
        <p class="muted">
          {{
            current.checked_at
              ? "最近检测：" + date(current.checked_at)
              : "尚未检测连接。保存配置不代表服务可用。"
          }}
        </p>
        <details
          v-for="t in current.tools"
          :key="t.name"
          class="version-detail"
        >
          <summary>{{ t.name }}</summary>
          <p>{{ t.description }}</p>
          <pre>{{ JSON.stringify(t.input_schema, null, 2) }}</pre>
        </details></template
      >
    </section>
  </main>
</template>
