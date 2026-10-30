import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import RolesWorkspace from "./RolesWorkspace.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
function setup(allowed = true) {
  const role = {
    role_code: "rol_target",
    role_name: "观察者",
    permissions: ["tasks.read"],
    status: 1,
    member_count: 3,
    active_member_count: 1,
    editable: true,
  };
  const go = vi.fn();
  const state: Workspace = {
    identity: ref({
      user_code: "usr_actor",
      role: "CUSTOM",
      platform_admin: false,
      permissions: ["roles.manage"],
    }),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) =>
      allowed &&
      (p !== "roles.manage" ||
        !!state.identity.value?.permissions.includes("roles.manage")),
    go,
    load: vi.fn(async () => {}),
    act: async (fn) => {
      await fn();
    },
  };
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method) return new Response(JSON.stringify(role));
    if (path.endsWith("/me"))
      return new Response(
        JSON.stringify({
          role: "rol_target",
          platform_admin: false,
          permissions: ["tasks.read"],
        }),
      );
    return new Response(
      JSON.stringify({
        permissions: { "tasks.read": "查看任务", "agents.read": "查看 Agent" },
        roles: [
          role,
          {
            ...role,
            role_code: "TENANT_ADMIN",
            role_name: "租户管理员",
            editable: false,
            blocked_reason: "内置角色只读",
          },
        ],
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const w = mount(RolesWorkspace, {
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, fetchMock, state, go };
}
it("renders permission matrix and actual member counts while keeping builtins readonly", async () => {
  const { w } = setup();
  await flushPromises();
  expect(w.get("table").text()).toContain("启用账号成员 1");
  expect(w.text()).toContain("内置角色只读");
  expect(
    w.findAll("button").filter((b) => b.text() === "编辑角色"),
  ).toHaveLength(1);
  expect(w.find('[aria-label="查看任务：已授予"]').exists()).toBe(true);
});
it("sends combined search and status to the server and saves selected permissions", async () => {
  const { w, fetchMock } = setup();
  await flushPromises();
  vi.useFakeTimers();
  await w.get('input[aria-label="搜索角色"]').setValue("outside%");
  await w.get('select[aria-label="筛选角色状态"]').setValue("0");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  expect(fetchMock.mock.calls.at(-1)![0]).toContain("search=outside%25");
  expect(fetchMock.mock.calls.at(-1)![0]).toContain("status=0");
  await w
    .findAll("button")
    .find((b) => b.text() === "编辑角色")!
    .trigger("click");
  await w.get('input[value="agents.read"]').setValue(true);
  await w.get("form").trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "PATCH")!;
  expect(JSON.parse(call[1]!.body as string).permissions).toEqual([
    "tasks.read",
    "agents.read",
  ]);
});
it("recomputes identity and exits after removing own role management permission", async () => {
  const { w, state, go } = setup();
  state.identity.value!.role = "rol_target";
  await flushPromises();
  await w
    .findAll("button")
    .find((b) => b.text() === "编辑角色")!
    .trigger("click");
  expect(
    w.get("select:not([aria-label])").attributes("disabled"),
  ).toBeDefined();
  await w.get("form").trigger("submit");
  await flushPromises();
  expect(go).toHaveBeenCalledWith("overview");
  expect(state.identity.value!.permissions).toEqual(["tasks.read"]);
});
it("does not read roles without permission and disables writes in readonly tenants", async () => {
  const denied = setup(false);
  await flushPromises();
  expect(denied.fetchMock).not.toHaveBeenCalled();
  const readonly = setup();
  readonly.state.readonly.value = true;
  await flushPromises();
  expect(
    readonly.w
      .findAll("button")
      .find((b) => b.text() === "＋ 创建角色")!
      .attributes("disabled"),
  ).toBeDefined();
});
