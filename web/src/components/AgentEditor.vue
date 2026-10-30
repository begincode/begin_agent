<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api, post } from "../api";
import VersionComparison from "./VersionComparison.vue";
import CollaborationPanel from "./CollaborationPanel.vue";
import { useWorkspace, label, date, type Row } from "../workspace";
const props = defineProps<{ code: string; tab: string }>();
const { data, busy, readonly, can, go, act, load } = useWorkspace();
const agent = ref<Row>({ agent_name: "", agent_type: "NORMAL", status: 1 }),
  versions = ref<Row[]>([]),
  cases = ref<Row[]>([]),
  step = ref(1),
  revision = ref(0);
const config = ref<Row>({
  profile_code: "",
  system_prompt: "你是一位认真、可靠的助手，根据任务要求提供可核对的结果。",
  tool_codes: [],
  skill_codes: [],
  mcp_codes: [],
  mcp_tool_names: {},
  execution_config: {
    max_steps: 10,
    timeout_seconds: 300,
    max_sub_agent_calls: 5,
    max_depth: 2,
    max_tokens: 30000,
  },
  model_parameters: { thinking: "auto", max_tokens: 4096 },
});
const test = ref({
    case_name: "",
    prompt: "",
    expected_contains: "",
    status: 1,
  }),
  debugPrompt = ref(""),
  debugDraft = ref(false),
  selectedVersion = ref(""),
  editingCase = ref("");
