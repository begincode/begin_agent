import { afterEach, expect, it, vi } from "vitest";
import { storeWizard, readWizard, clearWizard } from "./wizardDraft";
afterEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});
const agent = { agent_name: "恢复名称", agent_type: "NORMAL", status: 1 };
const config = {
  profile_code: "mdl_one",
  system_prompt: "完整指令",
  tool_codes: ["tool_one"],
  skill_codes: [],
  mcp_codes: [],
  mcp_tool_names: {},
  execution_config: { max_steps: 9 },
  model_parameters: { thinking: "auto" },
};
it("recovers complete allowlisted fields and wizard step", () => {
  expect(storeWizard("u", "t", agent, config, 2)).toBe("");
  expect(readWizard("u", "t").draft).toMatchObject({ agent, config, step: 2 });
});
it("isolates user and tenant and removes confirmed drafts", () => {
  storeWizard("u", "t", agent, config, 3);
  expect(readWizard("other", "t").draft).toBeNull();
  expect(readWizard("u", "other").draft).toBeNull();
  clearWizard("u", "t");
  expect(readWizard("u", "t").draft).toBeNull();
});
it("never stores credential or unknown configuration properties", () => {
  storeWizard(
    "u",
    "t",
    agent,
    {
      ...config,
      api_key: "NEVER_SAVE",
      model_parameters: { ...config.model_parameters, api_key: "NEVER_SAVE" },
    },
    1,
  );
  expect(JSON.stringify(Object.entries(sessionStorage))).not.toContain(
    "NEVER_SAVE",
  );
});
it("rejects expired, malformed or oversized records", () => {
  storeWizard("u", "t", agent, config, 1);
  const k = Object.keys(sessionStorage)[0];
  const v = JSON.parse(sessionStorage.getItem(k)!);
  v.savedAt -= 86400001;
  sessionStorage.setItem(k, JSON.stringify(v));
  expect(readWizard("u", "t").warning).not.toBe("");
  sessionStorage.setItem(k, "{");
  expect(readWizard("u", "t").draft).toBeNull();
  expect(
    storeWizard(
      "u",
      "t",
      agent,
      { ...config, system_prompt: "x".repeat(131073) },
      1,
    ),
  ).not.toBe("");
});
it("reports blocked storage without claiming persistence", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw Error("quota");
  });
  expect(storeWizard("u", "t", agent, config, 1)).toContain("保存失败");
});
