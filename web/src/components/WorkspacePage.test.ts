import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import WorkspacePage from "./WorkspacePage.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.forEach((w) => w.unmount());
  wrappers.length = 0;
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("searches all task pages on the server and resets pagination when filters change", async () => {
  const row = {
    task_code: "tsk_old",
    title: "Older matching task",
    status: "FAILED",
    created_at: "2026-10-29",
  };
  const fetchMock = vi.fn(
    async (url: string) =>
      new Response(JSON.stringify(url.includes("search=older") ? [row] : []), {
        status: 200,
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
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
  const wrapper = mount(WorkspacePage, {
    props: { kind: "tasks" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  vi.useFakeTimers();
  await wrapper.get("input").setValue("older");
  await wrapper.get('select[aria-label="任务范围"]').setValue("mine");
  await wrapper.get('select[aria-label="状态筛选"]').setValue("FAILED");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  const url = fetchMock.mock.calls.at(-1)![0];
  expect(url).toContain("offset=0");
  expect(url).toContain("search=older");
  expect(url).toContain("status=FAILED");
  expect(url).toContain("mine=true");
  expect(wrapper.text()).toContain("Older matching task");
});
it("sends run filters to the server and renders task names with real usage", async () => {
  const fetchMock = vi.fn(
    async (_path: string) =>
      new Response(
        JSON.stringify([
          {
            run_code: "run_test",
            task_code: "tsk_test",
            task_title: "月报任务",
            agent_name: "运行助手",
            elapsed_ms: 1200,
            tokens: null,
            unreported_calls: 2,
            status: "SUCCEEDED",
            turn_no: 1,
          },
        ]),
      ),
  );
  vi.stubGlobal("fetch", fetchMock);
  const state: Workspace = {
    identity: ref(null),
    data: ref({ agents: [{ agent_code: "agt_test", agent_name: "运行助手" }] }),
    busy: ref(false),
    readonly: ref(false),
    can: () => true,
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const wrapper = mount(WorkspacePage, {
    props: { kind: "runs" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  vi.useFakeTimers();
  await wrapper.get('select[aria-label="执行 Agent"]').setValue("agt_test");
  await wrapper.get('input[aria-label="任务编码"]').setValue("tsk_test");
  await wrapper.get('select[aria-label="运行日期"]').setValue("30");
  await wrapper.get('select[aria-label="运行层级"]').setValue("root");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  const url = String(fetchMock.mock.calls.at(-1)?.[0]);
  for (const part of [
    "agent_code=agt_test",
    "task_code=tsk_test",
    "days=30",
    "root_only=true",
    "offset=0",
  ])
    expect(url).toContain(part);
  expect(wrapper.text()).toContain("月报任务");
  expect(wrapper.text()).toContain("2 次未上报");
});
