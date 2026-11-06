import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import AgentEditor from "./AgentEditor.vue";
import CollaborationPanel from "./CollaborationPanel.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  sessionStorage.clear();
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
});
const config = {
  profile_code: "mdl_one",
  system_prompt: "原提示词",
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
function setup(collaboration = false, creating = false) {
  let current = {
    agent: {
      agent_code: "agt_one",
      agent_name: "服务器名称",
      agent_type: "NORMAL",
      status: 1,
      current_version_code: "v1",
    },
    draft: { revision: 1, config: config as typeof config | null },
    versions: [
      { version_code: "v1", version_no: 1, status: "PUBLISHED", ...config },
    ],
    etag: '"old"',
    relation_etag: '"relations"',
    relations: [
      {
        relation_code: "rel_one",
        sub_agent_code: "agt_child",
        sub_agent_name: "子助手",
        call_mode: "SERIAL",
        timeout_seconds: 10,
        enabled: true,
      },
    ],
  };
  let reject = false,
    readError = false;
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (path === "/api/v1/agents" && options.method === "POST")
      return new Response(
        JSON.stringify({ ...current.agent, configuration_etag: current.etag }),
      );
    if (["PUT", "PATCH", "DELETE", "POST"].includes(options.method || "")) {
      if (reject)
        return new Response(
          JSON.stringify({ detail: "配置已被其他操作更新" }),
          { status: 412 },
        );
      return new Response(JSON.stringify({ draft_revision: 2 }));
    }
    if (readError) throw new Error("读取中断");
    if (path.endsWith("/relations"))
      return new Response(JSON.stringify(current.relations), {
        headers: { ETag: current.relation_etag },
      });
    return new Response(
      JSON.stringify(path.endsWith("/configuration") ? current : []),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const errors: string[] = [];
  const state: Workspace = {
    identity: ref({ user_code: "user", tenant_code: "tenant" }),
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
    can: () => true,
    go: vi.fn(),
    load: async () => {},
    act: async (fn) => {
      try {
        await fn();
      } catch (e) {
        errors.push(String(e));
      }
    },
  };
  const w = collaboration
    ? mount(CollaborationPanel, {
        props: { code: "agt_one" },
        global: { provide: { [workspaceKey as symbol]: state } },
      })
    : mount(AgentEditor, {
        props: { code: creating ? "new" : "agt_one", tab: "config" },
        global: { provide: { [workspaceKey as symbol]: state } },
      });
  wrappers.push(w);
  return {
    w,
    state,
    fetchMock,
    errors,
    reject: () => {
      reject = true;
    },
    readError: () => {
      readError = true;
    },
    latest: () => {
      current = {
        ...current,
        agent: { ...current.agent, agent_name: "最新名称" },
        etag: '"new"',
      };
    },
    emptyDraft: () => {
      current = {
        ...current,
        agent: { ...current.agent, current_version_code: "" },
        draft: { revision: 0, config: null },
        versions: [],
      };
    },
  };
}
function button(w: ReturnType<typeof mount>, name: string) {
  return w.findAll("button").find((b) => b.text() === name)!;
}
it("saves metadata and draft in a single conditional request", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  await w.get<HTMLInputElement>('input[maxlength="128"]').setValue("本地名称");
  await button(w, "保存草稿").trigger("click");
  await flushPromises();
  const writes = fetchMock.mock.calls.filter(
    ([, o]) => o?.method === "PUT" || o?.method === "PATCH",
  );
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe("/api/v1/agents/agt_one/configuration");
  expect(new Headers(writes[0][1]!.headers).get("If-Match")).toBe('"old"');
  expect(JSON.parse(writes[0][1]!.body as string)).toMatchObject({
    agent: { agent_name: "本地名称" },
    config: { revision: 1 },
    publish: false,
  });
});
it("retains local input on conflict and requires a discard confirmation before loading", async () => {
  const s = setup();
  await flushPromises();
  await s.w
    .get<HTMLInputElement>('input[maxlength="128"]')
    .setValue("不能丢失的本地名称");
  s.reject();
  await button(s.w, "保存草稿").trigger("click");
  await flushPromises();
  expect(
    s.w.get<HTMLInputElement>('input[maxlength="128"]').element.value,
  ).toBe("不能丢失的本地名称");
  expect(button(s.w, "保存草稿").attributes("disabled")).toBeDefined();
  expect(
    s.w
      .findAll("button")
      .filter((b) => b.text() === "保存草稿")
      .every((b) => b.attributes("disabled") !== undefined),
  ).toBe(true);
  const reads = s.fetchMock.mock.calls.length;
  await button(s.w, "重新加载配置").trigger("click");
  await flushPromises();
  expect(s.fetchMock.mock.calls).toHaveLength(reads);
  await button(s.w, "保留本地编辑").trigger("click");
  expect(
    s.w.get<HTMLInputElement>('input[maxlength="128"]').element.value,
  ).toBe("不能丢失的本地名称");
  s.latest();
  await button(s.w, "重新加载配置").trigger("click");
  await button(s.w, "放弃本地修改并加载").trigger("click");
  await flushPromises();
  expect(
    s.w.get<HTMLInputElement>('input[maxlength="128"]').element.value,
  ).toBe("最新名称");
  expect(s.w.find(".edit-conflict").exists()).toBe(false);
});
it("keeps local values when confirmed reloading fails", async () => {
  const s = setup();
  await flushPromises();
  await s.w
    .get<HTMLInputElement>('input[maxlength="128"]')
    .setValue("保留输入");
  s.reject();
  await button(s.w, "保存草稿").trigger("click");
  await flushPromises();
  s.readError();
  await button(s.w, "重新加载配置").trigger("click");
  await button(s.w, "放弃本地修改并加载").trigger("click");
  await flushPromises();
  expect(
    s.w.get<HTMLInputElement>('input[maxlength="128"]').element.value,
  ).toBe("保留输入");
  expect(s.w.find(".edit-conflict").exists()).toBe(true);
});
it("publishes through the same atomic request for configuration editors", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  await button(w, "发布新版本").trigger("click");
  await flushPromises();
  const write = fetchMock.mock.calls.find(([, o]) => o?.method === "PUT")!;
  expect(JSON.parse(write[1]!.body as string).publish).toBe(true);
  expect(
    fetchMock.mock.calls.some(
      ([p, o]) => p.endsWith("/versions") && o?.method === "POST",
    ),
  ).toBe(false);
});
it("preserves relation edit fields on a stale save and sends the collection token", async () => {
  const s = setup(true);
  await flushPromises();
  await button(s.w, "编辑协作").trigger("click");
  await s.w
    .get<HTMLInputElement>('form.collaboration-edit input[type="number"]')
    .setValue(37);
  s.reject();
  await s.w.get("form.collaboration-edit").trigger("submit");
  await flushPromises();
  expect(
    s.w.get<HTMLInputElement>('form.collaboration-edit input[type="number"]')
      .element.value,
  ).toBe("37");
  expect(s.w.find(".edit-conflict").exists()).toBe(true);
  const call = s.fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(new Headers(call[1]!.headers).get("If-Match")).toBe('"relations"');
});
it("protects relation deletion and only clears the stale form after reloading succeeds", async () => {
  const s = setup(true);
  await flushPromises();
  s.reject();
  await button(s.w, "移除关系").trigger("click");
  await flushPromises();
  const call = s.fetchMock.mock.calls.find(([, o]) => o?.method === "DELETE")!;
  expect(new Headers(call[1]!.headers).get("If-Match")).toBe('"relations"');
  expect(s.w.text()).toContain("子助手");
  await button(s.w, "重新加载配置").trigger("click");
  await button(s.w, "放弃本地修改并加载").trigger("click");
  await flushPromises();
  expect(s.w.find(".edit-conflict").exists()).toBe(false);
});
it("aborts configuration reads when the editor is unmounted", async () => {
  const s = setup();
  await flushPromises();
  const call = s.fetchMock.mock.calls.find(([p]) =>
    p.endsWith("/configuration"),
  )!;
  s.w.unmount();
  expect(call[1]!.signal?.aborted).toBe(true);
});

