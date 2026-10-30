<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { Row } from "../workspace";
const props = defineProps<{ versions: Row[]; current?: string | null }>();
const left = ref(""),
  right = ref("");
watch(
  () => props.versions,
  (values) => {
    if (!values.some((v) => v.version_code === right.value))
      right.value = props.current || values[0]?.version_code || "";
    if (!values.some((v) => v.version_code === left.value))
      left.value =
        values.find((v) => v.version_code !== right.value)?.version_code ||
        right.value;
  },
  { immediate: true },
);
function stable(value: any): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(stable).join(",") + "]";
  return (
    "{" +
    Object.keys(value)
      .sort()
      .map((k) => JSON.stringify(k) + ":" + stable(value[k]))
      .join(",") +
    "}"
  );
}
const fields = [
  ["system_prompt", "系统提示词"],
  ["profile_code", "模型配置"],
  ["model_snapshot", "模型快照"],
  ["model_parameters", "模型参数"],
  ["execution_config", "执行边界"],
  ["tools", "工具授权"],
  ["skills", "Skill 指令"],
  ["mcps", "MCP 工具"],
  ["children", "子 Agent"],
] as const;
const rows = computed(() => {
  const a = props.versions.find((v) => v.version_code === left.value),
    b = props.versions.find((v) => v.version_code === right.value);
  if (!a || !b) return [];
  return fields.map(([key, name]) => {
    const first = key in a ? a[key] : (a.dependency_snapshot?.[key] ?? []),
      second = key in b ? b[key] : (b.dependency_snapshot?.[key] ?? []);
    return {
      key,
      name,
      changed: stable(first) !== stable(second),
      before:
        typeof first === "string" ? first : JSON.stringify(first, null, 2),
      after:
        typeof second === "string" ? second : JSON.stringify(second, null, 2),
    };
  });
});
const changed = computed(() => rows.value.filter((r) => r.changed).length);
</script>
<template>
  <section v-if="versions.length" class="version-comparison">
    <h2>版本对比</h2>
    <div class="comparison-selects">
      <label
        >基准版本<select v-model="left">
          <option
            v-for="v in versions"
            :key="v.version_code"
            :value="v.version_code"
          >
            v{{ v.version_no }} · {{ v.status === "DEBUG" ? "调试" : "发布" }}
          </option>
        </select></label
      ><label
        >目标版本<select v-model="right">
          <option
            v-for="v in versions"
            :key="v.version_code"
            :value="v.version_code"
          >
            v{{ v.version_no }} · {{ v.status === "DEBUG" ? "调试" : "发布" }}
          </option>
        </select></label
      ><span class="muted">{{ changed }} 项变化</span>
    </div>
    <div class="comparison-scroll">
      <table class="comparison-table">
        <thead>
          <tr>
            <th>配置</th>
            <th>基准版本</th>
            <th>目标版本</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.key"
            :class="{ 'changed-field': row.changed }"
          >
            <th>
              {{ row.name }}<small>{{ row.changed ? "已变更" : "相同" }}</small>
            </th>
            <td>
              <pre>{{ row.before }}</pre>
            </td>
            <td>
              <pre>{{ row.after }}</pre>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
