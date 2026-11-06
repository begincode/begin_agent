import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import TaskInputs from "./TaskInputs.vue";
import { apiFetch } from "../api";
vi.mock("../api", () => ({ apiFetch: vi.fn() }));
afterEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
});
it("aborts an old attachment download on navigation without creating a new download", async () => {
  let resolve!: (r: Response) => void;
  vi.mocked(apiFetch).mockImplementation(
    () => new Promise((r) => (resolve = r)),
  );
  const w = mount(TaskInputs, {
    props: {
      taskCode: "tsk_one",
      files: [{ file_name: "参考.md", size_bytes: 5 }],
    },
  });
  await w.get("button").trigger("click");
  const signal = vi.mocked(apiFetch).mock.calls[0][1]?.signal;
  w.unmount();
  expect(signal?.aborted).toBe(true);
  resolve(new Response("old document"));
  await flushPromises();
});
it("shows a failed permission check without triggering a file", async () => {
  vi.mocked(apiFetch).mockResolvedValue(new Response("", { status: 403 }));
  const w = mount(TaskInputs, {
    props: {
      taskCode: "tsk_one",
      files: [{ file_name: "参考.md", size_bytes: 5 }],
    },
  });
  await w.get("button").trigger("click");
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("403");
  w.unmount();
});
