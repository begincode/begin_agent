<script setup lang="ts">
import { computed } from "vue";
import type { Row } from "../workspace";
const props = defineProps<{ activity: Row; days: number; busy: boolean }>();
const emit = defineEmits<{ change: [days: number] }>();
const maxTasks = computed(() =>
  Math.max(1, ...props.activity.daily.map((d: Row) => d.total)),
);
</script>
<template>
  <section class="workspace-panel operations-overview">
    <div class="section-heading">
      <div>
        <h2>近期运行统计</h2>
        <p class="muted">按北京时间创建日期统计；成功率不含取消任务。</p>
      </div>
      <label class="operations-period"
        >统计周期<select
          :value="days"
          :disabled="busy"
          @change="
            emit('change', Number(($event.target as HTMLSelectElement).value))
          "
        >
          <option :value="7">最近 7 天</option>
          <option :value="30">最近 30 天</option>
        </select></label
      >
    </div>
    <div class="operations-metrics">
      <span v-if="activity.task_count !== null"
        >任务 <b>{{ activity.task_count }}</b></span
      ><span v-if="activity.task_count !== null"
        >成功率
        <b>{{
          activity.success_rate === null ? "—" : activity.success_rate + "%"
        }}</b></span
      ><span v-if="activity.run_count !== null"
        >Run <b>{{ activity.run_count }}</b></span
      ><span v-if="activity.run_count !== null"
        >平均耗时
        <b>{{
          activity.average_run_ms === null
            ? "—"
            : (activity.average_run_ms / 1000).toFixed(1) + " 秒"
        }}</b></span
      ><span v-if="activity.model_calls !== null"
        >模型调用 <b>{{ activity.model_calls }}</b></span
      ><span v-if="activity.run_count !== null"
        >已上报 Token <b>{{ activity.tokens ?? "—" }}</b></span
      >
    </div>
    <p v-if="activity.unreported_calls" class="inline-hint">
      {{ activity.unreported_calls }} 次模型调用未上报
      Token，用量合计仅包含已上报值。
    </p>
    <figure
      v-if="activity.daily.length"
      class="activity-chart"
      aria-label="每日任务数量"
    >
      <figcaption class="muted">
        每日任务数量 · {{ activity.from_date }} 至 {{ activity.to_date }}
      </figcaption>
      <div class="activity-days">
        <div
          v-for="d in activity.daily"
          :key="d.date"
          class="activity-day"
          :title="
            d.date + '：' + d.total + ' 个任务，' + d.succeeded + ' 个成功'
          "
        >
          <div class="activity-bars">
            <span
              class="activity-bar"
              :style="{ height: (d.total / maxTasks) * 70 + 'px' }"
            ></span>
          </div>
          <b>{{ d.total }}</b
          ><small>{{ d.date.slice(5) }}</small>
        </div>
      </div>
    </figure>
    <div v-if="activity.run_count !== null" class="usage-table-scroll">
      <table class="usage-table">
        <thead>
          <tr>
            <th>供应商 / 模型</th>
            <th>调用次数</th>
            <th>已上报 Token</th>
            <th>未上报次数</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="m in activity.model_usage"
            :key="m.provider_code + ':' + m.model_code"
          >
            <td>{{ m.provider_code }} / {{ m.model_code }}</td>
            <td>{{ m.calls }}</td>
            <td>{{ m.tokens ?? "—" }}</td>
            <td>{{ m.unreported_calls }}</td>
          </tr>
          <tr v-if="!activity.model_usage.length">
            <td colspan="4">当前时间范围暂无模型调用记录。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
