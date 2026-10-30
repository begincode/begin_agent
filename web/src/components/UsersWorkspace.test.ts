import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import UsersWorkspace from "./UsersWorkspace.vue";
import { activeTenant, selectTenant } from "../api";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
  selectTenant("");
});
function setup(code?: string, admin = true) {
  const user = {
    user_code: "usr_target",
    username: "target",
    display_name: "目标账号",
    status: 1,
    platform_admin: false,
  };
  const go = vi.fn();
  const state: Workspace = {
    identity: ref({ user_code: "usr_actor", platform_admin: admin }),
    data: ref({}),
    busy: ref(false),
    readonly: ref(true),
    can: () => false,
    go,
    load: vi.fn(async () => {}),
    act: async (fn) => {
      await fn();
    },
  };
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method) return new Response(JSON.stringify(user));
    if (path.endsWith("/me"))
      return new Response(
        JSON.stringify({ user_code: "usr_target", platform_admin: false }),
      );
    return new Response(
      JSON.stringify(
        path.includes("?")
          ? Array.from({ length: 50 }, (_, i) => ({
              ...user,
              user_code: "usr_" + i,
            }))
          : user,
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const w = mount(UsersWorkspace, {
    props: { code },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, state, fetchMock, go };
}
it("queries page two and combines server search with identity/status filters", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  await w
    .findAll("button")
    .find((b) => b.text() === "下一页")!
    .trigger("click");
  await flushPromises();
  expect(fetchMock.mock.calls.at(-1)![0]).toContain("offset=50");
  vi.useFakeTimers();
  await w.get('input[aria-label="搜索用户"]').setValue("outside%");
  await w.get('select[aria-label="筛选账号状态"]').setValue("0");
  await w.get('select[aria-label="筛选平台身份"]').setValue("false");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  const path = fetchMock.mock.calls.at(-1)![0];
  expect(path).toContain("search=outside%25");
  expect(path).toContain("offset=0");
  expect(path).toContain("status=0");
  expect(path).toContain("platform_admin=false");
});
it("edits global account fields even in a readonly tenant and clears reset secrets", async () => {
  const { w, fetchMock } = setup("usr_target");
  await flushPromises();
  await w.get("input:not([readonly])").setValue("修改名称");
  await w.get("form").trigger("submit");
  await flushPromises();
  const patch = fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(JSON.parse(patch[1]!.body as string)).toEqual({
    display_name: "修改名称",
    status: 1,
    platform_admin: false,
  });
  await w
    .findAll("button")
    .find((b) => b.text() === "重置密码")!
    .trigger("click");
  await w.get("input[type=password]").setValue("reset-password-2026");
  await w.findAll("form")[1].trigger("submit");
  await flushPromises();
  expect(
    fetchMock.mock.calls.some(
      ([p, o]) => p.endsWith("/password") && o?.method === "POST",
    ),
  ).toBe(true);
  expect(w.find("input[type=password]").exists()).toBe(false);
});
it("creates an account and opens immutable-code detail, and refreshes identity on self demotion", async () => {
  const created = setup("new");
  await flushPromises();
  await created.w.get("form").trigger("submit");
  await flushPromises();
  expect(created.go).toHaveBeenCalledWith("users/usr_target");
  const self = setup("usr_target");
  self.state.identity.value!.user_code = "usr_target";
  await flushPromises();
  await self.w.get("form").trigger("submit");
  await flushPromises();
  expect(self.state.identity.value!.platform_admin).toBe(false);
  expect(self.go).toHaveBeenCalledWith("overview");
});
it("does not request a platform directory for ordinary users", async () => {
  const { w, fetchMock } = setup(undefined, false);
  await flushPromises();
  expect(w.text()).toContain("需要平台管理员权限");
  expect(fetchMock).not.toHaveBeenCalled();
});

it("resolves an allowed tenant after self demotion from a tenant without membership", async () => {
  const { w, state, fetchMock, go } = setup("usr_target");
  state.identity.value!.user_code = "usr_target";
  state.data.value = { agents: [{ agent_code: "must-clear" }] };
  selectTenant("tenant_unjoined");
  await flushPromises();
  let calls = 0;
  fetchMock.mockImplementation(async (path: string) => {
    if (path.endsWith("/me")) {
      calls++;
      return calls === 1
        ? new Response(JSON.stringify({ detail: "无该租户访问权限" }), {
            status: 403,
          })
        : new Response(
            JSON.stringify({
              user_code: "usr_target",
              platform_admin: false,
              tenant_code: "",
            }),
          );
    }
    return new Response("{}");
  });
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(calls).toBe(2);
  expect(activeTenant.value).toBe("");
  expect(state.data.value).toEqual({});
  expect(go).toHaveBeenCalledWith("overview");
});
