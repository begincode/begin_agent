export type CreationFields = {
  agent_name: string;
  agent_type: string;
  status: number;
};
export type PendingCreation = {
  version: 1;
  owner: string;
  tenant: string;
  key: string;
  startedAt: number;
  agent: CreationFields;
};
const prefix = "agent.creation.v1.";
const storageKey = (owner: string, tenant: string) =>
  prefix + JSON.stringify([owner, tenant]);
export function creationFields(value: CreationFields): CreationFields {
  return {
    agent_name: value.agent_name.trim(),
    agent_type: value.agent_type,
    status: value.status,
  };
}
export function startCreation(
  owner: string,
  tenant: string,
  agent: CreationFields,
): PendingCreation {
  if (!owner || !tenant)
    throw new Error("账号或工作空间尚未就绪，请刷新后重试");
  const fields = creationFields(agent);
  if (
    !fields.agent_name ||
    fields.agent_name.length > 128 ||
    !["NORMAL", "SUPERVISOR", "ROUTER"].includes(fields.agent_type) ||
    ![0, 1].includes(fields.status)
  )
    throw new Error("请填写有效的 Agent 名称、类型和状态");
  return {
    version: 1,
    owner,
    tenant,
    key: crypto.randomUUID(),
    startedAt: Date.now(),
    agent: fields,
  };
}
export function keepCreation(value: PendingCreation) {
  try {
    // Persist only request correlation and basic metadata. Never persist the full editor state.
    sessionStorage.setItem(
      storageKey(value.owner, value.tenant),
      JSON.stringify({
        version: 1,
        owner: value.owner,
        tenant: value.tenant,
        key: value.key,
        startedAt: value.startedAt,
        agent: creationFields(value.agent),
      }),
    );
    return "";
  } catch {
    return "浏览器无法保留恢复记录，请在当前页面确认结果后再离开。";
  }
}
export function readCreation(
  owner: string,
  tenant: string,
): { pending: PendingCreation | null; warning: string } {
  try {
    const raw = sessionStorage.getItem(storageKey(owner, tenant));
    if (!raw) return { pending: null, warning: "" };
    if (raw.length > 4096) throw new Error();
    const value = JSON.parse(raw);
    if (
      value.version !== 1 ||
      value.owner !== owner ||
      value.tenant !== tenant ||
      typeof value.key !== "string" ||
      !/^[A-Za-z0-9_.-]{16,128}$/.test(value.key) ||
      !Number.isFinite(value.startedAt) ||
      typeof value.agent?.agent_name !== "string" ||
      !value.agent.agent_name.trim() ||
      value.agent.agent_name.length > 128 ||
      !["NORMAL", "SUPERVISOR", "ROUTER"].includes(value.agent.agent_type) ||
      ![0, 1].includes(value.agent.status)
    )
      throw new Error();
    return {
      pending: {
        version: 1,
        owner,
        tenant,
        key: value.key,
        startedAt: value.startedAt,
        agent: creationFields(value.agent),
      },
      warning: "",
    };
  } catch {
    return {
      pending: null,
      warning: "恢复记录不可用，请先到 Agent 列表核对此前是否已创建。",
    };
  }
}
export function forgetCreation(value: PendingCreation) {
  try {
    const key = storageKey(value.owner, value.tenant);
    const current = sessionStorage.getItem(key);
    if (current && JSON.parse(current).key === value.key)
      sessionStorage.removeItem(key);
  } catch {
    /* In-memory state still clears. */
  }
}
