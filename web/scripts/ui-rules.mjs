import postcss from "postcss"

export const controlTokens = {
  "--control-height": "40px",
  "--control-radius": "8px",
}

function controlKind(selector, classes) {
  if (/input\[type=["']?checkbox["']?\]/.test(selector) && !selector.includes(":not(")) return "checkbox"
  if (/(?:^|[\s(,>+~])(?:button|select|input|summary)(?=$|[\s.#[:),>+~])/.test(selector)) return "single"
  if (/(?:^|[\s(,>+~])textarea(?=$|[\s.#[:),>+~])/.test(selector)) return "multiline"
  for (const match of selector.matchAll(/\.([\w-]+)/g)) {
    if (classes.has(match[1])) return classes.get(match[1])
  }
  return undefined
}

// This checks authored dimensions, including media queries and component styles.
// Actual browser geometry, clipping and visual alignment remain required UI acceptance checks.
export function inspectCss(css, file, classes = new Map([["md-copy-button", "single"]])) {
  const errors = []
  const root = postcss.parse(css, { from: file })
  root.walkDecls(decl => {
    const report = message => errors.push(`${file}:${decl.source.start.line}: ${message}`)
    const value = decl.value.trim()
    if (Object.hasOwn(controlTokens, decl.prop) && value !== controlTokens[decl.prop]) {
      report(`${decl.prop} 必须为 ${controlTokens[decl.prop]}，禁止通过局部或媒体查询改写统一尺寸`)
    }
    if (decl.parent.type !== "rule") return
    const kinds = postcss.list.comma(decl.parent.selector).map(s => controlKind(s, classes))
    const sizeProperty = ["height", "min-height", "max-height"].includes(decl.prop)
    if (sizeProperty && kinds.includes("single") && value !== "var(--control-height)") {
      report(`单行控件的 ${decl.prop} 必须使用 var(--control-height)，当前为 ${decl.value}`)
    }
    if (decl.prop === "border-radius" && (kinds.includes("single") || kinds.includes("multiline")) && value !== "var(--control-radius)") {
      report(`控件圆角必须使用 var(--control-radius)，当前为 ${decl.value}`)
    }
  })
  return errors
}
