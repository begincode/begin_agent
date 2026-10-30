<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue"
import { renderMarkdown } from "../markdown"
import "highlight.js/styles/github-dark.css"

const props = defineProps<{ content: string }>()
const rendered = computed(() => renderMarkdown(props.content))
const root = ref<HTMLElement | null>(null)
const copyStatus = ref("")
const timers = new Set<ReturnType<typeof setTimeout>>()
onBeforeUnmount(() => { for (const timer of timers) clearTimeout(timer) })

async function writeClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return } catch { /* Try legacy copy below. */ }
  }
  const previousFocus = document.activeElement as HTMLElement | null
  const selection = window.getSelection()
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : []
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  textarea.style.cssText = "position:fixed;left:-9999px;top:0"
  document.body.appendChild(textarea)
  try {
    textarea.select()
    if (!document.execCommand("copy")) throw new Error("Clipboard unavailable")
  } finally {
    textarea.remove()
    previousFocus?.focus({ preventScroll: true })
    if (selection) { selection.removeAllRanges(); for (const range of ranges) selection.addRange(range) }
  }
}

async function copyCode(event: MouseEvent) {
  const button = (event.target as Element | null)?.closest<HTMLButtonElement>("button[data-code-index]")
  if (!button || !root.value?.contains(button) || button.disabled) return
  const code = rendered.value.codeBlocks[Number(button.dataset.codeIndex)]
  if (code === undefined) return
  button.disabled = true
  try {
    await writeClipboard(code)
    button.textContent = "已复制"
    copyStatus.value = "代码已复制"
  } catch {
    button.textContent = "复制失败，请重试"
    copyStatus.value = "复制失败，请重试或手动选中代码复制"
  } finally {
    button.disabled = false
    const timer = setTimeout(() => { button.textContent = "复制"; copyStatus.value = ""; timers.delete(timer) }, 2000)
    timers.add(timer)
  }
}
</script>

<template>
  <div class="markdown-content">
    <div ref="root" class="markdown-body" @click="copyCode" v-html="rendered.html"></div>
    <span class="sr-only" role="status" aria-live="polite">{{ copyStatus }}</span>
  </div>
</template>
