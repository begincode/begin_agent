import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import AgentEditor from "./AgentEditor.vue";
import {
  keepCreation,
  readCreation,
  startCreation,
  type PendingCreation,
} from "../agentCreation";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
function setup(
  drop: "before" | "after" | "none" = "none",
  restored?: PendingCreation,
  existing = false,
) {
  const receipt = new Map<string, any>();
  if (existing && restored)
    receipt.set(restored.key, {
      ...restored.agent,
      agent_code: "agt_1",
      configuration_etag: '"initial"',
    });
  let count = existing ? 1 : 0,
    lost = drop !== "none",
    changed = false;
  const keys: string[] = [],
    bodies: any[] = [];
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (path === "/api/v1/agents" && options.method === "POST") {
      const key = new Headers(options.headers).get("Idempotency-Key")!;
      if (!key) throw new Error("missing idempotency key");
      keys.push(key);
      if (lost && drop === "before") {
        lost = false;
        throw new Error("Failed to fetch");
      }
      const replay = receipt.has(key);
      if (!replay) {
        count++;
        receipt.set(key, {
          ...JSON.parse(options.body as string),
          agent_code: "agt_" + count,
          configuration_etag: '"initial"',
        });
      }
      if (lost && drop === "after") {
        lost = false;
        throw new Error("Failed to fetch");
      }
      return new Response(JSON.stringify(receipt.get(key)), {
        status: replay ? 200 : 201,
      });
    }
    if (path.includes("/creation-requests/")) {
      const value = receipt.get(decodeURIComponent(path.split("/").at(-1)!));
      return new Response(JSON.stringify(value || { detail: "尚未找到" }), {
        status: value ? 200 : 404,
      });
    }
    if (options.method === "PUT") {
      bodies.push(JSON.parse(options.body as string));
      return new Response(
        JSON.stringify(
          changed ? { detail: "配置已变化" } : { draft_revision: 1 },
        ),
        { status: changed ? 412 : 200 },
      );
    }
    return new Response("[]");
  });
  if (restored) keepCreation(restored);
  vi.stubGlobal("fetch", fetchMock);
  const errors: string[] = [];
  const state: Workspace = {
    identity: ref({ user_code: "owner", tenant_code: "tenant" }),
    data: ref({
      models: [
        { profile_code: "mdl_one", display_name: "模型", enabled: true },
      ],
      tools: [],
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
  const w = mount(AgentEditor, {
    props: { code: "new", tab: "config" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return {
    w,
    fetchMock,
    keys,
    bodies,
    state,
    errors,
    count: () => count,
    change: () => {
      changed = true;
    },
  };
}
function button(w: ReturnType<typeof mount>, label: string) {
  return w.findAll("button").find((b) => b.text() === label)!;
}
async function submit(w: ReturnType<typeof mount>) {
  await flushPromises();
  await w.get('input[maxlength="128"]').setValue("本地创建名称");
  await w.get("form.agent-config").trigger("submit");
  await flushPromises();
  await w.get("textarea").setValue("本地未保存的长提示词");
  await w.get("form.agent-config").trigger("submit");
  await flushPromises();
  await w.get("form.agent-config").trigger("submit");
  await flushPromises();
}
it("retries a lost creation response with the same key and keeps the local configuration", async () => {
  const s = setup("after");
  await submit(s.w);
  expect(s.count()).toBe(1);
  expect(s.w.text()).toContain("创建结果尚未确认");
  expect(s.bodies).toHaveLength(0);
  expect(
    (s.w.get("form.agent-config").element as HTMLFormElement).checkValidity(),
  ).toBe(true);
  await s.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  expect(s.keys).toHaveLength(2);
  expect(s.keys[0]).toBe(s.keys[1]);
  expect(s.count()).toBe(1);
  expect(s.bodies[0].config.system_prompt).toBe("本地未保存的长提示词");
  expect(s.state.go).toHaveBeenCalledWith("agents/agt_1/config");
  expect(readCreation("owner", "tenant").pending).toBeNull();
});
it("can query a committed creation without another create request", async () => {
  const s = setup("after");
  await submit(s.w);
  await button(s.w, "查询创建结果").trigger("click");
  await flushPromises();
  expect(s.keys).toHaveLength(1);
  expect(s.bodies).toHaveLength(0);
  expect(s.w.text()).toContain("Agent 已创建");
  expect(
    (s.w.get("form.agent-config").element as HTMLFormElement).checkValidity(),
  ).toBe(true);
  await s.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  expect(s.keys).toHaveLength(1);
  expect(s.bodies).toHaveLength(1);
});
it("preserves the original request after a missing lookup and retries without inventing a key", async () => {
  const s = setup("before");
  await submit(s.w);
  await button(s.w, "查询创建结果").trigger("click");
  await flushPromises();
  expect(s.w.text()).toContain("当前状态仍未确认");
  expect(s.keys).toHaveLength(1);
  expect(
    (s.w.get("form.agent-config").element as HTMLFormElement).checkValidity(),
  ).toBe(true);
  await s.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  expect(s.count()).toBe(1);
  expect(s.keys[0]).toBe(s.keys[1]);
});
it("restores after reload and never writes a default configuration over the recovered Agent", async () => {
  const pending = startCreation("owner", "tenant", {
    agent_name: "先前创建",
    agent_type: "NORMAL",
    status: 1,
  });
  const s = setup("none", pending, true);
  await flushPromises();
  expect(s.w.text()).toContain("恢复 Agent 创建");
  expect(s.w.find("form.agent-config").exists()).toBe(false);
  await button(s.w, "查询创建结果").trigger("click");
  await flushPromises();
  expect(s.keys).toHaveLength(0);
  expect(s.bodies).toHaveLength(0);
  expect(s.state.go).toHaveBeenCalledWith("agents/agt_1/config");
});
it("retries the persisted original metadata after reload without saving empty editor defaults", async () => {
  const pending = startCreation("owner", "tenant", {
    agent_name: "先前未提交",
    agent_type: "ROUTER",
    status: 1,
  });
  const s = setup("none", pending);
  await flushPromises();
  await button(s.w, "重试原创建请求").trigger("click");
  await flushPromises();
  expect(s.keys).toEqual([pending.key]);
  expect(s.count()).toBe(1);
  expect(s.bodies).toHaveLength(0);
});
it("retains stale-initial-condition conflict protection after recovering a creation", async () => {
  const s = setup("after");
  await submit(s.w);
  s.change();
  expect(
    (s.w.get("form.agent-config").element as HTMLFormElement).checkValidity(),
  ).toBe(true);
  await s.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  expect(s.count()).toBe(1);
  expect(s.w.find(".edit-conflict").exists()).toBe(true);
  expect(readCreation("owner", "tenant").pending).not.toBeNull();
});
it("does not write or navigate into a new tenant when an old creation returns late", async () => {
  const s = setup();
  await flushPromises();
  let resolve!: (r: Response) => void;
  s.fetchMock.mockImplementationOnce(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  await s.w.get('input[maxlength="128"]').setValue("切换前名称");
  for (let i = 0; i < 3; i++) {
    await s.w.get("form.agent-config").trigger("submit");
    await flushPromises();
  }
  s.state.identity.value = { user_code: "owner", tenant_code: "other" };
  resolve(
    new Response(
      JSON.stringify({
        agent_code: "agt_old",
        configuration_etag: '"initial"',
      }),
    ),
  );
  await flushPromises();
  expect(s.bodies).toHaveLength(0);
  expect(s.state.go).not.toHaveBeenCalled();
  expect(readCreation("owner", "tenant").pending).not.toBeNull();
});
it("keeps in-page recovery when persistence fails and reports that limitation", async () => {
  const s = setup("after");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  await submit(s.w);
  expect(s.w.text()).toContain("浏览器无法保留恢复记录");
  expect(
    (s.w.get("form.agent-config").element as HTMLFormElement).checkValidity(),
  ).toBe(true);
  await s.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  expect(s.count()).toBe(1);
  expect(s.keys[0]).toBe(s.keys[1]);
});
it("offers full wizard restore before submission and restores prompt and step explicitly", async () => {
  const first = setup();
  await flushPromises();
  await first.w.get('input[maxlength="128"]').setValue("完整恢复名称");
  await first.w.get("form.agent-config").trigger("submit");
  await flushPromises();
  await first.w.get("textarea").setValue("刷新后继续的提示词");
  first.w.unmount();
  const second = setup();
  await flushPromises();
  expect(second.w.text()).toContain("发现本标签页未保存的向导草稿");
  expect(second.w.find("form.agent-config").exists()).toBe(false);
  await button(second.w, "继续编辑草稿").trigger("click");
  await flushPromises();
  expect((second.w.get("textarea").element as HTMLTextAreaElement).value).toBe(
    "刷新后继续的提示词",
  );
  expect(second.keys).toHaveLength(0);
});
it("discards a local draft without discarding an unresolved creation identity", async () => {
  const first = setup("after");
  await submit(first.w);
  const key = first.keys[0];
  first.w.unmount();
  const second = setup();
  await flushPromises();
  await button(second.w, "放弃本地草稿").trigger("click");
  await flushPromises();
  expect(second.w.text()).toContain("恢复 Agent 创建");
  expect(readCreation("owner", "tenant").pending?.key).toBe(key);
  expect(second.w.find("form.agent-config").exists()).toBe(false);
});
