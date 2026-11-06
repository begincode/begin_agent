import { afterEach, expect, it, vi } from "vitest";
import {
  forgetCreation,
  keepCreation,
  readCreation,
  startCreation,
} from "./agentCreation";
const fields = { agent_name: "  创建助手  ", agent_type: "NORMAL", status: 1 };
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});
it("stores only scoped request correlation and basic metadata", () => {
  const pending = startCreation("owner", "tenant", fields);
  expect(
    keepCreation({
      ...pending,
      config: {
        system_prompt: "never-store-prompt",
        api_key: "never-store-key",
      },
    } as any),
  ).toBe("");
  const raw = Object.values(sessionStorage).join("");
  expect(raw).not.toContain("never-store");
  expect(readCreation("owner", "tenant").pending).toEqual({
    ...pending,
    agent: { ...fields, agent_name: "创建助手" },
  });
});
it("separates actors and tenants and new creation intents", () => {
  const first = startCreation("owner", "tenant", fields);
  const next = startCreation("owner", "tenant", fields);
  expect(first.key).not.toBe(next.key);
  keepCreation(first);
  expect(readCreation("other", "tenant").pending).toBeNull();
  expect(readCreation("owner", "other").pending).toBeNull();
});
it("rejects invalid metadata before a pending request is minted", () => {
  expect(() => startCreation("", "tenant", fields)).toThrow("尚未就绪");
  expect(() =>
    startCreation("owner", "tenant", { ...fields, agent_name: "   " }),
  ).toThrow("有效");
  expect(() =>
    startCreation("owner", "tenant", { ...fields, agent_type: "INVALID" }),
  ).toThrow("有效");
  expect(sessionStorage.length).toBe(0);
});
it("reports corrupt or tampered records without reading another actor's data", () => {
  keepCreation(startCreation("owner", "tenant", fields));
  const key = sessionStorage.key(0)!;
  const value = JSON.parse(sessionStorage.getItem(key)!);
  sessionStorage.setItem(key, JSON.stringify({ ...value, owner: "other" }));
  expect(readCreation("owner", "tenant").pending).toBeNull();
  expect(readCreation("owner", "tenant").warning).toContain("不可用");
  sessionStorage.setItem(key, "x".repeat(4097));
  expect(readCreation("owner", "tenant").pending).toBeNull();
});
it("returns an explicit warning when the browser cannot persist recovery", () => {
  const value = startCreation("owner", "tenant", fields);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  expect(keepCreation(value)).toContain("当前页面");
  expect(value.key).toBeTruthy();
});
it("clears only the confirmed scoped request", () => {
  const first = startCreation("owner", "tenant", fields);
  keepCreation(first);
  keepCreation(startCreation("other", "tenant", fields));
  sessionStorage.setItem("agent.tenant", "tenant");
  forgetCreation(first);
  expect(readCreation("owner", "tenant").pending).toBeNull();
  expect(readCreation("other", "tenant").pending).not.toBeNull();
  expect(sessionStorage.getItem("agent.tenant")).toBe("tenant");
});
it("does not clear a newer request when an older completion arrives late", () => {
  const old = startCreation("owner", "tenant", fields);
  keepCreation(old);
  const latest = startCreation("owner", "tenant", fields);
  keepCreation(latest);
  forgetCreation(old);
  expect(readCreation("owner", "tenant").pending?.key).toBe(latest.key);
});
