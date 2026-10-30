import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import PlatformApp from "./PlatformApp.vue";
import { selectTenant } from "./api";
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const tenant = {
  tenant_code: "ten_a",
  tenant_name: "租户 A",
  status: 1,
  role: "MEMBER",
};
const me = {
  user_code: "usr_a",
  username: "member",
  platform_admin: false,
  tenant_code: "ten_a",
  role: "MEMBER",
  permissions: [
    "agents.read",
    "tasks.read",
    "tasks.create",
    "resources.read",
    "runs.read",
  ],
  tenants: [tenant],
};
const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  for (const w of wrappers.splice(0)) w.unmount();
  vi.unstubAllGlobals();
  selectTenant("");
  location.hash = "";
  localStorage.clear();
});
async function boot(
  overrides: Record<string, Response | (() => Response)> = {},
  hash = "overview",
) {
  location.hash = "/" + hash;
  const fetchMock = vi.fn(async (path: string) => {
    const custom = overrides[path];
    if (custom) return typeof custom === "function" ? custom() : custom.clone();
    if (path === "/api/v1/auth/config")
      return json({ mode: "password", setup_required: false });
    if (path === "/api/v1/me") return json(me);
    if (path === "/api/v1/overview")
      return json({
        agents: 0,
        tasks: 0,
        running: 0,
        succeeded: 0,
        recent_tasks: [],
      });
    return json([]);
  });
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(PlatformApp);
  wrappers.push(wrapper);
  await flushPromises();
  return { wrapper, fetchMock };
}
it("offers independent login after session expiry and hides business pages", async () => {
  const { wrapper } = await boot({
    "/api/v1/me": json({ detail: "登录已失效" }, 401),
  });
  expect(wrapper.text()).toContain("登录工作空间");
  expect(wrapper.find('input[autocomplete="username"]').exists()).toBe(true);
  expect(wrapper.find(".sidebar").exists()).toBe(false);
  expect(wrapper.text()).not.toContain("统一登录");
});
it("keeps database outages distinct from invalid sessions and offers retry", async () => {
  const { wrapper } = await boot({
    "/api/v1/me": json({ detail: "身份与租户数据库暂不可用" }, 503),
  });
  expect(wrapper.text()).toContain("工作台暂不可用");
  expect(wrapper.text()).toContain("重新加载");
});
it("shows first-admin setup without requesting protected business endpoints", async () => {
  const { wrapper, fetchMock } = await boot({
    "/api/v1/auth/config": json({ mode: "password", setup_required: true }),
  });
  expect(wrapper.text()).toContain("创建你的管理员账号");
  expect(wrapper.find('input[autocomplete="name"]').exists()).toBe(true);
  expect(fetchMock.mock.calls.some(([p]) => p === "/api/v1/me")).toBe(false);
});
it("submits setup and enters authenticated workspace without retaining password", async () => {
  let created = false;
  const { wrapper, fetchMock } = await boot({
    "/api/v1/auth/config": () =>
      json({ mode: "password", setup_required: !created }),
    "/api/v1/auth/setup": () => {
      created = true;
      return json({}, 201);
    },
  });
  await wrapper.get('input[autocomplete="name"]').setValue("管理员");
  await wrapper.get('input[autocomplete="username"]').setValue("admin");
  await wrapper.get('input[type="password"]').setValue("long-enough-password");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  const call = (
    fetchMock.mock.calls as unknown as [string, RequestInit][]
  ).find(([p]) => p === "/api/v1/auth/setup");
  expect(JSON.parse(call![1].body as string)).toMatchObject({
    username: "admin",
    display_name: "管理员",
  });
  expect(wrapper.find(".sidebar").exists()).toBe(true);
  expect(wrapper.find('input[type="password"]').exists()).toBe(false);
});
it("members see independent Agent pages and cannot see administration controls", async () => {
  const { wrapper } = await boot({}, "agents");
  expect(wrapper.find('a[href="#/members"]').exists()).toBe(false);
  expect(wrapper.find('a[href="#/users"]').exists()).toBe(false);
  expect(wrapper.text()).toContain("还没有Agent");
  expect(
    wrapper.findAll("button").some((b) => b.text().includes("新建Agent")),
  ).toBe(false);
});
it("disabled tenants retain views and hide creation and model calls", async () => {
  const { wrapper } = await boot({
    "/api/v1/me": json({
      ...me,
      platform_admin: true,
      tenants: [{ ...tenant, status: 0 }],
    }),
  });
  expect(wrapper.text()).toContain("租户已停用");
  expect(wrapper.findAll("button").some((b) => b.text() === "创建 Agent")).toBe(
    false,
  );
  location.hash = "/model-chat";
  window.dispatchEvent(new Event("hashchange"));
  await flushPromises();
  expect(wrapper.text()).toContain("模型调用已停止");
  expect(wrapper.find("textarea").exists()).toBe(false);
});
it("switches tenant before fetching its catalog and clears previous route", async () => {
  let selected = "ten_a";
  const second = { ...tenant, tenant_code: "ten_b", tenant_name: "租户 B" };
  const { wrapper, fetchMock } = await boot(
    {
      "/api/v1/me": () =>
        json({ ...me, tenant_code: selected, tenants: [tenant, second] }),
    },
    "agents",
  );
  selected = "ten_b";
  await wrapper.get("#tenant-switch").setValue("ten_b");
  await flushPromises();
  const call = (fetchMock.mock.calls as unknown as [string, RequestInit][])
    .filter(([p]) => p === "/api/v1/agents")
    .at(-1);
  expect(new Headers(call?.[1].headers).get("X-Tenant-Code")).toBe("ten_b");
  expect(location.hash).toBe("#/overview");
});
it("persists sidebar collapse preference", async () => {
  const { wrapper } = await boot();
  await wrapper.get('button[aria-label="收起导航"]').trigger("click");
  expect(wrapper.get(".platform-shell").classes()).toContain("collapsed");
  expect(localStorage.getItem("agent.sidebar")).toBe("collapsed");
});
it("remembers folded navigation groups and reveals the destination group on navigation", async () => {
  localStorage.setItem("agent.nav-groups", "invalid-json");
  const first = await boot();
  await first.wrapper.get('button[aria-label="收起能力资源"]').trigger("click");
  expect(JSON.parse(localStorage.getItem("agent.nav-groups")!)).toContain(
    "能力资源",
  );
  first.wrapper.unmount();
  wrappers.splice(wrappers.indexOf(first.wrapper), 1);
  const second = await boot();
  expect(
    second.wrapper
      .get('button[aria-label="展开能力资源"]')
      .attributes("aria-expanded"),
  ).toBe("false");
  location.hash = "/models";
  window.dispatchEvent(new Event("hashchange"));
  await flushPromises();
  expect(
    second.wrapper
      .get('button[aria-label="收起能力资源"]')
      .attributes("aria-expanded"),
  ).toBe("true");
  expect(JSON.parse(localStorage.getItem("agent.nav-groups")!)).not.toContain(
    "能力资源",
  );
});
it("uses internal logout and clears tenant context", async () => {
  const { wrapper, fetchMock } = await boot({}, "account");
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "退出登录")!
    .trigger("click");
  await flushPromises();
  expect(fetchMock.mock.calls.some(([p]) => p === "/api/v1/auth/logout")).toBe(
    true,
  );
  expect(wrapper.find(".sidebar").exists()).toBe(false);
  expect(sessionStorage.getItem("agent.tenant")).toBeNull();
});
it("honors fine-grained custom-role permissions in navigation", async () => {
  const { wrapper } = await boot({
    "/api/v1/me": json({
      ...me,
      role: "rol_custom",
      permissions: ["agents.read"],
    }),
  });
  expect(wrapper.find('a[href="#/tasks"]').exists()).toBe(false);
  expect(wrapper.find('a[href="#/models"]').exists()).toBe(false);
  expect(wrapper.find('a[href="#/agents"]').exists()).toBe(true);
});

