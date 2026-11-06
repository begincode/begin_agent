import { afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";
import ResourceEditor from "./ResourceEditor.vue";
import { workspaceKey, type Workspace } from "../workspace";
afterEach(() => vi.unstubAllGlobals());
it("loads a tool detail directly even when it is absent from the current catalog page", async () => {
  const fetch = vi.fn(
    async (_path: string) =>
      new Response(
        JSON.stringify({
          tool_code: "tol_older",
          tool_name: "列表之外",
          group_name: "历史组",
          status: 0,
          connection_config: { implementation: "utc_now" },
        }),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetch);
  const state: Workspace = {
    identity: ref(null),
    data: ref({ tools: [] }),
    busy: ref(false),
    readonly: ref(false),
    can: () => true,
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(ResourceEditor, {
    props: { kind: "tools", code: "tol_older" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  await flushPromises();
  expect(fetch.mock.calls[0]?.[0]).toBe("/api/v1/tools/tol_older");
  expect((w.get("input").element as HTMLInputElement).value).toBe("列表之外");
  expect(w.findAll("select").at(-1)!.element.value).toBe("0");
  w.unmount();
});

it("reloads the persisted MCP failure after a check error and clears it after recovery", async () => {
  let failed = false;
  let recover = false;
  const fetch = vi.fn(async (path: string, options?: RequestInit) => {
    if (path.endsWith("/check") && options?.method === "POST") {
      if (!recover) {
        failed = true;
        return new Response(JSON.stringify({ detail: "MCP 鉴权失败" }), {
          status: 502,
        });
      }
      failed = false;
      return new Response(JSON.stringify({}), { status: 200 });
    }
    return new Response(
      JSON.stringify({
        mcp_code: "mcp_one",
        mcp_name: "真实连接",
        endpoint: "https://safe.test/mcp",
        status: 1,
        tools: [],
        checked_at: failed ? null : "2026-11-06T10:00:00Z",
        last_error: failed ? "MCP_AUTH_FAILED" : null,
        has_token: true,
      }),
      { status: 200 },
    );
  });
  vi.stubGlobal("fetch", fetch);
  const load = vi.fn(async () => {});
  const state: Workspace = {
    identity: ref(null),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: () => true,
    go: () => {},
    load,
    act: async (fn) => {
      try {
        await fn();
      } catch {
        /* The parent renders the action error. */
      }
    },
  };
  const w = mount(ResourceEditor, {
    props: { kind: "mcps", code: "mcp_one" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  await flushPromises();
  expect(w.text()).toContain("连接与工具发现已通过");
  await w
    .findAll("button")
    .find((b) => b.text() === "检测连接")!
    .trigger("click");
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("鉴权失败");
  expect(w.text()).not.toContain("最近检测：");
  expect(w.text()).not.toContain("连接与工具发现已通过");
  expect(load).toHaveBeenCalled();
  recover = true;
  await w
    .findAll("button")
    .find((b) => b.text() === "检测连接")!
    .trigger("click");
  await flushPromises();
  expect(w.find('[role="alert"]').exists()).toBe(false);
  expect(w.text()).toContain("不代表模型已实际调用这些工具");
  expect(
    (w.get('input[type="password"]').element as HTMLInputElement).value,
  ).toBe("");
  w.unmount();
});

it("keeps the failed MCP status visible after a fresh page mount", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            mcp_code: "mcp_one",
            mcp_name: "连接",
            endpoint: "https://safe.test/mcp",
            status: 1,
            tools: [],
            checked_at: null,
            last_error: "MCP_TIMEOUT",
          }),
          { status: 200 },
        ),
    ),
  );
  const state: Workspace = {
    identity: ref(null),
    data: ref({}),
    busy: ref(false),
    readonly: ref(true),
    can: () => false,
    go: () => {},
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(ResourceEditor, {
    props: { kind: "mcps", code: "mcp_one" },
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain("请求超时");
  expect(w.findAll("button").some((b) => b.text() === "检测连接")).toBe(false);
  w.unmount();
});
