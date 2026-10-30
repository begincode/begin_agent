import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import RunInspector from "./RunInspector.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function render() {
  const state: Workspace = {
    identity: ref(null),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: () => true,
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(RunInspector, {
    props: { code: "run_one" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return w;
}
const run = {
  task_code: "tsk_one",
  status: "RUNNING",
  task_title: "测试任务",
  agent_name: "助手",
  elapsed_ms: 1200,
  tokens: null,
  unreported_calls: 1,
  turn_no: 2,
  run_depth: 1,
  parent_run_code: "run_parent",
  root_run_code: "run_root",
  steps: [
    {
      step_code: "s1",
      step_no: 1,
      step_type: "MODEL_CALL",
      name: "模型",
      status: "SUCCEEDED",
    },
    {
      step_code: "s2",
      step_no: 2,
      step_type: "TOOL_CALL",
      name: "失败工具",
      status: "FAILED",
    },
  ],
  messages: [],
};
it("polls active runs, stops at completion and filters recorded steps", async () => {
  vi.useFakeTimers();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(run)))
    .mockResolvedValue(
      new Response(JSON.stringify({ ...run, status: "SUCCEEDED" })),
    );
  vi.stubGlobal("fetch", fetchMock);
  const w = render();
  await flushPromises();
  expect(w.text()).toContain("已上报 Token —");
  expect(w.get('a[href="#/runs/run_parent"]').text()).toContain("父运行");
  await w.get('select[aria-label="步骤状态"]').setValue("FAILED");
  expect(w.findAll("details")).toHaveLength(1);
  expect(w.text()).toContain("失败工具");
  await vi.advanceTimersByTimeAsync(1800);
  await flushPromises();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
it("offers a retry after errors and cancels polling when leaving", async () => {
  vi.useFakeTimers();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "暂不可用" }), { status: 503 }),
    )
    .mockResolvedValue(new Response(JSON.stringify(run)));
  vi.stubGlobal("fetch", fetchMock);
  const w = render();
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("暂不可用");
  await w
    .findAll("button")
    .find((b) => b.text() === "刷新详情")!
    .trigger("click");
  await flushPromises();
  expect(w.find('[role="alert"]').exists()).toBe(false);
  w.unmount();
  await vi.advanceTimersByTimeAsync(5000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
