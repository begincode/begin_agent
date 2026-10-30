import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TaskWorkspace from "./TaskWorkspace.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
});
function setup(permissions = ["tasks.read", "tasks.create"]) {
  const detail = {
    title: "任务",
    status: "SUCCEEDED",
    created_by: "owner",
    input: {},
    artifacts: [],
    turns: [
      { turn_code: "turn_original", prompt: "要求", status: "SUCCEEDED" },
    ],
  };
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "POST")
      return new Response(
        JSON.stringify(path.endsWith("/rerun") ? { detail: "可重试错误" } : {}),
        { status: path.endsWith("/rerun") ? 503 : 200 },
      );
    return new Response(JSON.stringify(detail));
  });
  vi.stubGlobal("fetch", fetchMock);
  const state: Workspace = {
    identity: ref({ user_code: "owner" }),
    data: ref({ agents: [] }),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => permissions.includes(p),
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      try {
        await fn();
      } catch {}
    },
  };
  const w = mount(TaskWorkspace, {
    props: { code: "tsk_one" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, state, fetchMock };
}
it("reuses the idempotency key and immutable source after a failed rerun request", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  const button = w.findAll("button").find((b) => b.text() === "重新执行")!;
  await button.trigger("click");
  await flushPromises();
  await button.trigger("click");
  await flushPromises();
  const calls = fetchMock.mock.calls.filter(([p]) => p.endsWith("/rerun"));
  expect(calls).toHaveLength(2);
  const first = JSON.parse(calls[0]![1]!.body as string),
    second = JSON.parse(calls[1]![1]!.body as string);
  expect(first).toEqual(second);
  expect(first.source_turn_code).toBe("turn_original");
  expect(first.idempotency_key).toBeTruthy();
});
it("uses modified Enter to send while preserving plain Enter and composition", async () => {
  const { w, fetchMock, state } = setup();
  await flushPromises();
  const input = w.get("textarea");
  await input.setValue("后续要求");
  await input.trigger("keydown", { key: "Enter" });
  await input.trigger("keydown", {
    key: "Enter",
    ctrlKey: true,
    isComposing: true,
  });
  expect(
    fetchMock.mock.calls.filter(([, o]) => o?.method === "POST"),
  ).toHaveLength(0);
  state.busy.value = true;
  await input.trigger("keydown", { key: "Enter", ctrlKey: true });
  expect(
    fetchMock.mock.calls.filter(([, o]) => o?.method === "POST"),
  ).toHaveLength(0);
  state.busy.value = false;
  await flushPromises();
  await input.trigger("keydown", { key: "Enter", metaKey: true });
  await flushPromises();
  const calls = fetchMock.mock.calls.filter(([p]) => p.endsWith("/messages"));
  expect(calls).toHaveLength(1);
  expect(JSON.parse(calls[0]![1]!.body as string).prompt).toBe("后续要求");
});
it("hides successful rerun for read-only accounts", async () => {
  const { w } = setup(["tasks.read"]);
  await flushPromises();
  expect(w.findAll("button").some((b) => b.text() === "重新执行")).toBe(false);
});
