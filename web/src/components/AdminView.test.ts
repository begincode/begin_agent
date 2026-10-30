import { afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";
import AdminView from "./AdminView.vue";
import { workspaceKey, type Workspace } from "../workspace";
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("keeps the newest audit search when an older request resolves last", async () => {
  const json = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
  let release: ((r: Response) => void) | undefined;
  const row = (name: string) => ({
    event_code: name,
    action: name,
    actor_code: "usr",
    actor_name: "用户",
    created_at: "2026-10-30",
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("search=older"))
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      if (url.includes("search=newer")) return json([row("最新结果")]);
      return json([]);
    }),
  );
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
  const w = mount(AdminView, {
    props: { kind: "audit" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  await flushPromises();
  vi.useFakeTimers();
  await w.get("input").setValue("older");
  await vi.advanceTimersByTimeAsync(260);
  await w.get("input").setValue("newer");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  expect(w.text()).toContain("最新结果");
  release!(json([row("过期结果")]));
  await flushPromises();
  expect(w.text()).toContain("最新结果");
  expect(w.text()).not.toContain("过期结果");
  w.unmount();
});
