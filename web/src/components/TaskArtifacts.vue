<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { useWorkspace, date, type Row } from "../workspace";
import MarkdownContent from "./MarkdownContent.vue";
defineProps<{ artifacts: Row[] }>();
const { can } = useWorkspace();
const copying = ref("");
const feedback = ref<{ code: string; text: string; error: boolean } | null>(
  null,
);
const urls = new Set<string>();
const timers = new Set<ReturnType<typeof setTimeout>>();
let alive = true;
function isText(a: Row) {
  return a.artifact_type === "TEXT" && typeof a.content?.text === "string";
}
async function copy(a: Row) {
  if (!isText(a) || copying.value) return;
  copying.value = a.artifact_code;
  try {
    if (!navigator.clipboard?.writeText)
      throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(a.content.text);
    if (alive)
      feedback.value = {
        code: a.artifact_code,
        text: "原文已复制",
        error: false,
      };
  } catch {
    if (alive)
      feedback.value = {
        code: a.artifact_code,
        text: "复制失败，请重试或下载 Markdown。",
        error: true,
      };
  } finally {
    if (alive) copying.value = "";
  }
}
function download(a: Row) {
  if (!isText(a)) return;
  try {
    const name =
      String(a.artifact_name || "任务产物")
        .normalize("NFKC")
        .replace(/[\x00-\x1f\x7f\\/:*?"<>|]/g, "_")
        .replace(/^[.\s]+|[.\s]+$/g, "")
        .slice(0, 90) || "任务产物";
    const url = URL.createObjectURL(
      new Blob([a.content.text], { type: "text/markdown;charset=utf-8" }),
    );
    urls.add(url);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name}-v${a.version_no}.md`;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    const timer = setTimeout(() => {
      URL.revokeObjectURL(url);
      urls.delete(url);
      timers.delete(timer);
    }, 1000);
    timers.add(timer);
  } catch {
    feedback.value = {
      code: a.artifact_code,
      text: "下载失败，请重试。",
      error: true,
    };
  }
}
onBeforeUnmount(() => {
  alive = false;
  timers.forEach(clearTimeout);
  urls.forEach((url) => URL.revokeObjectURL(url));
});
</script>
<template>
  <div class="conversation-width">
    <article
      v-for="a in artifacts"
      :key="a.artifact_code"
      class="workspace-panel run-card"
    >
      <div class="section-heading">
        <h2>{{ a.artifact_name }} · v{{ a.version_no }}</h2>
        <div v-if="isText(a)" class="row-actions">
          <button
            class="secondary-button"
            :disabled="!!copying"
            @click="copy(a)"
          >
            {{ copying === a.artifact_code ? "正在复制…" : "复制原文" }}
          </button>
          <button class="secondary-button" @click="download(a)">
            下载 Markdown
          </button>
        </div>
      </div>
      <p class="muted">
        {{ date(a.created_at) }} · 产物版本 v{{ a.version_no }}
      </p>
      <p class="code-text">
        来源 Run
        <a v-if="can('runs.read')" :href="'#/runs/' + a.run_code">{{
          a.run_code
        }}</a
        ><span v-else>{{ a.run_code }}</span>
      </p>
      <p
        v-if="feedback && feedback.code === a.artifact_code"
        :class="feedback.error ? 'inline-error' : 'muted'"
        :role="feedback.error ? 'alert' : 'status'"
      >
        {{ feedback.text }}
      </p>
      <MarkdownContent v-if="isText(a)" :content="a.content.text" />
      <p v-else class="muted">该产物不是可下载的文本，请查看来源运行记录。</p>
    </article>
    <p v-if="!artifacts.length" class="blank-state">
      任务成功后，文本产物会保存在这里。
    </p>
  </div>
</template>
