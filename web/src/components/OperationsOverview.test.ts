import { expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import OperationsOverview from "./OperationsOverview.vue";
it("shows unreported usage without inventing zero and emits a period change", async () => {
  const w = mount(OperationsOverview, {
    props: {
      days: 7,
      busy: false,
      activity: {
        daily: [],
        task_count: 1,
        run_count: 1,
        success_rate: null,
        average_run_ms: null,
        model_calls: 1,
        tokens: null,
        unreported_calls: 1,
        model_usage: [
          {
            provider_code: "test",
            model_code: "model",
            calls: 1,
            tokens: null,
            unreported_calls: 1,
          },
        ],
      },
    },
  });
  expect(w.text()).toContain("未上报 Token");
  expect(w.get("tbody").text()).toContain("—");
  await w.get("select").setValue("30");
  expect(w.emitted("change")).toEqual([[30]]);
  w.unmount();
});
