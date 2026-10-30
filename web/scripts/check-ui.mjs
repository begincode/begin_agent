import { readdir, readFile } from "node:fs/promises"
import { dirname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"
import { parse } from "@vue/compiler-sfc"
import { controlTokens, inspectCss } from "./ui-rules.mjs"
import postcss from "postcss"

const project = dirname(dirname(fileURLToPath(import.meta.url)))
async function filesIn(directory) {
  const result = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) result.push(...await filesIn(path))
    else if (/\.(vue|css)$/.test(entry.name)) result.push(path)
  }
  return result.sort()
}
const classes = new Map([["md-copy-button", "single"]])
const styles = []
const errors = []
function visit(node, file) {
  const checkbox = node.tag === "input" && node.props?.some(p => p.name === "type" && p.value?.content === "checkbox")
  const kind = checkbox ? "checkbox" : node.tag === "textarea" ? "multiline" : ["button", "select", "input", "summary"].includes(node.tag) ? "single" : undefined
  if (kind) {
    for (const prop of node.props ?? []) {
      if (prop.name === "class" && prop.value) {
        for (const name of prop.value.content.split(/\s+/).filter(Boolean)) classes.set(name, kind)
      }
      if (prop.name === "style" || (prop.name === "bind" && prop.arg?.content === "style")) {
        const style = prop.value?.content ?? prop.exp?.content ?? ""
        if (/height|border.?radius/i.test(style)) errors.push(`${file}:${prop.loc.start.line}: 控件尺寸不得写在内联样式中，请使用共享 CSS 变量`)
      }
    }
  }
  for (const child of node.children ?? []) visit(child, file)
}
for (const path of await filesIn(join(project, "src"))) {
  const file = relative(project, path)
  const content = await readFile(path, "utf8")
  if (file.endsWith(".css")) styles.push({ file, content })
  else {
    const { descriptor, errors: parseErrors } = parse(content, { filename: path })
    for (const error of parseErrors) errors.push(`${file}: ${String(error)}`)
    if (descriptor.template?.ast) visit(descriptor.template.ast, file)
    for (const style of descriptor.styles) styles.push({ file, content: style.content })
  }
}
for (const style of styles) errors.push(...inspectCss(style.content, style.file, classes))
const main = await readFile(join(project, "src/style.css"), "utf8")
for (const [token, value] of Object.entries(controlTokens)) {
  let found = false
  postcss.parse(main).walkRules(":root", rule => {
    rule.walkDecls(token, decl => { if (decl.value.trim() === value) found = true })
  })
  if (!found) errors.push(`src/style.css: :root 必须定义 ${token}: ${value}`)
}
if (errors.length) {
  console.error("前端 UI 规范检查失败：\n" + errors.map(e => `- ${e}`).join("\n"))
  process.exitCode = 1
} else console.log(`UI 规范检查通过：${styles.length} 份样式，单行控件 40px / 圆角 8px；仍须完成浏览器验收。`)
