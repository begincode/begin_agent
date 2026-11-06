import { defineAsyncComponent, defineComponent, h, type Component } from "vue";
const Loading = defineComponent({
  setup: () => () =>
    h(
      "main",
      { class: "platform-main", "data-page-loading": "", role: "status" },
      "正在加载页面…",
    ),
});
const Failed = defineComponent({
  setup: () => () =>
    h("main", { class: "platform-main" }, [
      h("section", { class: "workspace-panel" }, [
        h(
          "p",
          { role: "alert", class: "inline-error" },
          "页面资源加载失败，请刷新页面重试。",
        ),
        h(
          "button",
          { class: "secondary-button", onClick: () => location.reload() },
          "刷新页面",
        ),
      ]),
    ]),
});
export function asyncPage(loader: () => Promise<{ default: Component }>) {
  return defineAsyncComponent({
    loader: () => loader().then((module) => module.default),
    loadingComponent: Loading,
    errorComponent: Failed,
    delay: 0,
    timeout: 30000,
  });
}
