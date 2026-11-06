import type { Row } from "./workspace";
const permission: Record<string, string> = {
  agents: "agents.read",
  tasks: "tasks.read",
  models: "resources.read",
  tools: "resources.read",
  mcps: "resources.read",
  skills: "resources.read",
  resources: "resources.read",
};
export function catalogKinds(
  path: string,
  can: (permission: string) => boolean,
) {
  const [section, code, tab = "config"] = path.split("/");
  let kinds: string[] = [];
  if (section === "tasks" && code === "new") kinds = ["agents"];
  if (section === "runs" && !code) kinds = ["agents"];
  if (section === "agents" && code) {
    if (code === "new" || tab === "config")
      kinds = ["models", "tools", "mcps", "skills", "resources"];
    if (tab === "collaboration") kinds = ["agents"];
  }
  return kinds.filter((kind) => can(permission[kind]));
}
export async function readCatalog(
  kind: string,
  fetchPage: (url: string) => Promise<Row[]>,
  signal: AbortSignal,
) {
  const url = "/api/v1/" + (kind === "models" ? "model-profiles" : kind);
  const first = await fetchPage(url);
  if (
    !["tools", "mcps", "skills", "resources", "agents"].includes(kind) ||
    first.length < 50
  )
    return first;
  const key = (r: Row) =>
    r.tool_code ||
    r.mcp_code ||
    r.skill_code ||
    r.resource_code ||
    r.agent_code;
  const all = [...first],
    seen = new Set(first.map(key));
  let offset = first.length;
  while (!signal.aborted) {
    const page = await fetchPage(url + `?limit=100&offset=${offset}`);
    if (!page.length) break;
    const fresh = page.filter((r) => !seen.has(key(r)));
    if (!fresh.length) throw new Error("资源分页未前进，请重新加载");
    for (const row of fresh) {
      all.push(row);
      seen.add(key(row));
    }
    offset += page.length;
    if (page.length < 100) break;
  }
  return all;
}
