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
