import { ref } from "vue";

export const activeTenant = ref(sessionStorage.getItem("agent.tenant") ?? "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export function selectTenant(code: string) {
  activeTenant.value = code;
  if (code) sessionStorage.setItem("agent.tenant", code);
  else sessionStorage.removeItem("agent.tenant");
}

export function appPath(path: string) {
  const base = new URL(".", document.baseURI).pathname.replace(/\/$/, "");
  return base + path;
}

export async function apiFetch(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  if (activeTenant.value) headers.set("X-Tenant-Code", activeTenant.value);
  const response = await fetch(appPath(path), { ...options, headers });
  if (response.status === 401)
    window.dispatchEvent(new Event("agent-auth-expired"));
  return response;
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(path, options);
  return parseResponse<T>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  const data =
    response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      typeof data?.detail === "string"
        ? data.detail
        : `请求失败（${response.status}）`,
      response.status,
    );
  return data as T;
}

export async function versioned<T>(path: string, options: RequestInit = {}) {
  const response = await apiFetch(path, options);
  return {
    data: await parseResponse<T>(response),
    etag: response.headers.get("ETag") || "",
  };
}

export function post<T = unknown>(
  path: string,
  data: unknown,
  method = "POST",
  options: RequestInit = {},
) {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  return api<T>(path, {
    ...options,
    method,
    headers,
    body: JSON.stringify(data),
  });
}