it("does not request a transient login page as business data after authenticating", async () => {
  let loggedIn = false;
  const { wrapper, fetchMock } = await boot(
    {
      "/api/v1/me": () =>
        loggedIn ? json(me) : json({ detail: "请登录账号" }, 401),
      "/api/v1/auth/login": () => {
        loggedIn = true;
        return json({});
      },
    },
    "login",
  );
  await wrapper.get('input[autocomplete="username"]').setValue("member");
  await wrapper.get('input[type="password"]').setValue("test-password-2026");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.find(".sidebar").exists()).toBe(true);
  expect(fetchMock.mock.calls.some(([path]) => path === "/api/v1/login")).toBe(
    false,
  );
  expect(
    fetchMock.mock.calls.some(([path]) => path === "/api/v1/overview"),
  ).toBe(true);
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
});
it("loads the authorization catalog past its first page", async () => {
  const resources = Array.from({ length: 61 }, (_, i) => ({
    resource_code: "tol_" + i,
    resource_kind: "TOOL",
    available: true,
  }));
  const { fetchMock } = await boot({
    "/api/v1/resources": json(resources.slice(0, 50)),
    "/api/v1/resources?limit=100&offset=50": json(resources.slice(50)),
  });
  expect(
    fetchMock.mock.calls.some(
      ([p]) => p === "/api/v1/resources?limit=100&offset=50",
    ),
  ).toBe(true);
});
it("loads Agent candidates past the first page for collaboration", async () => {
  const agents = Array.from({ length: 61 }, (_, i) => ({
    agent_code: "agt_" + i,
    agent_name: "协作助手" + i,
    status: 1,
    current_version_code: "v_" + i,
  }));
  const { fetchMock } = await boot({
    "/api/v1/agents": json(agents.slice(0, 50)),
    "/api/v1/agents?limit=100&offset=50": json(agents.slice(50)),
  });
  expect(
    fetchMock.mock.calls.some(
      ([p]) => p === "/api/v1/agents?limit=100&offset=50",
    ),
  ).toBe(true);
});
