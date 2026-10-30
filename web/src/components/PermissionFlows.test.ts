import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import AgentEditor from "./AgentEditor.vue";
import TaskWorkspace from "./TaskWorkspace.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  for (const w of wrappers.splice(0)) w.unmount();
  vi.unstubAllGlobals();
});
function scope(permissions: string[]) {
  const errors: string[] = [];
  const state: Workspace = {
    identity: ref({ user_code: "usr_owner" }),
    data: ref({
      models: [
        { profile_code: "mdl_one", display_name: "模型", enabled: true },
      ],
      tools: [],
      skills: [],
      mcps: [],
      agents: [],
    }),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => permissions.includes(p),
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      try {
        await fn();
      } catch (e) {
        errors.push(String(e));
      }
    },
  };
  return { state, errors };
}
const config = {
  profile_code: "mdl_one",
  system_prompt: "已保存的草稿",
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
};
it("lets a publisher publish the saved draft without requiring configuration-write permission", async () => {
  const { state, errors } = scope([
    "agents.read",
    "agents.publish",
    "resources.read",
  ]);
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "PATCH" || options.method === "PUT")
      return new Response(JSON.stringify({ detail: "没有此操作权限" }), {
        status: 403,
      });
    let value: unknown = [];
    if (path.endsWith("/agt_one"))
      value = {
        agent_code: "agt_one",
        agent_name: "发布验收",
        status: 1,
        agent_type: "NORMAL",
        current_version_code: "agv_old",
      };
    if (path.endsWith("/draft")) value = { revision: 2, config };
    return new Response(JSON.stringify(value), {
      status: options.method === "POST" ? 201 : 200,
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(AgentEditor, {
    props: { code: "agt_one", tab: "config" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  // Native controls inherit disabled state from the fieldset.
  expect(
    (wrapper.get("fieldset.plain-fieldset").element as HTMLFieldSetElement)
      .disabled,
  ).toBe(true);
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "发布新版本")!
    .trigger("click");
  await flushPromises();
  const calls = fetchMock.mock.calls as [string, RequestInit?][];
  expect(
    calls.some(
      ([path, options]) =>
        path.endsWith("/versions") && options?.method === "POST",
    ),
  ).toBe(true);
  expect(
    calls.some(([, options]) =>
      ["PATCH", "PUT"].includes(options?.method ?? ""),
    ),
  ).toBe(false);
  expect(errors).toEqual([]);
});
it.each(["PENDING", "CANCELLED"])(
  "does not offer task controls to an owner whose role is read-only (%s)",
  async (status) => {
    const { state } = scope(["tasks.read"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              task_code: "tsk_one",
              title: "只读任务",
              created_by: "usr_owner",
              status,
              turns: [],
              runs: [],
              artifacts: [],
            }),
            { status: 200 },
          ),
      ),
    );
    const wrapper = mount(TaskWorkspace, {
      props: { code: "tsk_one" },
      global: { provide: { [workspaceKey as symbol]: state } },
    });
    wrappers.push(wrapper);
    await flushPromises();
    expect(
      wrapper
        .findAll("button")
        .some((b) => ["取消执行", "重试"].includes(b.text())),
    ).toBe(false);
    expect(wrapper.get("textarea").attributes("placeholder")).toBe(
      "当前账号没有继续对话权限",
    );
  },
);
it("passes the selected historical version when running an Agent test case", async () => {
  const { state } = scope([
    "agents.read",
    "tasks.read",
    "tasks.create",
    "resources.read",
  ]);
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    let value: unknown = [];
    if (path.endsWith("/agt_one"))
      value = {
        agent_code: "agt_one",
        agent_name: "助手",
        current_version_code: "v2",
        status: 1,
      };
    if (path.endsWith("/draft")) value = { revision: 1, config };
    if (path.endsWith("/versions"))
      value = [
        { version_code: "v2", version_no: 2, status: "PUBLISHED" },
        { version_code: "v1", version_no: 1, status: "PUBLISHED" },
      ];
    if (path.endsWith("/test-cases"))
      value = [
        {
          case_code: "case_one",
          case_name: "回归",
          prompt: "任务",
          expected_contains: "ok",
          status: 1,
        },
      ];
    if (path.endsWith("/run")) value = { task_code: "tsk_test" };
    return new Response(JSON.stringify(value), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(AgentEditor, {
    props: { code: "agt_one", tab: "tests" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  await wrapper.get("select").setValue("v1");
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "运行用例")!
    .trigger("click");
  await flushPromises();
  const call = (fetchMock.mock.calls as [string, RequestInit?][]).find(
    ([path]) => path.endsWith("/test-cases/case_one/run"),
  );
  expect(JSON.parse(call![1]!.body as string)).toEqual({ version_code: "v1" });
});
it("keeps revoked bindings removable and excludes revoked tools from group selection", async () => {
  const { state } = scope(["agents.read", "agents.manage", "resources.read"]);
  state.data.value.tools = ["old", "new", "allowed"].map((name) => ({
    tool_code: "tol_" + name,
    tool_name: name,
    status: 1,
    group_name: "测试组",
  }));
  state.data.value.resources = [
    {
      resource_kind: "MODEL",
      resource_code: "mdl_one",
      availability: "AVAILABLE",
      available: true,
    },
    ...["old", "new", "allowed"].map((name) => ({
      resource_kind: "TOOL",
      resource_code: "tol_" + name,
      availability: name === "allowed" ? "AVAILABLE" : "REVOKED",
      available: name === "allowed",
    })),
  ];
  const fetchMock = vi.fn(async (path: string) => {
    const value = path.endsWith("/agt_one")
      ? { agent_code: "agt_one", agent_name: "助手", status: 1 }
      : path.endsWith("/draft")
        ? { revision: 1, config: { ...config, tool_codes: ["tol_old"] } }
        : [];
    return new Response(JSON.stringify(value));
  });
  vi.stubGlobal("fetch", fetchMock);
  const w = mount(AgentEditor, {
    props: { code: "agt_one", tab: "config" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  await flushPromises();
  const old = w.get('input[value="tol_old"]'),
    fresh = w.get('input[value="tol_new"]');
  expect(old.attributes("disabled")).toBeUndefined();
  expect(fresh.attributes("disabled")).toBeDefined();
  const group = w
    .findAll("label")
    .find((l) => l.text().includes("测试组 · 全选"))!
    .get("input");
  await group.setValue(true);
  expect(
    (w.get('input[value="tol_allowed"]').element as HTMLInputElement).checked,
  ).toBe(true);
  expect((fresh.element as HTMLInputElement).checked).toBe(false);
  await old.setValue(false);
  expect(old.attributes("disabled")).toBeDefined();
  expect(w.text()).toContain("已撤销授权");
});