const editable = computed(() => can("agents.manage") && !readonly.value);
const resourceStates = computed(
  () =>
    new Map(
      (data.value.resources ?? []).map((r) => [
        r.resource_kind + ":" + r.resource_code,
        r,
      ]),
    ),
);
function resourceReady(kind: string, code: string) {
  return (
    !data.value.resources ||
    resourceStates.value.get(kind + ":" + code)?.available === true
  );
}
function resourceNote(kind: string, code: string) {
  if (!data.value.resources) return "";
  const state = resourceStates.value.get(kind + ":" + code)?.availability;
  const text =
    (
      {
        REVOKED: "已撤销授权",
        INACTIVE: "已停用",
        UNCHECKED: "需检测且至少发现一个工具",
      } as Record<string, string>
    )[state] || (state ? "" : "目录中不可用");
  return text ? "（" + text + "）" : "";
}
function groupCodes(group: string) {
  return (data.value.tools ?? [])
    .filter(
      (t) =>
        (t.group_name || "通用") === group &&
        t.status === 1 &&
        resourceReady("TOOL", t.tool_code),
    )
    .map((t) => t.tool_code);
}
const groups = computed(() => [
  ...new Set((data.value.tools ?? []).map((t) => t.group_name || "通用")),
]);
function allGroup(group: string) {
  const codes = groupCodes(group);
  return (
    codes.length > 0 && codes.every((c) => config.value.tool_codes.includes(c))
  );
}
function toggleGroup(group: string) {
  const codes = groupCodes(group);
  config.value.tool_codes = allGroup(group)
    ? config.value.tool_codes.filter((c: string) => !codes.includes(c))
    : [...new Set([...config.value.tool_codes, ...codes])];
}
async function refresh() {
  if (props.code === "new") {
    config.value.profile_code =
      data.value.models?.find(
        (p) => p.enabled && resourceReady("MODEL", p.profile_code),
      )?.profile_code ?? "";
    return;
  }
  const [a, d, v, c] = await Promise.all([
    api<Row>(`/api/v1/agents/${props.code}`),
    api<Row>(`/api/v1/agents/${props.code}/draft`),
    api<Row[]>(`/api/v1/agents/${props.code}/versions`),
    api<Row[]>(`/api/v1/agents/${props.code}/test-cases`),
  ]);
  agent.value = a;
  versions.value = v;
  cases.value = c;
  revision.value = d.revision;
  if (d.config) config.value = { ...config.value, ...d.config };
  else {
    const published = v.find((v) => v.version_code === a.current_version_code);
    if (published) fromVersion(published);
  }
  selectedVersion.value = a.current_version_code ?? "";
}
function fromVersion(v: Row) {
  const snap = v.dependency_snapshot;
  config.value = {
    ...config.value,
    profile_code: v.profile_code,
    system_prompt: v.system_prompt,
    execution_config: v.execution_config,
    model_parameters: v.model_parameters,
    tool_codes: (snap.tools ?? []).map((t: Row) => t.tool_code),
    skill_codes: (snap.skills ?? []).map((t: Row) => t.skill_code),
    mcp_codes: (snap.mcps ?? []).map((t: Row) => t.mcp_code),
    mcp_tool_names: Object.fromEntries(
      (snap.mcps ?? []).map((t: Row) => [
        t.mcp_code,
        (t.tools ?? []).map((x: Row) => x.name),
      ]),
    ),
  };
}
async function save(publish = false) {
  if (
    (!editable.value && !publish) ||
    (props.code === "new" && !editable.value)
  )
    return;
  if (publish && (!can("agents.publish") || readonly.value)) return;
  await act(
    async () => {
      let code = props.code;
      if (code === "new") {
        const a = await post<Row>("/api/v1/agents", agent.value);
        code = a.agent_code;
        agent.value = a;
      } else if (editable.value)
        await post(
          `/api/v1/agents/${code}`,
          {
            agent_name: agent.value.agent_name,
            agent_type: agent.value.agent_type,
            status: agent.value.status,
          },
          "PATCH",
        );
      if (editable.value) {
        const draft = await post<Row>(
          `/api/v1/agents/${code}/draft`,
          { ...config.value, revision: revision.value },
          "PUT",
        );
        revision.value = draft.revision;
      }
      if (publish) await post(`/api/v1/agents/${code}/versions`, config.value);
      await load();
      if (props.code === "new") go("agents/" + code + "/config");
      else await refresh();
    },
    publish
      ? "版本已发布，后续新任务使用此版本"
      : "草稿已保存，已发布版本保持原配置",
  );
}
async function runDebug() {
  await act(async () => {
    const task = await post<Row>(`/api/v1/agents/${props.code}/debug`, {
      prompt: debugPrompt.value,
      version_code: selectedVersion.value || null,
      use_draft: debugDraft.value,
    });
    go("tasks/" + task.task_code);
  });
}
onMounted(() => void act(refresh, "", true));
</script>
<template>
  <main class="platform-main">
    <div class="page-heading">
      <div>
        <button class="text-button" @click="go('agents')">← Agent</button>
        <h1>{{ code === "new" ? "创建 Agent" : agent.agent_name }}</h1>
        <p class="muted">
          {{
            code === "new"
              ? "三步配置一个可以执行任务的协作成员。"
              : "草稿与发布版本分开保存，历史执行始终可以追溯。"
          }}
        </p>
      </div>
      <div v-if="code !== 'new'" class="row-actions">
        <span class="status-badge">{{
          agent.current_version_code ? "已发布" : "待发布"
        }}</span
        ><button
          v-if="editable && tab === 'config'"
          class="secondary-button"
          :disabled="busy"
          @click="save()"
        >
          保存草稿</button
        ><button
          v-if="can('agents.publish') && !readonly && tab === 'config'"
          class="primary-button"
          :disabled="
            busy || !config.profile_code || !config.system_prompt.trim()
          "
          @click="save(true)"
        >
          发布新版本
        </button>
      </div>
    </div>
    <nav v-if="code !== 'new'" class="detail-tabs agent-tabs">
      <a
        v-for="[key, text] in [
          ['config', '基本配置'],
          ['collaboration', '协作配置'],
          ['debug', '对话调试'],
          ['versions', '版本管理'],
          ['tests', '测试用例'],
        ]"
        :key="key"
        :href="'#/agents/' + code + '/' + key"
        :class="{ active: tab === key }"
        >{{ text }}</a
      >
    </nav>
    <nav v-else class="wizard-steps">
      <span
        v-for="[no, text] in [
          [1, '基本信息'],
          [2, '模型与指令'],
          [3, '能力资源'],
        ]"
        :key="no"
        :class="{ active: step === no }"
        ><b>{{ no }}</b
        >{{ text }}</span
      >
    </nav>
    <form
      v-if="tab === 'config' || code === 'new'"
      class="agent-config"
      @submit.prevent="code === 'new' && step < 3 ? step++ : save()"
    >
      <fieldset :disabled="busy || !editable" class="plain-fieldset">
        <section v-if="code !== 'new' || step === 1" class="workspace-panel">
          <h2>基本信息</h2>
          <div class="stack-form">
            <label
              >Agent 名称<input
                v-model="agent.agent_name"
                maxlength="128"
                required
                placeholder="例如：产品研究助手" /></label
            ><label
              >Agent 类型<select v-model="agent.agent_type">
                <option value="NORMAL">通用 Agent</option>
                <option value="SUPERVISOR">主 Agent · 调度协作</option>
                <option value="ROUTER">路由 Agent · 选择执行者</option>
              </select></label
            ><label v-if="code !== 'new'"
              >状态<select v-model.number="agent.status">
                <option :value="1">启用</option>
                <option :value="0">停用</option>
              </select></label
            >
          </div>
        </section>
        <section v-if="code !== 'new' || step === 2" class="workspace-panel">
          <h2>模型与指令</h2>
          <div class="stack-form">
            <label
              >模型配置<select
                v-model="config.profile_code"
                :required="code === 'new'"
              >
                <option value="">选择模型</option>
                <option
                  v-for="m in data.models"
                  :key="m.profile_code"
                  :value="m.profile_code"
                  :disabled="
                    !m.enabled || !resourceReady('MODEL', m.profile_code)
                  "
                >
                  {{ m.display_name }} · {{ m.model_code
                  }}{{
                    !m.enabled
                      ? "（已停用）"
                      : resourceNote("MODEL", m.profile_code)
                  }}
                </option>
              </select></label
            >
            <p
              v-if="
                !data.models?.some(
                  (m) => m.enabled && resourceReady('MODEL', m.profile_code),
                )
              "
              class="inline-hint"
            >
              暂无可用模型。<a href="#/models/new">先配置模型 →</a>
            </p>
            <label
              >系统提示词<textarea
                v-model="config.system_prompt"
                rows="8"
                maxlength="30000"
                required
                placeholder="定义职责、执行步骤与输出要求…"
              />
            </label>
            <div class="form-columns">
              <label
                >最大输出 Token<input
                  v-model.number="config.model_parameters.max_tokens"
                  type="number"
                  min="1"
                  max="65536"
                  required /></label
              ><label
                >思考模式<select v-model="config.model_parameters.thinking">
                  <option value="auto">自动</option>
                  <option value="enabled">启用</option>
                  <option value="disabled">关闭</option>
                </select></label
              >
            </div>
          </div>
        </section>
        <section v-if="code !== 'new' || step === 3" class="workspace-panel">
          <h2>能力资源</h2>
          <p class="muted">
            资源在发布时固定到版本；租户撤销授权后执行将停止使用。
          </p>
          <div class="stack-form">
            <h3>工具</h3>
            <div v-for="group in groups" :key="group" class="selection-group">
              <label class="check-label"
                ><input
                  type="checkbox"
                  :checked="allGroup(group)"
                  :disabled="!groupCodes(group).length"
                  @change="toggleGroup(group)"
                /><strong>{{ group }} · 全选</strong></label
              ><label
                v-for="t in data.tools?.filter(
                  (t) => (t.group_name || '通用') === group,
                )"
                :key="t.tool_code"
                class="check-label"
                ><input
                  v-model="config.tool_codes"
                  type="checkbox"
                  :value="t.tool_code"
                  :disabled="
                    (t.status !== 1 || !resourceReady('TOOL', t.tool_code)) &&
                    !config.tool_codes.includes(t.tool_code)
                  "
                />{{ t.tool_name }}
                <span v-if="t.status !== 1" class="muted">（已停用）</span
                ><span v-else class="muted">{{
                  resourceNote("TOOL", t.tool_code)
                }}</span></label
              >
            </div>
            <p v-if="!groups.length" class="muted">
              暂无工具，<a href="#/tools/new">创建工具</a>。
            </p>
            <h3>Skill</h3>
            <label
              v-for="s in data.skills"
              :key="s.skill_code"
              class="check-label"
              ><input
                v-model="config.skill_codes"
                type="checkbox"
                :value="s.skill_code"
                :disabled="
                  (s.status !== 1 || !resourceReady('SKILL', s.skill_code)) &&
                  !config.skill_codes.includes(s.skill_code)
                "
              />{{ s.skill_name
              }}<span v-if="s.status !== 1" class="muted">（已停用）</span>
              <span v-else class="muted">{{
                resourceNote("SKILL", s.skill_code)
              }}</span>
              <span class="muted">{{ s.description }}</span></label
            >
            <p v-if="!data.skills?.length" class="muted">
              暂无 Skill，<a href="#/skills/new">添加可复用指令</a>。
            </p>
            <h3>MCP 服务</h3>
            <div
              v-for="m in data.mcps"
              :key="m.mcp_code"
              class="selection-group"
            >
              <label class="check-label"
                ><input
                  v-model="config.mcp_codes"
                  type="checkbox"
                  :value="m.mcp_code"
                  :disabled="
                    (m.status !== 1 ||
                      !m.checked_at ||
                      !resourceReady('MCP', m.mcp_code)) &&
                    !config.mcp_codes.includes(m.mcp_code)
                  "
                />{{ m.mcp_name }}<span v-if="m.status !== 1">（已停用）</span
                ><span v-else>{{ resourceNote("MCP", m.mcp_code) }}</span> ·
                {{
                  m.checked_at ? m.tools.length + " 个工具" : "需先检测连接"
                }}</label
              >
              <div v-if="config.mcp_codes.includes(m.mcp_code)">
                <p class="muted">
                  首次选择服务时默认使用已发现工具；指定工具后需至少保留一个，或取消该服务。
                </p>
                <label v-for="t in m.tools" :key="t.name" class="check-label"
                  ><input
                    type="checkbox"
                    :checked="
                      config.mcp_tool_names[m.mcp_code]?.includes(t.name)
                    "
                    @change="
                      config.mcp_tool_names[m.mcp_code] = (
                        $event.target as HTMLInputElement
                      ).checked
                        ? [...(config.mcp_tool_names[m.mcp_code] || []), t.name]
                        : config.mcp_tool_names[m.mcp_code].filter(
                            (n: string) => n !== t.name,
                          )
                    "
                  />{{ t.name }}</label
                >
              </div>
            </div>
            <p v-if="!data.mcps?.length" class="muted">
              暂无 MCP 服务，<a href="#/mcps/new">添加连接</a>。
            </p>
          </div>
        </section>
        <section v-if="code !== 'new'" class="workspace-panel">
          <h2>执行边界</h2>
          <div class="stack-form form-columns">
            <label
              v-for="[key, text, min, max] in [
                ['max_steps', '模型调用上限', 1, 40],
                ['timeout_seconds', '超时（秒）', 1, 1800],
                ['max_sub_agent_calls', '最大子调用', 0, 20],
                ['max_depth', '最大深度', 0, 5],
                ['max_tokens', 'Token 总预算', 1, 500000],
              ]"
              :key="key"
              ><span>{{ text }}</span
              ><input
                v-model.number="config.execution_config[key]"
                type="number"
                :min="min"
                :max="max"
                required
            /></label>
          </div>
        </section>
      </fieldset>
      <div v-if="editable" class="form-footer">
        <button
          v-if="code === 'new' && step > 1"
          type="button"
          class="secondary-button"
          @click="step--"
        >
          上一步</button
        ><button
          class="primary-button"
          :disabled="busy || !agent.agent_name.trim()"
        >
          {{ code === "new" && step < 3 ? "下一步 →" : "保存草稿" }}</button
        ><span class="muted">{{
          code === "new" ? "创建后可调试并发布版本" : "草稿修订 " + revision
        }}</span>
      </div>
    </form>
    <CollaborationPanel v-else-if="tab === 'collaboration'" :code="code" />
    <section v-else-if="tab === 'debug'" class="workspace-panel compact-panel">
      <h2>执行调试</h2>
      <p class="muted">通过同一任务队列运行，保留完整输出、步骤与模型消息。</p>
      <form class="stack-form" @submit.prevent="runDebug">
        <label
          >版本<select v-model="selectedVersion" :disabled="busy || debugDraft">
            <option value="">选择版本</option>
            <option
              v-for="v in versions.filter((v) => v.status === 'PUBLISHED')"
              :key="v.version_code"
              :value="v.version_code"
            >
              v{{ v.version_no }} · {{ date(v.created_at) }}
            </option>
          </select></label
        ><label v-if="editable" class="check-label"
          ><input
            v-model="debugDraft"
            type="checkbox"
            :disabled="busy"
          />使用已保存的草稿（不发布）</label
        ><label
          >调试要求<textarea
            v-model="debugPrompt"
            rows="7"
            required
            maxlength="30000"
            :disabled="busy || readonly"
          /></label
        ><button
          class="primary-button"
          :disabled="
            busy ||
            readonly ||
            !can('tasks.create') ||
            !debugPrompt.trim() ||
            (!debugDraft && !selectedVersion)
          "
        >
          开始调试 →
        </button>
      </form>
    </section>
    <section v-else-if="tab === 'versions'" class="workspace-panel">
      <VersionComparison
        :versions="versions"
        :current="agent.current_version_code"
      />
      <h2>版本历史</h2>
      <p class="muted">
        回滚只切换新任务的默认版本，已有任务继续使用提交时的快照。
      </p>
      <div v-if="!versions.length" class="blank-state">
        尚无版本，请完善配置后发布。
      </div>
      <article v-for="v in versions" :key="v.version_code" class="version-card">
        <div class="section-heading">
          <h3>
            v{{ v.version_no }}
            <span class="status-badge">{{
              agent.current_version_code === v.version_code
                ? "当前版本"
                : label(v.status)
            }}</span>
          </h3>
          <span class="muted">{{ date(v.created_at) }}</span>
        </div>
        <p class="code-text">{{ v.version_code }}</p>
        <div class="row-actions">
          <button
            v-if="editable"
            class="secondary-button"
            :disabled="busy"
            @click="
              act(async () => {
                fromVersion(v);
                const draft = await post<Row>(
                  `/api/v1/agents/${code}/draft`,
                  { ...config, revision },
                  'PUT',
                );
                revision = draft.revision;
                go('agents/' + code + '/config');
              }, '已复制到草稿')
            "
          >
            复制到草稿</button
          ><button
            v-if="
              can('agents.publish') &&
              !readonly &&
              v.status === 'PUBLISHED' &&
              agent.current_version_code !== v.version_code
            "
            class="secondary-button"
            :disabled="busy"
            @click="
              act(async () => {
                await post(`/api/v1/agents/${code}/rollback`, {
                  version_code: v.version_code,
                });
                await refresh();
                await load();
              }, '已切换当前版本')
            "
          >
            回滚到此版本
          </button>
        </div>
        <details class="version-detail">
          <summary>查看配置快照</summary>
          <pre>{{ JSON.stringify(v, null, 2) }}</pre>
        </details>
      </article>
    </section>
    <section v-else-if="tab === 'tests'" class="workspace-panel">
      <h2>固定测试用例</h2>
      <label class="case-version"
        >执行版本<select v-model="selectedVersion" :disabled="busy || readonly">
          <option value="">当前发布版本</option>
          <option
            v-for="v in versions.filter((v) => v.status === 'PUBLISHED')"
            :key="v.version_code"
            :value="v.version_code"
          >
            v{{ v.version_no }}
          </option>
        </select></label
      >
      <p class="muted">按“输出包含预期文本”验证结果；每次执行生成独立任务。</p>
      <form
        v-if="editable"
        class="stack-form compact-form"
        @submit.prevent="
          act(async () => {
            await post(
              `/api/v1/agents/${code}/test-cases` +
                (editingCase ? '/' + editingCase : ''),
              test,
              editingCase ? 'PATCH' : 'POST',
            );
            editingCase = '';
            test = {
              case_name: '',
              prompt: '',
              expected_contains: '',
              status: 1,
            };
            await refresh();
          }, '测试用例已保存')
        "
      >
        <label
          >用例名称<input
            v-model="test.case_name"
            required
            maxlength="128"
            :disabled="busy" /></label
        ><label
          >输入要求<textarea
            v-model="test.prompt"
            required
            rows="3"
            maxlength="30000"
            :disabled="busy"
          /></label
        ><label
          >预期包含<input
            v-model="test.expected_contains"
            required
            maxlength="2000"
            :disabled="busy" /></label
        ><button
          class="primary-button"
          :disabled="
            busy || !test.case_name || !test.prompt || !test.expected_contains
          "
        >
          {{ editingCase ? "保存用例" : "添加用例" }}
        </button>
      </form>
      <article v-for="c in cases" :key="c.case_code" class="version-card">
        <h3>
          {{ c.case_name }}
          <span class="status-badge">{{ c.status ? "启用" : "停用" }}</span>
        </h3>
        <p>{{ c.prompt }}</p>
        <p class="muted">预期包含：{{ c.expected_contains }}</p>
        <div v-if="c.latest_execution" class="case-result">
          <span
            class="status-badge"
            :data-status="
              c.latest_execution.test_result
                ? c.latest_execution.test_result.passed
                  ? 'SUCCEEDED'
                  : 'FAILED'
                : c.latest_execution.status
            "
            >{{
              c.latest_execution.test_result
                ? c.latest_execution.test_result.passed
                  ? "断言通过"
                  : "断言未通过"
                : label(c.latest_execution.status)
            }}</span
          ><span class="muted"
            >v{{
              versions.find(
                (v) => v.version_code === c.latest_execution.version_code,
              )?.version_no ?? "历史"
            }}
            · {{ date(c.latest_execution.created_at) }}</span
          ><button
            class="secondary-button"
            @click="go('tasks/' + c.latest_execution.task_code)"
          >
            查看最近执行
          </button>
        </div>
        <p v-else class="muted">
          {{ can("tasks.read") ? "尚未运行" : "没有测试任务读取权限" }}
        </p>
        <div class="row-actions">
          <button
            v-if="can('tasks.create') && !readonly"
            class="primary-button"
            :disabled="
              busy ||
              (!selectedVersion && !agent.current_version_code) ||
              !c.status
            "
            @click="
              act(async () => {
                const task = await post<Row>(
                  `/api/v1/agents/${code}/test-cases/${c.case_code}/run`,
                  selectedVersion ? { version_code: selectedVersion } : null,
                );
                go('tasks/' + task.task_code);
              })
            "
          >
            运行用例</button
          ><button
            v-if="editable"
            class="secondary-button"
            :disabled="busy"
            @click="
              editingCase = c.case_code;
              test = {
                case_name: c.case_name,
                prompt: c.prompt,
                expected_contains: c.expected_contains,
                status: c.status,
              };
            "
          >
            编辑</button
          ><button
            v-if="editable"
            class="secondary-button"
            :disabled="busy"
            @click="
              act(async () => {
                await post(
                  `/api/v1/agents/${code}/test-cases/${c.case_code}`,
                  {
                    case_name: c.case_name,
                    prompt: c.prompt,
                    expected_contains: c.expected_contains,
                    status: c.status ? 0 : 1,
                  },
                  'PATCH',
                );
                await refresh();
              })
            "
          >
            {{ c.status ? "停用" : "启用" }}
          </button>
        </div>
      </article>
      <p v-if="!cases.length" class="blank-state">暂无测试用例。</p>
    </section>
  </main>
</template>
