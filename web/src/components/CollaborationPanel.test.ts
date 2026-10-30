import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import CollaborationPanel from "./CollaborationPanel.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
});
function setup(manage = true) {
  let row = {
    relation_code: "rel_one",
    sub_agent_code: "agt_child",
    sub_agent_name: "已有子助手",
    sub_agent_status: 1,
    sub_agent_version_no: 2,
    sub_agent_version_code: "v2",
    call_mode: "ON_DEMAND",
    timeout_seconds: 120,
    enabled: true,
  };
  const fetchMock = vi.fn(async (_path: string, options: RequestInit = {}) => {
    if (options.method === "PATCH") {
      row = { ...row, ...JSON.parse(options.body as string) };
      return new Response(JSON.stringify(row));
    }
    if (options.method === "POST") return new Response("{}");
    return new Response(JSON.stringify([row]));
  });
  vi.stubGlobal("fetch", fetchMock);
  const state: Workspace = {
    identity: ref(null),
    data: ref({
      agents: [
        {
          agent_code: "agt_child",
          agent_name: "已有子助手",
          status: 1,
          current_version_code: "v2",
        },
        {
          agent_code: "agt_next",
          agent_name: "新子助手",
          status: 1,
          current_version_code: "v1",
        },
        {
          agent_code: "agt_disabled",
          agent_name: "停用子助手",
          status: 0,
          current_version_code: "v1",
        },
      ],
    }),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => p === "agents.read" || (manage && p === "agents.manage"),
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(CollaborationPanel, {
    props: { code: "agt_master" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, fetchMock };
}
it("edits mode timeout and enabled state without changing the selected child", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  await w
    .findAll("button")
    .find((b) => b.text() === "编辑协作")!
    .trigger("click");
  const form = w.get("form.collaboration-edit");
  await form.get("select").setValue("PARALLEL");
  await form.get('input[type="number"]').setValue(31);
  await form.get('input[type="checkbox"]').setValue(false);
  await form.trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(call[0]).toBe("/api/v1/agents/agt_master/relations/rel_one");
  expect(JSON.parse(call[1]!.body as string)).toEqual({
    call_mode: "PARALLEL",
    timeout_seconds: 31,
    enabled: false,
  });
  expect(w.text()).toContain("关系停用");
  expect(w.text()).toContain("单次超时 31 秒");
  expect(w.text()).toContain("当前发布版本 v2");
});
it("filters existing and disabled candidates and sends the requested timeout", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  const form = w.get("form.collaboration-create");
  expect(form.text()).toContain("新子助手");
  expect(form.text()).not.toContain("已有子助手");
  expect(form.text()).not.toContain("停用子助手");
  await form.get("select").setValue("agt_next");
  await form.get('input[type="number"]').setValue(17);
  await form.trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string)).toMatchObject({
    sub_agent_code: "agt_next",
    timeout_seconds: 17,
  });
});
it("provides a read-only collaboration view for readers", async () => {
  const { w } = setup(false);
  await flushPromises();
  expect(w.text()).toContain("已有子助手");
  expect(w.findAll("form")).toHaveLength(0);
  expect(
    w
      .findAll("button")
      .some((b) => ["编辑协作", "移除关系"].includes(b.text())),
  ).toBe(false);
});
