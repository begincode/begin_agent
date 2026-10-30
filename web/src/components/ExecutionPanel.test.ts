import { expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import ExecutionPanel from "./ExecutionPanel.vue";
it("orders child runs under their parent and filters by conversation turn", async () => {
  const run = (
    code: string,
    parent: string | null,
    turn: number,
    name: string,
  ) => ({
    run_code: code,
    parent_run_code: parent,
    turn_no: turn,
    agent_name: name,
    status: "SUCCEEDED",
    elapsed_ms: 1000,
    run_depth: parent ? 1 : 0,
    steps_count: 1,
    steps: [
      { step_code: code, step_no: 1, name: "模型调用", status: "SUCCEEDED" },
    ],
  });
  const wrapper = mount(ExecutionPanel, {
    props: {
      execution: {
        run_count: 3,
        steps_count: 3,
        tokens: null,
        runs: [
          run("child", "root", 1, "子助手"),
          run("root", null, 1, "主助手"),
          run("second", null, 2, "第二轮"),
        ],
      },
    },
  });
  expect(
    wrapper.findAll(".execution-node summary").map((e) => e.text()),
  ).toEqual(["主助手已完成", "↳ 子助手已完成", "第二轮已完成"]);
  await wrapper.get("select").setValue("2");
  expect(wrapper.findAll(".execution-node")).toHaveLength(1);
  expect(wrapper.text()).toContain("第二轮");
  expect(wrapper.text()).not.toContain("子助手");
  expect(wrapper.text()).toContain("未返回");
  wrapper.unmount();
});
