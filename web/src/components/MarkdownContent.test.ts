import { afterEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import MarkdownContent from "./MarkdownContent.vue"
import { renderMarkdown } from "../markdown"

const wrappers: ReturnType<typeof mount>[] = []
function render(content: string) {
  const wrapper = mount(MarkdownContent, { props: { content } })
  wrappers.push(wrapper)
  return wrapper
}
function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } })
  return writeText
}
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); vi.restoreAllMocks() })

describe("Markdown replies", () => {
  it("renders semantic headings, nested lists, tables, inline code and highlighted fences", () => {
    const wrapper = render('# 标题\n\n## 小节\n\n- 项目\n  - 子项\n\n1. 第一步\n2. 第二步\n\n| 模型 | 状态 |\n| --- | --- |\n| DeepSeek | 通过 |\n\n正文含 `profile_code`。\n\n```python\ndef hello():\n    return "你好"\n```')
    expect(wrapper.get("h1").text()).toBe("标题")
    expect(wrapper.get("h2").text()).toBe("小节")
    expect(wrapper.findAll("ul ul li")).toHaveLength(1)
    expect(wrapper.findAll("ol > li")).toHaveLength(2)
    expect(wrapper.get("th").text()).toBe("模型")
    expect(wrapper.get("tbody td").text()).toBe("DeepSeek")
    expect(wrapper.get("p code").text()).toBe("profile_code")
    expect(wrapper.find("pre .hljs-keyword").exists()).toBe(true)
    expect(wrapper.get("pre code").element.textContent).toBe('def hello():\n    return "你好"\n')
    expect(wrapper.get("button").attributes("aria-label")).toBe("复制代码")
  })

  it("keeps HTML, script URLs and remote image payloads inert", () => {
    const html = renderMarkdown('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n\n![remote](https://example.test/tracker)\n\n```html\n</code><img src=x onerror=alert(1)>\n```').html
    const container = document.createElement("div")
    container.innerHTML = html
    expect(container.querySelector("script, img, iframe, svg")).toBeNull()
    expect(container.querySelector("[onerror], [onclick], a[href^='javascript:']")).toBeNull()
    expect(container.querySelector("pre code")?.textContent).toContain("<img src=x onerror=alert(1)>")
  })

  it("copies only the selected block, preserving whitespace and literal HTML", async () => {
    const writeText = clipboard()
    const wrapper = render('```python\nprint("first")\n```\n\n```unknown-language\n\t<value>& literal\n\n```')
    await wrapper.findAll("button")[1].trigger("click")
    await flushPromises()
    expect(writeText).toHaveBeenCalledExactlyOnceWith("\t<value>& literal\n\n")
    expect(wrapper.findAll("button")[1].text()).toBe("已复制")
    expect(wrapper.get('[role="status"]').text()).toBe("代码已复制")
  })

  it("renders an open streaming fence and updates the copied content as chunks arrive", async () => {
    const writeText = clipboard()
    const wrapper = render('## 流式\n\n```javascript\nconst value =')
    expect(wrapper.get("pre code").text()).toBe("const value =")
    await wrapper.setProps({ content: '## 流式\n\n```javascript\nconst value = 42;\n```\n\n完成。' })
    expect(wrapper.findAll(".md-code-block")).toHaveLength(1)
    expect(wrapper.get("pre .hljs-number").text()).toBe("42")
    await wrapper.get("button").trigger("click")
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith("const value = 42;\n")
    expect(wrapper.text()).toContain("完成。")
  })

  it("supports unlabelled and indented code without losing source text", () => {
    const { html, codeBlocks } = renderMarkdown('    const indented = 1;\n\n```\nprint("中文")\n```')
    expect(codeBlocks).toEqual(['const indented = 1;\n', 'print("中文")\n'])
    expect(html.match(/md-copy-button/g)).toHaveLength(2)
  })

  it("keeps a long code language label available as a tooltip and preserves its code", () => {
    const language = "unknown_language_".repeat(18)
    const wrapper = render(`\`\`\`${language}\nvalue = 42\n\`\`\``)
    expect(wrapper.get(".md-code-toolbar > span").attributes("title")).toBe(language)
    expect(wrapper.get("pre code").element.textContent).toBe("value = 42\n")
    expect(wrapper.get("button").attributes("aria-label")).toBe("复制代码")
  })

  it("preserves table alignment and secures external links", () => {
    const wrapper = render('| 左 | 右 |\n| :--- | ---: |\n| A | B |\n\n[文档](https://example.test/docs)')
    expect(wrapper.findAll("th")[1].attributes("align")).toBe("right")
    expect(wrapper.get("a").attributes("target")).toBe("_blank")
    expect(wrapper.get("a").attributes("rel")).toBe("noopener noreferrer")
  })

  it("provides a copy fallback when the Clipboard API fails", async () => {
    clipboard(vi.fn().mockRejectedValue(new Error("denied")))
    const fallback = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, "execCommand", { configurable: true, value: fallback })
    const wrapper = render('```text\ncopy me\n```')
    await wrapper.get("button").trigger("click")
    await flushPromises()
    expect(fallback).toHaveBeenCalledWith("copy")
    expect(document.querySelector("textarea")).toBeNull()
    expect(wrapper.get("button").text()).toBe("已复制")
  })

  it("reports copy failure instead of claiming success", async () => {
    clipboard(vi.fn().mockRejectedValue(new Error("denied")))
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn().mockReturnValue(false) })
    const wrapper = render('```text\ncopy me\n```')
    await wrapper.get("button").trigger("click")
    await flushPromises()
    expect(wrapper.get("button").text()).toContain("复制失败")
    expect(wrapper.get('[role="status"]').text()).toContain("复制失败")
  })
})
