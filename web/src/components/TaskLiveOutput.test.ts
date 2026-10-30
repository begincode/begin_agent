import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TaskWorkspace from "./TaskWorkspace.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function setup(history = false) {
  const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
  let status = "RUNNING",
    text: string | undefined;
  const detail = () => ({
    title: "实时任务",
    status,
    created_by: "owner",
    input: {},
    artifacts: [],
    turns: [
      ...(history
        ? [
            {
              turn_code: "old",
              turn_no: 1,
              prompt: "旧要求",
              status: "FAILED",
              preview: { call_no: 1, content: "上一轮中断草稿" },
            },
          ]
        : []),
      {
        turn_code: "turn1",
        turn_no: history ? 2 : 1,
        prompt: "要求",
        status,
        output: text ? { text } : null,
      },
    ],
  });
  const fetchMock = vi.fn(async (path: string) =>
    path.endsWith("/stream")
      ? new Response(
          new ReadableStream<Uint8Array>({
            start(c) {
              streams.push(c);
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        )
      : new Response(JSON.stringify(detail())),
  );
  vi.stubGlobal("fetch", fetchMock);
  const state: Workspace = {
    identity: ref({ user_code: "owner" }),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => ["tasks.read", "tasks.create"].includes(p),
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(TaskWorkspace, {
    props: { code: "task1" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  const send = (type: string, data: unknown) =>
    streams
      .at(-1)!
      .enqueue(
        new TextEncoder().encode(
          `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`,
        ),
      );
  return {
    w,
    streams,
    fetchMock,
    send,
    finish() {
      status = "SUCCEEDED";
      text = "第一段第二段";
    },
  };
}
it("replaces cumulative draft snapshots, keeps input disabled and confirms canonical completion", async () => {
  const { w, send, finish } = setup();
  await flushPromises();
  send("snapshot", {
    revision: "a",
    turn_no: 1,
    status: "RUNNING",
    preview: { call_no: 1, content: "第一段", reasoning: "分析中" },
    activity: [],
  });
  await flushPromises();
  expect(w.text()).toContain("实时草稿");
  expect(w.find(".task-reasoning").exists()).toBe(true);
  expect(w.get("textarea").attributes("disabled")).toBeDefined();
  send("snapshot", {
    revision: "b",
    turn_no: 1,
    status: "RUNNING",
    preview: { call_no: 1, content: "第一段第二段" },
    activity: [],
  });
  await flushPromises();
  expect(w.find(".task-live-draft").text()).toContain("第一段第二段");
  expect(w.text()).not.toContain("第一段第一段");
  finish();
  send("snapshot", {
    revision: "c",
    turn_no: 1,
    status: "SUCCEEDED",
    preview: { call_no: 1, content: "第一段第二段" },
    activity: [],
    terminal: true,
  });
  send("complete", {});
  await flushPromises();
  expect(w.find(".task-live-draft").exists()).toBe(false);
  expect(w.get("textarea").attributes("disabled")).toBeUndefined();
});
it("reconnects from latest snapshot and marks interrupted output as non-final", async () => {
  vi.useFakeTimers();
  const { w, send, streams, fetchMock } = setup();
  await flushPromises();
  send("snapshot", {
    revision: "a",
    turn_no: 1,
    status: "RUNNING",
    preview: { call_no: 1, content: "部分输出" },
    activity: [],
  });
  await flushPromises();
  streams.at(-1)!.close();
  await flushPromises();
  expect(w.text()).toContain("正在重连");
  await vi.advanceTimersByTimeAsync(1100);
  await flushPromises();
  expect(
    fetchMock.mock.calls.filter(([p]) => p.endsWith("/stream")).length,
  ).toBeGreaterThan(1);
  send("snapshot", {
    revision: "b",
    turn_no: 1,
    status: "FAILED",
    preview: { call_no: 1, content: "部分输出" },
    activity: [],
  });
  await flushPromises();
  expect(w.text()).toContain("中断草稿 · 非最终结果");
});
it("clears streamed data and stops retrying after permission revocation", async () => {
  vi.useFakeTimers();
  const { w, send, fetchMock } = setup();
  await flushPromises();
  send("snapshot", {
    revision: "a",
    turn_no: 1,
    status: "RUNNING",
    preview: { content: "不应继续显示的草稿" },
    activity: [],
  });
  await flushPromises();
  send("access_error", { status: 403, message: "访问已撤销" });
  await flushPromises();
  expect(w.text()).not.toContain("不应继续显示的草稿");
  expect(w.text()).toContain("访问已撤销");
  const calls = fetchMock.mock.calls.length;
  await vi.advanceTimersByTimeAsync(10000);
  expect(fetchMock.mock.calls.length).toBe(calls);
});

it("shows initial connection failure before any preview has arrived", async () => {
  const { w, streams } = setup();
  await flushPromises();
  streams.at(-1)!.error(new Error("initial read failed"));
  await flushPromises();
  expect(w.text()).toContain("连接中断，正在重连");
  expect(w.find(".task-live-draft").exists()).toBe(false);
});

it("keeps failed historical drafts visible while a new turn waits for output", async () => {
  const { w, send } = setup(true);
  await flushPromises();
  send("snapshot", {
    revision: "new",
    turn_no: 2,
    status: "PENDING",
    preview: null,
    activity: [],
  });
  await flushPromises();
  expect(w.find(".task-live-draft").text()).toContain("上一轮中断草稿");
  expect(w.text()).toContain("中断草稿 · 非最终结果");
  expect(w.findAll(".task-live-draft")).toHaveLength(1);
});
