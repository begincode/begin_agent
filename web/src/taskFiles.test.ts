import { expect, it } from "vitest";
import { readInputFiles } from "./taskFiles";
const fake = (name: string, raw: Uint8Array) =>
  ({ name, size: raw.length, arrayBuffer: async () => raw.buffer }) as File;
it("encodes UTF-8 files for a bounded request", async () => {
  const raw = new TextEncoder().encode("参考内容");
  const values = await readInputFiles([fake("参考.md", raw)]);
  expect(atob(values[0].content_base64).length).toBe(raw.length);
});
it("rejects unsupported, oversized and excessive files", async () => {
  await expect(
    readInputFiles([fake("a.exe", new Uint8Array([1]))]),
  ).rejects.toThrow();
  await expect(
    readInputFiles([fake("a.txt", new Uint8Array(32769))]),
  ).rejects.toThrow();
  await expect(
    readInputFiles(Array(4).fill(fake("a.txt", new Uint8Array([1])))),
  ).rejects.toThrow();
});
it("rejects malformed UTF-8", async () => {
  await expect(
    readInputFiles([fake("a.txt", new Uint8Array([255]))]),
  ).rejects.toThrow();
});
