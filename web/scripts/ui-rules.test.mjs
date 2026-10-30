import assert from "node:assert/strict"
import test from "node:test"
import { inspectCss } from "./ui-rules.mjs"

test("allows shared tokens and taller multiline composers", () => {
  assert.deepEqual(inspectCss(':root { --control-height:40px; --control-radius:8px } button, select { height:var(--control-height); border-radius:var(--control-radius) } textarea { min-height:68px; border-radius:var(--control-radius) } input[type="checkbox"] { height:16px }', 'ok.css'), [])
})
test("rejects a conflicting mobile height and radius", () => {
  const errors = inspectCss('@media (max-width:680px) { .generation-controls select { height:38px; border-radius:7px } }', 'mobile.css')
  assert.equal(errors.length, 2)
  assert.match(errors[0], /var\(--control-height\)/)
})
test("rejects a local token override that could silently defeat all controls", () => {
  assert.equal(inspectCss('.panel { --control-height:34px; --control-radius:6px }', 'bad.css').length, 2)
})
test("checks a newly introduced button class collected from a Vue template", () => {
  assert.equal(inspectCss('.new-action { height:36px }', 'component.vue', new Map([["new-action", "single"]])).length, 1)
})
test("checks native disclosure controls as well as regular buttons", () => {
  assert.equal(inspectCss('.reasoning-panel summary { height:28px; border-radius:6px }', 'bad.css').length, 2)
})
test("does not exempt a text input or button in a mixed checkbox selector", () => {
  assert.equal(inspectCss('input[type="checkbox"], button { height:16px }', 'bad.css').length, 1)
  assert.equal(inspectCss('input:not([type="checkbox"]) { height:36px }', 'bad.css').length, 1)
})
