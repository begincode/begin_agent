export type InputFile = { file_name: string; content_base64: string };
export async function readInputFiles(files: File[]): Promise<InputFile[]> {
  if (files.length > 3) throw Error("最多选择 3 个参考附件");
  return Promise.all(
    files.map(async (file) => {
      if (
        !/\.(txt|md|csv|json)$/i.test(file.name) ||
        !file.size ||
        file.size > 32768
      )
        throw Error("请选择不超过 32KiB 的 txt、md、csv、json 文件");
      const bytes = new Uint8Array(await file.arrayBuffer());
      new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      return {
        file_name: file.name,
        content_base64: btoa(String.fromCharCode(...bytes)),
      };
    }),
  );
}
