import { flushPromises, mount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { asyncPage } from "./pageLoading";
it("renders a visible loading state and the resolved page", async () => {
  let release!: (value: {
    default: ReturnType<typeof defineComponent>;
  }) => void;
  const component = asyncPage(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const w = mount(
    defineComponent({
      components: { Page: component },
      template: "<section><Page /></section>",
    }),
  );
  expect(w.get('[role="status"]').text()).toContain("正在加载页面");
  release({
    default: defineComponent({ template: "<main>异步页面已加载</main>" }),
  });
  await flushPromises();
  await vi.waitFor(() => expect(w.text()).toContain("异步页面已加载"));
  w.unmount();
});
it("offers refresh instead of a blank page when a chunk fails", async () => {
  const handled = vi.fn();
  const component = asyncPage(async () => {
    throw new Error("test chunk unavailable");
  });
  const w = mount(
    defineComponent({
      components: { Page: component },
      template: "<section><Page /></section>",
    }),
    {
      global: { config: { errorHandler: handled } },
    },
  );
  await flushPromises();
  await vi.waitFor(() => expect(w.find('[role="alert"]').exists()).toBe(true));
  expect(w.get('[role="alert"]').text()).toContain("页面资源加载失败");
  expect(w.get("button").text()).toBe("刷新页面");
  w.unmount();
  expect(handled).toHaveBeenCalled();
});
