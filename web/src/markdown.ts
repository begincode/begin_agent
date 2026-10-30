import DOMPurify from "dompurify"
import hljs from "highlight.js/lib/common"
import MarkdownIt, { type Env } from "markdown-it"

interface RenderEnvironment extends Env { codeBlocks: string[] }
export interface RenderedMarkdown { html: string; codeBlocks: string[] }

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: true })
const escape = markdown.utils.escapeHtml

function codeBlock(code: string, info: string, env: RenderEnvironment): string {
  const language = info.trim().split(/\s+/)[0]?.toLowerCase() ?? ""
  let highlighted = escape(code)
  let label = language || "text"
  // Large blocks remain readable and copyable without expensive syntax detection.
  if (code.length <= 50_000) {
    try {
      if (language && hljs.getLanguage(language)) {
        highlighted = hljs.highlight(code, { language, ignoreIllegals: true }).value
      } else if (!language) {
        const result = hljs.highlightAuto(code, ["javascript", "python", "json", "bash", "sql"])
        highlighted = result.value
        label = result.language ?? "text"
      }
    } catch { /* Unknown or incomplete streamed syntax falls back to escaped text. */ }
  }
  const index = env.codeBlocks.push(code) - 1
  return `<div class="md-code-block"><div class="md-code-toolbar"><span title="${escape(label)}">${escape(label)}</span><button type="button" class="md-copy-button" data-code-index="${index}" aria-label="复制代码">复制</button></div><pre><code class="hljs">${highlighted}</code></pre></div>\n`
}

markdown.renderer.rules.fence = (tokens, index, _options, env) =>
  codeBlock(tokens[index].content, tokens[index].info, env as RenderEnvironment)
markdown.renderer.rules.code_block = (tokens, index, _options, env) =>
  codeBlock(tokens[index].content, "", env as RenderEnvironment)

// Images are outside this text renderer's scope; do not load model-provided remote URLs.
markdown.renderer.rules.image = (tokens, index) => escape(tokens[index].content)
const renderLink = markdown.renderer.rules.link_open ?? ((tokens, index, options, _env, self) => self.renderToken(tokens, index, options))
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet("target", "_blank")
  tokens[index].attrSet("rel", "noopener noreferrer")
  return renderLink(tokens, index, options, env, self)
}
// Keep wide tables inside the response card instead of widening the page.
markdown.renderer.rules.table_open = () => '<div class="md-table-scroll" tabindex="0" role="region" aria-label="表格"><table>\n'
markdown.renderer.rules.table_close = () => "</table></div>\n"
for (const type of ["th_open", "td_open"]) {
  markdown.renderer.rules[type] = (tokens, index, options, _env, self) => {
    const style = tokens[index].attrGet("style")
    const alignment = typeof style === "string" ? style.match(/^text-align:\s*(left|center|right);?$/)?.[1] : undefined
    if (alignment) tokens[index].attrSet("align", alignment)
    return self.renderToken(tokens, index, options)
  }
}

export function renderMarkdown(source: string): RenderedMarkdown {
  const env: RenderEnvironment = { codeBlocks: [] }
  const html = DOMPurify.sanitize(markdown.render(source, env), {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target"],
    FORBID_TAGS: ["img", "style", "form", "input"],
    FORBID_ATTR: ["style"],
  })
  return { html, codeBlocks: env.codeBlocks }
}