it("recovers a created wizard after a conditional save failure without creating a duplicate Agent", async () => {
  const s = setup(false, true);
  await flushPromises();
  await s.w
    .get<HTMLInputElement>('input[maxlength="128"]')
    .setValue("新建本地名称");
  s.reject();
  for (let i = 0; i < 3; i++) {
    await s.w.get("form.agent-config").trigger("submit");
    await flushPromises();
  }
  expect(s.w.find(".edit-conflict").exists()).toBe(true);
  s.latest();
  s.emptyDraft();
  await button(s.w, "重新加载配置").trigger("click");
  await button(s.w, "放弃本地修改并加载").trigger("click");
  await flushPromises();
  expect(s.w.find(".edit-conflict").exists()).toBe(false);
  await button(s.w, "上一步").trigger("click");
  expect(s.w.get<HTMLTextAreaElement>("textarea").element.value).toBe(
    "你是一位认真、可靠的助手，根据任务要求提供可核对的结果。",
  );
  await button(s.w, "上一步").trigger("click");
  expect(
    s.w.get<HTMLInputElement>('input[maxlength="128"]').element.value,
  ).toBe("最新名称");
  for (let i = 0; i < 3; i++) {
    await s.w.get("form.agent-config").trigger("submit");
    await flushPromises();
  }
  await flushPromises();
  expect(
    s.fetchMock.mock.calls.filter(
      ([p, o]) => p === "/api/v1/agents" && o?.method === "POST",
    ),
  ).toHaveLength(1);
});
