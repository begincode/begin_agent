import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TenantResources from "./TenantResources.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function setup(manage = true) {
  let enabled = true;
  const row = (i: number) => ({
    resource_kind: "TOOL",
    resource_code: "tol_" + i,
    resource_name: "目录工具" + i,
    active: true,
    enabled: i === 0 ? enabled : true,
    available: i === 0 ? enabled : true,
    availability: i === 0 && !enabled ? "REVOKED" : "AVAILABLE",
  });
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "PUT") {
      enabled = JSON.parse(options.body as string).enabled;
      return new Response("{}");
    }
    if (path.endsWith("overview"))
      return new Response(
        JSON.stringify({
          tenant: {
            tenant_name: "测试租户",
            tenant_code: "ten_one",
            status: 1,
          },
          total: 61,
          available: enabled ? 61 : 60,
          members: null,
          agents: 1,
          groups: [
            {
              resource_kind: "TOOL",
              total: 61,
              available: enabled ? 61 : 60,
              revoked: enabled ? 0 : 1,
              inactive: 0,
              unchecked: 0,
            },
          ],
        }),
      );
    const params = new URL(path, "http://localhost").searchParams;
    return new Response(
      JSON.stringify(
        params.has("resource_kind")
          ? [row(0)]
          : Array.from(
              { length: params.get("offset") === "50" ? 11 : 50 },
              (_, i) => row(i + Number(params.get("offset") || 0)),
            ),
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const load = vi.fn(async () => {});
  const state: Workspace = {
    identity: ref(null),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => p === "resources.read" || (manage && p === "tenant.manage"),
    go: () => {},
    load,
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(TenantResources, {
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, fetchMock, load, state };
}
it("paginates on the server, resets filters and reloads eligibility after a grant change", async () => {
  const { w, fetchMock, load } = setup();
  await flushPromises();
  await w
    .findAll("button")
    .find((b) => b.text() === "下一页")!
    .trigger("click");
  await flushPromises();
  expect(fetchMock.mock.calls.some(([p]) => p.includes("offset=50"))).toBe(
    true,
  );
  vi.useFakeTimers();
  await w.get('select[aria-label="资源类型"]').setValue("TOOL");
  await w.get('select[aria-label="可用状态"]').setValue("AVAILABLE");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  const url = fetchMock.mock.calls
    .map(([p]) => p)
    .filter((p) => p.includes("resources?"))
    .at(-1)!;
  expect(url).toContain("offset=0");
  expect(url).toContain("resource_kind=TOOL");
  expect(url).toContain("availability=AVAILABLE");
  await w
    .findAll("button")
    .find((b) => b.text() === "撤销授权")!
    .trigger("click");
  await flushPromises();
  expect(load).toHaveBeenCalledOnce();
  expect(w.text()).toContain("授权关闭");
});
it("keeps grants unavailable for readers and disabled tenants", async () => {
  const { w, state } = setup(false);
  await flushPromises();
  expect(w.findAll("button").some((b) => b.text() === "撤销授权")).toBe(false);
  state.can = () => true;
  w.unmount();
  const manager = setup();
  manager.state.readonly.value = true;
  await flushPromises();
  expect(
    manager.w
      .findAll("button")
      .filter((b) => b.text() === "撤销授权")
      .every((b) => b.attributes("disabled") !== undefined),
  ).toBe(true);
});
