import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import MembersWorkspace from "./MembersWorkspace.vue";
import { workspaceKey, type Workspace } from "../workspace";
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
function setup(code?: string) {
  let role = "MEMBER";
  const roles = [
    {
      role_code: "MEMBER",
      role_name: "成员",
      permissions: ["tasks.read"],
      assignable: true,
      status: 1,
    },
    {
      role_code: "rol_observer",
      role_name: "观察者",
      permissions: ["tasks.read"],
      assignable: true,
      status: 1,
    },
    {
      role_code: "TENANT_ADMIN",
      role_name: "租户管理员",
      permissions: ["members.manage"],
      assignable: false,
      blocked_reason: "不能授予",
      status: 1,
    },
  ];
  const row = (i: number) => ({
    member_code: "mem_" + i,
    user_code: "usr_target",
    username: "membertarget",
    display_name: "目标成员" + i,
    role_code: role,
    role_name: role === "MEMBER" ? "成员" : "观察者",
    status: 1,
    account_status: 1,
    role_status: 1,
    access_state: "ACTIVE",
    effective_permissions: ["tasks.read"],
    permission_source: "成员",
  });
  const fetchMock = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "POST") {
      role = JSON.parse(options.body as string).role_code;
      return new Response(JSON.stringify(row(0)));
    }
    if (path.endsWith("/roles"))
      return new Response(
        JSON.stringify({ roles, permissions: { "tasks.read": "查看任务" } }),
      );
    if (path.includes("/mem_")) return new Response(JSON.stringify(row(0)));
    return new Response(
      JSON.stringify(
        Array.from({ length: path.includes("offset=50") ? 2 : 50 }, (_, i) =>
          row(i),
        ),
      ),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  const go = vi.fn();
  const state: Workspace = {
    identity: ref({ user_code: "actor" }),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => p === "members.manage",
    go,
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(MembersWorkspace, {
    props: { code },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, fetchMock, state, go };
}
it("loads assignable roles without role editing permission and saves a detail assignment", async () => {
  const { w, fetchMock } = setup("mem_0");
  await flushPromises();
  expect(
    fetchMock.mock.calls.some(([p]) => p === "/api/v1/members/roles"),
  ).toBe(true);
  expect(fetchMock.mock.calls.some(([p]) => p === "/api/v1/roles")).toBe(false);
  expect(
    w.get('option[value="TENANT_ADMIN"]').attributes("disabled"),
  ).toBeDefined();
  await w.get('select[aria-label="分配角色"]').setValue("rol_observer");
  await w.get("form").trigger("submit");
  await flushPromises();
  const call = fetchMock.mock.calls.find(([, o]) => o?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string)).toMatchObject({
    user_code: "usr_target",
    role_code: "rol_observer",
  });
  expect(w.text()).toContain("查看任务");
});
it("paginates and resets server filters", async () => {
  const { w, fetchMock } = setup();
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
  await w.get('input[aria-label="搜索成员"]').setValue("目标");
  await w.get('select[aria-label="筛选成员状态"]').setValue("0");
  await vi.advanceTimersByTimeAsync(260);
  await flushPromises();
  const url = fetchMock.mock.calls
    .map(([p]) => p)
    .filter((p) => p.includes("members?"))
    .at(-1)!;
  expect(url).toContain("offset=0");
  expect(url).toContain("status=0");
});
it("keeps disabled tenants read-only", async () => {
  const { w, state } = setup("mem_0");
  state.readonly.value = true;
  await flushPromises();
  expect((w.get("fieldset").element as HTMLFieldSetElement).disabled).toBe(
    true,
  );
});
