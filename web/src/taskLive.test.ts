import { afterEach, expect, it, vi } from "vitest";
import { readTaskEvents, LiveAccessError } from "./taskLive";
import { activeTenant, selectTenant } from "./api";
afterEach(() => {
  vi.unstubAllGlobals();
  selectTenant("");
});
function stream(chunks: Uint8Array[]) {
  return new Response(
    new ReadableStream({
      start(c) {
        for (const chunk of chunks) c.enqueue(chunk);
        c.close();
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}
it("decodes UTF8 and split CRLF frames, preserving cumulative snapshots and tenant header", async () => {
  const wire = new TextEncoder().encode(
    'event: snapshot\r\ndata: {"preview":{"content":"中"},"revision":"a"}\r\n\r\nevent: snapshot\r\ndata: {"preview":{"content":"中文"},"revision":"b"}\r\n\r\nevent: complete\r\ndata: {}\r\n\r\n',
  );
  const fetchMock = vi.fn(async () =>
    stream(Array.from(wire, (byte) => new Uint8Array([byte]))),
  );
  vi.stubGlobal("fetch", fetchMock);
  selectTenant("ten_selected");
  const values: string[] = [];
  const result = await readTaskEvents(
    "/api/v1/tasks/task/stream",
    new AbortController().signal,
    (v) => {
      values.push(v.preview.content);
    },
    () => {},
  );
  expect(values).toEqual(["中", "中文"]);
  expect(result).toBe("complete");
  expect(
    new Headers((fetchMock.mock.calls[0] as any)[1].headers).get(
      "X-Tenant-Code",
    ),
  ).toBe(activeTenant.value);
});
it("rejects incomplete streams and explicit access errors without treating them as completion", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      stream([
        new TextEncoder().encode('event: snapshot\ndata: {"revision":"a"}\n\n'),
      ]),
    ),
  );
  await expect(
    readTaskEvents(
      "/stream",
      new AbortController().signal,
      () => {},
      () => {},
    ),
  ).rejects.toThrow("连接中断");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      stream([
        new TextEncoder().encode(
          'event: access_error\ndata: {"status":403,"message":"revoked"}\n\n',
        ),
      ]),
    ),
  );
  await expect(
    readTaskEvents(
      "/stream",
      new AbortController().signal,
      () => {},
      () => {},
    ),
  ).rejects.toBeInstanceOf(LiveAccessError);
});
it("cancels a blocked read when the task page is left", async () => {
  const cancel = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(new ReadableStream({ cancel }), {
          headers: { "Content-Type": "text/event-stream" },
        }),
    ),
  );
  const controller = new AbortController();
  const reading = readTaskEvents(
    "/stream",
    controller.signal,
    () => {},
    () => {},
  );
  await Promise.resolve();
  await Promise.resolve();
  controller.abort();
  await expect(reading).rejects.toThrow();
  expect(cancel).toHaveBeenCalled();
});
