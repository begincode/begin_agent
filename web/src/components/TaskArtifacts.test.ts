import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TaskArtifacts from "./TaskArtifacts.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const text = "# 结果\n\n```ts\n  const n = 1;\n```\n";
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
  const w = mount(TaskArtifacts, {
    props: {
      artifacts: [
        {
          artifact_code: "art_one",
          artifact_name: "../../危险:标题",
          artifact_type: "TEXT",
          content: { text },
          version_no: 2,
          run_code: "run_original",
        },
      ],
    },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return w;
}
it("copies the exact original text and reports clipboard failures", async () => {
  const writeText = vi
    .fn()
    .mockRejectedValueOnce(new Error("denied"))
    .mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  const w = render();
  const button = w.findAll("button").find((b) => b.text() === "复制原文")!;
  await button.trigger("click");
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("复制失败");
  expect(w.text()).not.toContain("原文已复制");
  await button.trigger("click");
  await flushPromises();
  expect(writeText).toHaveBeenLastCalledWith(text);
  expect(w.get('[role="status"]').text()).toContain("原文已复制");
});
it("downloads original Markdown with a safe name and revokes its URL", async () => {
  const create = vi.fn().mockReturnValue("blob:artifact"),
    revoke = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
  let filename = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    filename = this.download;
  });
  const w = render();
  await w
    .findAll("button")
    .find((b) => b.text() === "下载 Markdown")!
    .trigger("click");
  expect(filename).toMatch(/-v2\.md$/);
  expect(filename).not.toMatch(/[\\/:]/);
  expect(filename).not.toMatch(/^\./);
  expect(create).toHaveBeenCalledTimes(1);
  const blob = create.mock.calls[0]![0] as Blob;
  const content = await new Promise<string>((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.readAsText(blob);
  });
  expect(content).toBe(text);
  expect(w.get('a[href="#/runs/run_original"]').text()).toContain(
    "run_original",
  );
  w.unmount();
  expect(revoke).toHaveBeenCalledWith("blob:artifact");
});
