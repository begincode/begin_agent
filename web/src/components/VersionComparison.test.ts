import { expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import VersionComparison from "./VersionComparison.vue";
it("compares immutable fields and ignores object key ordering", async () => {
  const a = {
    version_code: "v1",
    version_no: 1,
    status: "PUBLISHED",
    profile_code: "model",
    system_prompt: "旧指令",
    model_parameters: { thinking: "auto", max_tokens: 10 },
    execution_config: {},
    dependency_snapshot: { tools: [], skills: [], mcps: [], children: [] },
  };
  const b = {
    ...a,
    version_code: "v2",
    version_no: 2,
    system_prompt: "新指令",
    model_parameters: { max_tokens: 10, thinking: "auto" },
  };
  const w = mount(VersionComparison, {
    props: { versions: [b, a], current: "v2" },
  });
  expect(w.findAll(".changed-field")).toHaveLength(1);
  expect(w.text()).toContain("旧指令");
  expect(w.text()).toContain("新指令");
  await w.findAll("select")[0].setValue("v2");
  expect(w.text()).toContain("0 项变化");
  w.unmount();
});
