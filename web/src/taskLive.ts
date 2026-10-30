import { apiFetch } from "./api";
import type { Row } from "./workspace";
export class LiveAccessError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function readTaskEvents(
  path: string,
  signal: AbortSignal,
  snapshot: (value: Row) => void | Promise<void>,
  connected: () => void,
): Promise<"complete" | "reconnect"> {
  const response = await apiFetch(path, {
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    if ([401, 403, 404].includes(response.status))
      throw new LiveAccessError(
        response.status,
        data?.detail || "实时输出无访问权限",
      );
    throw new Error("实时输出连接暂不可用");
  }
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  )
    throw new Error("实时输出协议暂不可用");
  connected();
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  const cancel = () => {
    void reader.cancel().catch(() => {});
  };
  signal.addEventListener("abort", cancel, { once: true });
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = "message";
        const lines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:"))
            lines.push(line.slice(5).replace(/^ /, ""));
        }
        if (!lines.length) continue;
        const data = JSON.parse(lines.join("\n"));
        if (event === "snapshot") await snapshot(data);
        else if (event === "complete" || event === "reconnect") return event;
        else if (event === "access_error")
          throw new LiveAccessError(data.status, data.message);
        else if (event === "unavailable") throw new Error(data.message);
      }
      if (buffer.length > 2_000_000) throw new Error("实时输出帧超出限制");
      if (done) throw new Error("实时输出连接中断");
    }
    throw new DOMException("Cancelled", "AbortError");
  } finally {
    signal.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
