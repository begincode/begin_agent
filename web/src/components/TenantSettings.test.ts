import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import TenantSettings from "./TenantSettings.vue";
import { selectTenant, activeTenant } from "../api";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
  selectTenant("");
});
function setup(admin = false, empty = false) {
  let name = "当前租户",
    hasTenant = !empty;
  const identity = () => ({
    user_code: "actor",
    tenant_code: hasTenant ? "ten_current" : "",
    platform_admin: admin,
    tenants: hasTenant
      ? [{ tenant_code: "ten_current", tenant_name: name, status: 1 }]
      : [],
  });
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "PATCH") {
      name = JSON.parse(options.body as string).tenant_name;
      return new Response("{}");
    }
    if (options.method === "POST") {
      hasTenant = true;
      name = JSON.parse(options.body as string).tenant_name;
      return new Response(JSON.stringify({ tenant_code: "ten_current" }));
    }
    if (path.endsWith("/me")) return new Response(JSON.stringify(identity()));
    return new Response(
      JSON.stringify({
        tenant: { tenant_code: "ten_current", tenant_name: name, status: 1 },
        members: 2,
        agents: 0,
        resources: 0,
        available_resources: 0,
        administrators: 1,
        published_agents: 0,
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const load = vi.fn(async () => {});
  const state: Workspace = {
    identity: ref(identity()),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => p === "tenant.manage",
    go: () => {},
    load,
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(TenantSettings, {
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, fetchMock, state, load };
}
it("renames the current tenant without submitting state and refreshes the tenant identity", async () => {
  const { w, fetchMock, state, load } = setup();
  await flushPromises();
  await w.get("input:not([readonly])").setValue("修改后的名称");
  await w.get("form").trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(call[0]).toBe("/api/v1/tenant");
  expect(JSON.parse(call[1]!.body as string)).toEqual({
    tenant_name: "修改后的名称",
  });
  expect(state.identity.value?.tenants[0].tenant_name).toBe("修改后的名称");
  expect(load).toHaveBeenCalledOnce();
  expect(w.find("select").exists()).toBe(false);
});
it("prevents writes for ordinary managers of disabled tenants", async () => {
  const { w, state, fetchMock } = setup();
  state.readonly.value = true;
  await flushPromises();
  expect(
    w
      .findAll("button")
      .find((b) => b.text() === "保存当前租户")!
      .attributes("disabled"),
  ).toBeDefined();
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(fetchMock.mock.calls.some(([, o]) => o?.method === "PATCH")).toBe(
    false,
  );
});
it("uses the platform path for state control and selects a first created tenant", async () => {
  const { w, fetchMock } = setup(true);
  await flushPromises();
  await w.get("select").setValue(0);
  await w.get("form").trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(call[0]).toBe("/api/v1/tenants/ten_current");
  expect(JSON.parse(call[1]!.body as string).status).toBe(0);
  w.unmount();
  wrappers.splice(wrappers.indexOf(w), 1);
  const first = setup(true, true);
  await flushPromises();
  (first.w.get("details").element as HTMLDetailsElement).open = true;
  await first.w.get("input").setValue("第一个租户");
  await first.w.get("form").trigger("submit");
  await flushPromises();
  expect(activeTenant.value).toBe("ten_current");
  expect(first.state.identity.value?.tenant_code).toBe("ten_current");
});
