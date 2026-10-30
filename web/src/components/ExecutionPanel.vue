<script setup lang="ts">
import { computed, ref } from "vue";
import { label, type Row } from "../workspace";
const props = defineProps<{
  execution: Row | null;
  error?: string;
  activity?: Row[];
}>();
const emit = defineEmits<{ refresh: [] }>();
const turn = ref("");
const turns = computed(() =>
  [...new Set((props.execution?.runs ?? []).map((r: Row) => r.turn_no))].sort(
    (a, b) => Number(b) - Number(a),
  ),
);
const runs = computed(() => {
  const rows: Row[] = (props.execution?.runs ?? []).filter(
    (r: Row) => !turn.value || r.turn_no === Number(turn.value),
  );
  const found = new Set<string>(),
    result: Row[] = [];
  function visit(r: Row) {
    if (found.has(r.run_code)) return;
    found.add(r.run_code);
    result.push(r);
    for (const child of rows.filter((c) => c.parent_run_code === r.run_code))
      visit(child);
  }
  for (const root of rows.filter(
    (r) => !rows.some((p) => p.run_code === r.parent_run_code),
  ))
    visit(root);
  for (const r of rows) visit(r);
  return result;
});
const duration = (ms: number) =>
  ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} 秒`;
</script>
<template>
  <section class="execution-panel" aria-label="任务执行详情">
    <div class="section-heading">
      <h2>执行详情</h2>
      <button class="secondary-button" @click="emit('refresh')">刷新</button>
    </div>
    <p v-if="error" class="inline-error" role="status">{{ error }}</p>
    <div v-if="activity?.length" class="live-activity">
      <h3>当前执行阶段</h3>
      <p v-for="item in activity" :key="item.run_code" class="muted">
        {{ item.child ? "子 Agent" : "主 Agent" }} ·
        {{ item.phase?.name || "准备执行" }} ·
        {{
          label(
            item.status === "RUNNING"
              ? item.phase?.status || item.status
              : item.status,
          )
        }}
      </p>
    </div>
    <template v-if="execution"
      ><div class="execution-metrics">
        <div>
          <b>{{ execution.run_count }}</b
          ><small>Run</small>
        </div>
        <div>
          <b>{{ execution.steps_count }}</b
          ><small>已记录步骤</small>
        </div>
        <div>
          <b>{{ execution.tokens ?? "—" }}</b
          ><small>模型 Token</small>
        </div>
      </div>
      <label class="execution-turn"
        >对话轮次<select v-model="turn">
          <option value="">全部轮次</option>
          <option v-for="n in turns" :key="String(n)" :value="String(n)">
            第 {{ n }} 轮
          </option>
        </select></label
      >
      <p v-if="!runs.length" class="muted">
        尚无运行记录，任务领取后自动更新。
      </p>
      <details
        v-for="run in runs"
        :key="run.run_code"
        class="execution-node"
        :class="{ 'child-run': !!run.parent_run_code }"
        :open="!run.parent_run_code"
      >
        <summary>
          <span>{{ run.parent_run_code ? "↳ " : "" }}{{ run.agent_name }}</span
          ><span class="status-badge" :data-status="run.status">{{
            label(run.status)
          }}</span>
        </summary>
        <p class="muted">
          第 {{ run.turn_no }} 轮 · 深度 {{ run.run_depth }} ·
          {{ duration(run.elapsed_ms) }}
        </p>
        <p class="muted">
          {{ run.steps_count }} 步 · Token {{ run.tokens ?? "未返回"
          }}<span v-if="run.failed_steps">
            · {{ run.failed_steps }} 步失败</span
          >
        </p>
        <p v-if="run.finish_reason" class="muted">
          结束原因：{{ label(run.finish_reason) }}
        </p>
        <ol class="execution-steps">
          <li v-for="step in run.steps.slice(-10)" :key="step.step_code">
            <span>{{ step.step_no }} · {{ step.name }}</span
            ><span class="status-badge" :data-status="step.status">{{
              label(step.status)
            }}</span>
          </li>
        </ol>
        <a :href="'#/runs/' + run.run_code">查看全部步骤与消息 →</a>
      </details></template
    >
    <p v-else class="muted">正在读取执行记录…</p>
  </section>
</template>
