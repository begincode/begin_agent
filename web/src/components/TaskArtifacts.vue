<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api, apiFetch } from "../api";
import { useWorkspace, date, type Row } from "../workspace";
import MarkdownContent from "./MarkdownContent.vue";
const props = defineProps<{
  artifacts: Row[];
  taskCode?: string;
  canUpload?: boolean;
}>();
const { can, readonly } = useWorkspace();
const files = ref<Row[] | null>(null),
  total = ref(0),
  loading = ref(false);
const rows = computed(() => files.value ?? props.artifacts);
const fileInput = ref<HTMLInputElement | null>(null),
  selected = ref<File | null>(null);
const uploading = ref(false),
  downloading = ref(""),
  uploadError = ref(""),
  listError = ref("");
const uploadStatus = ref(""),
  maxBytes = ref(20 * 1024 * 1024);
const controller = new AbortController();
let retryKey = "";
const mayUpload = computed(
  () =>
    !!props.taskCode &&
    props.canUpload &&
    !readonly.value &&
    can("tasks.create") &&
    can("tasks.read"),
);
function size(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
async function page(more = false) {
  if (!props.taskCode || loading.value || !alive) return;
  loading.value = true;
  listError.value = "";
  try {
    const result = await api<Row>(
      `/api/v1/tasks/${props.taskCode}/artifacts?limit=20&offset=${more ? rows.value.length : 0}`,
      { signal: controller.signal },
    );
    if (!alive) return;
    files.value = more
      ? [...rows.value, ...result.items].filter(
          (a, i, all) =>
            all.findIndex((b) => b.artifact_code === a.artifact_code) === i,
        )
      : result.items;
    total.value = result.total;
    maxBytes.value = result.max_file_bytes;
  } catch (e) {
    if (alive)
      listError.value = e instanceof Error ? e.message : "产物列表读取失败";
  } finally {
    if (alive) loading.value = false;
  }
}
function choose(event: Event) {
  selected.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  retryKey = crypto.randomUUID();
  uploadError.value = uploadStatus.value = "";
  if (
    selected.value &&
    (!selected.value.size || selected.value.size > maxBytes.value)
  )
    uploadError.value = !selected.value.size
      ? "不能上传空文件"
      : `文件超过 ${size(maxBytes.value)} 上限`;
}
async function upload() {
  if (
    !mayUpload.value ||
    uploading.value ||
    !selected.value ||
    !selected.value.size ||
    selected.value.size > maxBytes.value
  )
    return;
  uploading.value = true;
  uploadError.value = uploadStatus.value = "";
  try {
    retryKey ||= crypto.randomUUID();
    await api(
      `/api/v1/tasks/${props.taskCode}/artifacts?file_name=${encodeURIComponent(selected.value.name)}`,
      {
        method: "POST",
        body: selected.value,
        signal: controller.signal,
        headers: {
          "Content-Type": "application/octet-stream",
          "Idempotency-Key": retryKey,
        },
      },
    );
    if (!alive) return;
    selected.value = null;
    retryKey = "";
    if (fileInput.value) fileInput.value.value = "";
    uploadStatus.value = "文件产物已上传";
    await page();
  } catch (e) {
    if (alive)
      uploadError.value = e instanceof Error ? e.message : "上传失败，请重试";
  } finally {
    if (alive) uploading.value = false;
  }
}
async function downloadFile(a: Row) {
  if (!props.taskCode || downloading.value) return;
  downloading.value = a.artifact_code;
  feedback.value = null;
  try {
    const response = await apiFetch(
      `/api/v1/tasks/${props.taskCode}/artifacts/${a.artifact_code}/download`,
      { signal: controller.signal },
    );
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(
        typeof error?.detail === "string"
          ? error.detail
          : `下载失败（${response.status}）`,
      );
    }
    const blob = await response.blob();
    if (alive) saveBlob(blob, a.content?.file_name || "文件产物");
  } catch (e) {
    if (alive)
      feedback.value = {
        code: a.artifact_code,
        error: true,
        text: e instanceof Error ? e.message : "下载失败，请重试",
      };
  } finally {
    if (alive) downloading.value = "";
  }
}
onMounted(() => {
  if (props.taskCode) void page();
});
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
    saveBlob(
      new Blob([a.content.text], { type: "text/markdown;charset=utf-8" }),
      `${name}-v${a.version_no}.md`,
    );
  } catch {
    feedback.value = {
      code: a.artifact_code,
      text: "下载失败，请重试。",
      error: true,
    };
  }
}
function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  urls.add(url);
  const link = document.createElement("a");
  link.href = url;
  link.download =
    name
      .replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g, "_")
      .replace(/^[.\s]+|[.\s]+$/g, "")
      .slice(0, 128) || "文件产物";
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
}
onBeforeUnmount(() => {
  alive = false;
  controller.abort();
  timers.forEach(clearTimeout);
  urls.forEach((url) => URL.revokeObjectURL(url));
});
</script>
<template>
  <div class="conversation-width">
    <section
      v-if="mayUpload"
      class="workspace-panel stack-form artifact-upload"
      aria-label="上传文件产物"
    >
      <h2>追加文件产物</h2>
      <p class="muted">
        上传报告、图片或其他交付文件，保留为独立产物。单文件最多
        {{ size(maxBytes) }}，文件内容不会自动加入模型上下文。
      </p>
      <input
        ref="fileInput"
        type="file"
        hidden
        :disabled="uploading"
        aria-label="选择文件"
        @change="choose"
      />
      <p v-if="selected" class="code-text">
        {{ selected.name }} · {{ size(selected.size) }}
      </p>
      <div class="row-actions">
        <button
          type="button"
          class="secondary-button"
          :disabled="uploading"
          @click="fileInput?.click()"
        >
          选择文件
        </button>
        <button
          type="button"
          class="primary-button"
          :disabled="
            uploading ||
            loading ||
            !selected ||
            !selected.size ||
            selected.size > maxBytes
          "
          @click="upload"
        >
          {{ uploading ? "正在上传…" : "上传文件" }}
        </button>
      </div>
      <p v-if="uploadError" class="inline-error" role="alert">
        {{ uploadError }}
      </p>
      <p v-if="uploadStatus" class="muted" role="status">{{ uploadStatus }}</p>
    </section>
    <p v-if="!mayUpload && taskCode" class="muted">
      成功任务的创建者或任务管理员可以追加文件；历史产物始终保留。
    </p>
    <div v-if="listError" class="inline-error" role="alert">
      {{ listError }}
      <button
        type="button"
        class="secondary-button"
        :disabled="loading"
        @click="page()"
      >
        重新读取
      </button>
    </div>
    <p v-if="loading" class="muted" role="status">正在读取产物…</p>
    <article
      v-for="a in rows"
      :key="a.artifact_code"
      class="workspace-panel run-card artifact-card"
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
        <button
          v-else-if="a.artifact_type === 'FILE' && taskCode"
          type="button"
          class="secondary-button"
          :disabled="!!downloading"
          @click="downloadFile(a)"
        >
          {{ downloading === a.artifact_code ? "正在下载…" : "下载文件" }}
        </button>
      </div>
      <p class="muted">
        {{ date(a.created_at) }} · 产物版本 v{{ a.version_no }}
        <span v-if="a.artifact_type === 'FILE'">
          ·
          {{ a.content.source === "GENERATED" ? "执行生成" : "人工上传" }}</span
        >
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
      <template v-else-if="a.artifact_type === 'FILE'">
        <p class="muted">
          {{ a.content.source === "GENERATED" ? "执行生成" : "人工上传" }} ·
          {{ size(a.content.size_bytes) }}
        </p>
        <p v-if="a.content.uploaded_by" class="code-text">
          上传人 {{ a.content.uploaded_by }}
        </p>
        <p class="code-text artifact-checksum">
          SHA-256：{{ a.content.sha256 }}
        </p>
      </template>
      <p v-else class="muted">该产物暂不支持下载，请查看来源运行记录。</p>
    </article>
    <p v-if="!rows.length && !loading && !listError" class="blank-state">
      任务成功后，文本和文件产物会保存在这里。
    </p>
    <button
      v-if="files && rows.length < total"
      type="button"
      class="secondary-button"
      :disabled="loading"
      @click="page(true)"
    >
      加载更多（{{ rows.length }}/{{ total }}）
    </button>
  </div>
</template>
