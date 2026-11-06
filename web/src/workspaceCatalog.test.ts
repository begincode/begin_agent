import { expect, it, vi } from "vitest";
import { catalogKinds, readCatalog } from "./workspaceCatalog";
it("does not preload catalogs for overview, ordinary lists, accounts or task details", () => {
  for (const path of [
    "overview",
    "agents",
    "tasks",
    "tasks/tsk_one",
    "account",
    "models",
    "users",
    "mcps/mcp_one",
    "runs/run_one",
  ])
    expect(catalogKinds(path, () => true)).toEqual([]);
  expect(catalogKinds("tasks/new", () => true)).toEqual(["agents"]);
  expect(catalogKinds("agents/agt_one/collaboration", () => true)).toEqual([
    "agents",
  ]);
  expect(catalogKinds("agents/new", () => true)).toEqual([
    "models",
    "tools",
    "mcps",
    "skills",
    "resources",
  ]);
  expect(catalogKinds("agents/new", (p) => p === "agents.read")).toEqual([]);
});
it("keeps later-page candidates while refusing a stalled pagination response", async () => {
  const first = Array.from({ length: 50 }, (_, i) => ({
    agent_code: `agt_${i}`,
  }));
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(first)
    .mockResolvedValueOnce([{ agent_code: "agt_last" }]);
  const rows = await readCatalog("agents", fetch, new AbortController().signal);
  expect(rows.at(-1)?.agent_code).toBe("agt_last");
  expect(fetch).toHaveBeenLastCalledWith("/api/v1/agents?limit=100&offset=50");
  await expect(
    readCatalog(
      "agents",
      vi.fn().mockResolvedValue(first),
      new AbortController().signal,
    ),
  ).rejects.toThrow("分页未前进");
});
it("does not request another page after the route signal was aborted", async () => {
  const controller = new AbortController();
  const fetch = vi.fn(async () => {
    controller.abort();
    return Array.from({ length: 50 }, (_, i) => ({ tool_code: `tol_${i}` }));
  });
  await readCatalog("tools", fetch, controller.signal);
  expect(fetch).toHaveBeenCalledTimes(1);
});
