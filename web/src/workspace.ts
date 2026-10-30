import { inject, type InjectionKey, type Ref } from "vue";
import { post } from "./api";
export interface Row {
  [key: string]: any;
}
export interface Workspace {
  identity: Ref<Row | null>;
  data: Ref<Record<string, Row[]>>;
  busy: Ref<boolean>;
  readonly: Ref<boolean>;
  can: (permission: string) => boolean;
  go: (path: string) => void;
  load: () => Promise<void>;
  act: (
    fn: () => Promise<void>,
    message?: string,
    initialize?: boolean,
  ) => Promise<void>;
}
export const workspaceKey: InjectionKey<Workspace> = Symbol("workspace");
export function useWorkspace() {
  const value = inject(workspaceKey);
  if (!value) throw new Error("Workspace unavailable");
  return value;
}
export const labels: Record<string, string> = {
  PENDING: "等待执行",
  RUNNING: "执行中",
  SUCCEEDED: "已完成",
  FAILED: "执行失败",
  TIMEOUT: "超时",
  CANCELLED: "已取消",
  NORMAL: "通用 Agent",
  SUPERVISOR: "主 Agent",
  ROUTER: "路由 Agent",
  PUBLISHED: "已发布",
  DEBUG: "调试快照",
};
export const label = (value: string) => labels[value] ?? value;
export const date = (value: string) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
export const duration = (milliseconds: number | null | undefined) =>
  milliseconds == null
    ? "—"
    : milliseconds < 1000
      ? `${milliseconds} 毫秒`
      : `${(milliseconds / 1000).toFixed(1)} 秒`;
export const keyOf = (kind: string) =>
  (
    ({
      agents: "agent_code",
      tasks: "task_code",
      runs: "run_code",
      models: "profile_code",
      tools: "tool_code",
      mcps: "mcp_code",
      skills: "skill_code",
      users: "user_code",
      roles: "role_code",
      members: "member_code",
    }) as Record<string, string>
  )[kind] ?? "event_code";
export const titleOf = (kind: string, row: Row) =>
  row[
    (
      {
        agents: "agent_name",
        tasks: "title",
        runs: "run_code",
        models: "display_name",
        tools: "tool_name",
        mcps: "mcp_name",
        skills: "skill_name",
        users: "display_name",
        members: "display_name",
        roles: "role_name",
      } as Record<string, string>
    )[kind] ?? "action"
  ] ?? "—";
export const endpoint = (kind: string) =>
  "/api/v1/" + (kind === "models" ? "model-profiles" : kind);
export async function mutate(kind: string, code: string, data: unknown) {
  return post<Row>(
    endpoint(kind) + (code ? "/" + code : ""),
    data,
    code ? "PATCH" : "POST",
  );
}
