import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TaskArtifacts from "./TaskArtifacts.vue";
import { workspaceKey, type Workspace } from "../workspace";
import { api, apiFetch } from "../api";
vi.mock("../api", () => ({ api: vi.fn(), apiFetch: vi.fn() }));
const wrappers: ReturnType<typeof mount>[] = [];
const file = {
  artifact_code: "art_file",
  artifact_name: "报告.bin",
  artifact_type: "FILE",
  content: {
    file_name: "报告.bin",
    size_bytes: 3,
    sha256: "abc",
    uploaded_by: "usr_one",
  },
  run_code: "run_one",
  version_no: 1,
};
function page(items = [file], total = items.length) {
  return { items, total, max_file_bytes: 8 };
}
function render(readonly = false, canUpload = true) {
  const state: Workspace = {
    identity: ref(null),
    data: ref({}),
    busy: ref(false),
    readonly: ref(readonly),
    can: (p) => p !== "runs.read",
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(TaskArtifacts, {
    props: { artifacts: [], taskCode: "tsk_one", canUpload },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return w;
}
function button(w: ReturnType<typeof mount>, text: string) {
  return w.findAll("button").find((b) => b.text().startsWith(text))!;
}
async function select(
  w: ReturnType<typeof mount>,
  data = "abc",
  name = "报告.bin",
) {
  const input = w.get('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    configurable: true,
    value: [new File([data], name)],
  });
  await input.trigger("change");
}
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});
it("keeps the selected file and retry key after failure, then refreshes the saved file list", async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(page([]))
    .mockRejectedValueOnce(new Error("暂不可用"))
    .mockResolvedValueOnce(file)
    .mockResolvedValueOnce(page());
  const w = render();
  await flushPromises();
  await select(w);
  await button(w, "上传文件").trigger("click");
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("暂不可用");
  expect(w.text()).toContain("报告.bin");
  const first = vi.mocked(api).mock.calls[1]![1]!;
  expect(first.body).toBeInstanceOf(File);
  expect(
    (first.headers as Record<string, string>)["Idempotency-Key"],
  ).toBeTruthy();
  await button(w, "上传文件").trigger("click");
  await flushPromises();
  expect(vi.mocked(api).mock.calls[2]![1]!.headers).toEqual(first.headers);
  expect(w.text()).toContain("文件产物已上传");
  expect(w.text()).toContain("人工上传");
  expect(w.find('a[href="#/runs/run_one"]').exists()).toBe(false);
});
it("rejects empty and oversized selections without uploading", async () => {
  vi.mocked(api).mockResolvedValue(page([]));
  const w = render();
  await flushPromises();
  await select(w, "");
  expect(w.get('[role="alert"]').text()).toContain("空文件");
  expect(button(w, "上传文件").attributes("disabled")).toBeDefined();
  await select(w, "123456789");
  expect(w.get('[role="alert"]').text()).toContain("上限");
  expect(button(w, "上传文件").attributes("disabled")).toBeDefined();
  expect(api).toHaveBeenCalledTimes(1);
});
it("loads more files and retries a failed list request", async () => {
  vi.mocked(api)
    .mockRejectedValueOnce(new Error("读取失败"))
    .mockResolvedValueOnce(page([file], 2))
    .mockResolvedValueOnce(page([{ ...file, artifact_code: "art_two" }], 2));
  const w = render();
  await flushPromises();
  await button(w, "重新读取").trigger("click");
  await flushPromises();
  await button(w, "加载更多").trigger("click");
  await flushPromises();
  expect(vi.mocked(api).mock.calls[2]![0]).toContain("offset=1");
  expect(w.findAll("article")).toHaveLength(2);
  expect(w.text()).not.toContain("加载更多");
});
it("uses authenticated file fetch and reports download failure before creating a URL", async () => {
  vi.mocked(api).mockResolvedValue(page());
  vi.mocked(apiFetch).mockResolvedValue({
    ok: false,
    status: 403,
    json: async () => ({ detail: "权限已撤销" }),
  } as Response);
  const create = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: vi.fn() });
  const w = render(true);
  await flushPromises();
  expect(w.text()).not.toContain("选择文件");
  await button(w, "下载文件").trigger("click");
  await flushPromises();
  expect(apiFetch).toHaveBeenCalledWith(
    "/api/v1/tasks/tsk_one/artifacts/art_file/download",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(w.get('[role="alert"]').text()).toContain("权限已撤销");
  expect(create).not.toHaveBeenCalled();
});
it("downloads binary content with its original name and cancels requests on leave", async () => {
  vi.mocked(api).mockResolvedValue(page());
  const blob = new Blob([new Uint8Array([0, 255, 1])]);
  vi.mocked(apiFetch).mockResolvedValue({
    ok: true,
    blob: async () => blob,
  } as Response);
  const create = vi.fn().mockReturnValue("blob:file"),
    revoke = vi.fn();
  vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
  let name = "";
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    name = this.download;
  });
  const w = render(false, false);
  await flushPromises();
  expect(w.text()).not.toContain("选择文件");
  await button(w, "下载文件").trigger("click");
  await flushPromises();
  expect(create).toHaveBeenCalledWith(blob);
  expect(name).toBe("报告.bin");
  const signal = vi.mocked(apiFetch).mock.calls[0]![1]!.signal!;
  w.unmount();
  expect(signal.aborted).toBe(true);
  expect(revoke).toHaveBeenCalledWith("blob:file");
});
it("labels generated files without claiming a human upload", async () => {
  vi.mocked(api).mockResolvedValue({
    items: [
      {
        ...file,
        content: { ...file.content, source: "GENERATED", uploaded_by: null },
      },
    ],
    total: 1,
    max_file_bytes: 8,
  });
  const w = render(false, false);
  await flushPromises();
  expect(w.text()).toContain("执行生成");
  expect(w.text()).not.toContain("人工上传");
  expect(w.text()).not.toContain("上传人");
});
