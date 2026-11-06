<script setup lang="ts">
import { ref, onBeforeUnmount } from "vue";
import { apiFetch } from "../api";
import type { Row } from "../workspace";
const props = defineProps<{ taskCode: string; files: Row[] }>();
const error = ref(""),
  downloading = ref(-1);
const controller = new AbortController();
let alive = true;
const urls = new Set<string>();
onBeforeUnmount(() => {
  alive = false;
  controller.abort();
  urls.forEach((url) => URL.revokeObjectURL(url));
});
async function download(index: number) {
  if (downloading.value >= 0) return;
  downloading.value = index;
  error.value = "";
  try {
    const r = await apiFetch(
      `/api/v1/tasks/${props.taskCode}/inputs/${index}/download`,
      { signal: controller.signal },
    );
    if (!r.ok) throw Error(`下载失败（${r.status}）`);
    const blob = await r.blob();
    if (!alive || controller.signal.aborted) return;
    const url = URL.createObjectURL(blob),
      a = document.createElement("a");
    urls.add(url);
    a.href = url;
    a.download = props.files[index].file_name;
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      urls.delete(url);
    }, 1000);
  } catch (e) {
    if (alive && !controller.signal.aborted)
      error.value = e instanceof Error ? e.message : "下载失败";
  } finally {
    downloading.value = -1;
  }
}
</script>
<template>
  <section v-if="files.length" class="workspace-panel">
    <h2>任务参考附件</h2>
    <p class="muted">固定于创建时，作为模型参考材料使用。</p>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <div v-for="(file, index) in files" :key="index" class="resource-group">
      <p class="code-text">{{ file.file_name }} · {{ file.size_bytes }} 字节</p>
      <button
        class="secondary-button"
        :disabled="downloading >= 0"
        @click="download(index)"
      >
        {{ downloading === index ? "下载中…" : "下载附件" }}
      </button>
    </div>
  </section>
</template>
