import { creationFields, type CreationFields } from "./agentCreation";
export type WizardDraft = {
  version: 1;
  owner: string;
  tenant: string;
  savedAt: number;
  agent: CreationFields;
  config: Record<string, any>;
  step: number;
};
const prefix = "agent.wizard.v1.";
const key = (owner: string, tenant: string) =>
  prefix + JSON.stringify([owner, tenant]);
const allowed = [
  "profile_code",
  "system_prompt",
  "tool_codes",
  "skill_codes",
  "mcp_codes",
  "mcp_tool_names",
  "execution_config",
  "model_parameters",
];
const limits: Record<string, string[]> = {
  execution_config: [
    "max_steps",
    "timeout_seconds",
    "max_sub_agent_calls",
    "max_depth",
    "max_tokens",
  ],
  model_parameters: [
    "thinking",
    "reasoning_effort",
    "max_tokens",
    "temperature",
    "top_p",
  ],
};
function clean(config: Record<string, any>) {
  const result: Record<string, any> = {};
  for (const name of allowed) {
    const value = config[name];
    if (value === undefined) continue;
    if (limits[name])
      result[name] = Object.fromEntries(
        limits[name]
          .filter((k) => value?.[k] !== undefined)
          .map((k) => [k, value[k]]),
      );
    else result[name] = value;
  }
  return JSON.parse(JSON.stringify(result));
}
function valid(v: any, owner: string, tenant: string): v is WizardDraft {
  return (
    v?.version === 1 &&
    v.owner === owner &&
    v.tenant === tenant &&
    Number.isFinite(v.savedAt) &&
    v.savedAt <= Date.now() + 60000 &&
    Date.now() - v.savedAt < 86400000 &&
    typeof v.agent?.agent_name === "string" &&
    v.agent.agent_name.length <= 128 &&
    ["NORMAL", "SUPERVISOR", "ROUTER"].includes(v.agent.agent_type) &&
    [0, 1].includes(v.agent.status) &&
    [1, 2, 3].includes(v.step) &&
    typeof v.config?.system_prompt === "string" &&
    typeof v.config.profile_code === "string" &&
    ["tool_codes", "skill_codes", "mcp_codes"].every(
      (k) =>
        Array.isArray(v.config[k]) &&
        v.config[k].length <= 1000 &&
        v.config[k].every((x: any) => typeof x === "string"),
    ) &&
    v.config.execution_config &&
    typeof v.config.execution_config === "object" &&
    v.config.model_parameters &&
    typeof v.config.model_parameters === "object"
  );
}
export function storeWizard(
  owner: string,
  tenant: string,
  agent: CreationFields,
  config: Record<string, any>,
  step: number,
) {
  try {
    const value = {
      version: 1,
      owner,
      tenant,
      savedAt: Date.now(),
      agent: creationFields(agent),
      config: clean(config),
      step,
    };
    const raw = JSON.stringify(value);
    if (raw.length > 131072) throw new Error();
    sessionStorage.setItem(key(owner, tenant), raw);
    return "";
  } catch {
    return "本标签页草稿保存失败，请勿刷新，先保存到系统。";
  }
}
export function readWizard(
  owner: string,
  tenant: string,
): { draft: WizardDraft | null; warning: string } {
  try {
    const raw = sessionStorage.getItem(key(owner, tenant));
    if (!raw) return { draft: null, warning: "" };
    if (raw.length > 131072) throw new Error();
    const v = JSON.parse(raw);
    if (!valid(v, owner, tenant)) throw new Error();
    return { draft: { ...v, config: clean(v.config) }, warning: "" };
  } catch {
    return {
      draft: null,
      warning: "本标签页草稿已过期或不可用，请核对已保存内容。",
    };
  }
}
export function clearWizard(owner: string, tenant: string) {
  try {
    sessionStorage.removeItem(key(owner, tenant));
  } catch {
    /* no persistent success claim */
  }
}
