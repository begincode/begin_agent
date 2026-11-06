<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, post } from "../api";
import { date, useWorkspace, type Row } from "../workspace";

const { identity, can, go, readonly } = useWorkspace();
const sample = ref<Row | null>(null);
const loading = ref(false),
  error = ref("");
let request = 0;
let controller: AbortController | undefined;
const allowed = computed(() => can("tenant.manage"));
const queue = computed(() => sample.value?.queue);
const queueReady = computed(() => queue.value?.status === "ready");
const reasons: Record<string, string> = {
  LEASE_EXPIRED: "租约已过期",
  LEASE_MISSING: "运行任务缺少租约",
  LONG_WAIT: "可领取后等待超过五分钟",
  RECENT_FAILURE: "最近一天失败或超时",
};
const acknowledging = ref("");
const ackController = new AbortController();
async function acknowledge(fingerprint: string) {
  if (acknowledging.value || readonly.value) return;
  const scope = [identity.value?.user_code, identity.value?.tenant_code].join(
    ":",
  );
  acknowledging.value = fingerprint;
  error.value = "";
  try {
    await post(
      `/api/v1/integration-status/alerts/${fingerprint}/ack`,
      {},
      "POST",
      { signal: ackController.signal },
    );
    if (
      scope ===
      [identity.value?.user_code, identity.value?.tenant_code].join(":")
    )
      await refresh();
  } catch (e) {
    if (!ackController.signal.aborted)
      error.value = e instanceof Error ? e.message : "确认失败";
  } finally {
    acknowledging.value = "";
  }
}
const metrics = [
  ["pending", "待执行"],
  ["ready", "可领取"],
  ["delayed", "计划或重试等待"],
  ["running", "运行中"],
  ["expired_leases", "租约过期"],
  ["missing_leases", "缺少租约"],
  ["long_waiting", "等待超过五分钟"],
  ["failed_24h", "最近一天失败 / 超时"],
];
async function refresh() {
  const current = ++request;
  controller?.abort();
  if (!allowed.value) {
    sample.value = null;
    loading.value = false;
    return;
  }
  const pending = new AbortController();
  controller = pending;
  loading.value = true;
  error.value = "";
  try {
    const result = await api<Row>("/api/v1/integration-status", {
      signal: pending.signal,
    });
    if (current === request) sample.value = result;
  } catch (e) {
    if (current === request && !pending.signal.aborted)
      error.value = e instanceof Error ? e.message : "状态读取失败";
  } finally {
    if (current === request) loading.value = false;
  }
}
watch(
  () => [
    identity.value?.user_code,
    identity.value?.tenant_code,
    can("tenant.manage"),
    can("tasks.read"),
  ],
  () => {
    sample.value = null;
    error.value = "";
    void refresh();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  request++;
  controller?.abort();
  ackController.abort();
});
</script>

<template>
  <main class="platform-main">
    <div class="page-heading">
      <div>
        <span class="eyebrow">ADMINISTRATION</span>
        <h1>系统状态</h1>
        <p class="muted">查看运行环境，以及当前工作空间的任务队列。</p>
      </div>
      <button
        v-if="allowed"
        class="secondary-button"
        :disabled="loading"
        @click="refresh"
      >
        {{ loading ? "正在读取…" : error ? "重试读取" : "刷新状态" }}
      </button>
    </div>
    <p v-if="!allowed" class="inline-error" role="alert">
      没有此页面的访问权限。
    </p>
    <template v-else>
      <p v-if="error" class="inline-error" role="alert">
        状态读取失败：{{ error }}
      </p>
      <p v-if="loading && !sample" class="muted" role="status">
        正在读取系统状态…
      </p>
      <template v-if="sample">
        <p class="muted" role="status">
          {{
            error
              ? "以下为上次样本，当前状态尚未确认。"
              : loading
                ? "正在刷新，以下为上次样本。"
                : "采样时间："
          }}
          {{ date(sample.sampled_at) }} · 手动刷新，不会修改任务。
        </p>
        <section v-if="sample.cluster_workers" class="workspace-panel">
          <h2>数据库 Worker 心跳</h2>
          <p v-if="sample.cluster_workers.status === 'ready'">
            当前活跃 {{ sample.cluster_workers.active }} · 过期
            {{ sample.cluster_workers.stale }} · 有效窗口
            {{ sample.cluster_workers.threshold_seconds }} 秒
          </p>
          <p v-else class="inline-error" role="alert">
            数据库心跳暂不可用，请重试。
          </p>
          <p class="muted">
            覆盖连接同一数据库的 Worker，不依赖 API
            主机的进程号。心跳表示进程仍在上报，不等同于模型或工具就绪。
          </p>
        </section>
        <section v-if="sample.alert_status" class="workspace-panel">
          <h2>运行告警</h2>
          <p
            v-if="sample.alert_status !== 'ready'"
            class="inline-error"
            role="alert"
          >
            告警读取失败，请重试。
          </p>
          <p v-else-if="!sample.alerts?.length" class="muted">
            本次采样未发现告警。
          </p>
          <p class="muted">
            确认仅记录当前账号已查看，故障仍需处理；新样本会重新提示。不自动重试任务或发送外部通知。
          </p>
          <div
            v-for="alert in sample.alerts || []"
            :key="alert.fingerprint"
            class="resource-group"
          >
            <p>
              {{ alert.message }}
              <span v-if="alert.acknowledged" class="status-badge"
                >本账号已确认</span
              >
            </p>
            <button
              v-if="!alert.acknowledged"
              class="secondary-button"
              :disabled="!!acknowledging || loading || readonly"
              @click="acknowledge(alert.fingerprint)"
            >
              {{
                acknowledging === alert.fingerprint ? "正在确认…" : "确认已查看"
              }}
            </button>
          </div>
        </section>
        <div class="diagnostic-grid">
          <section class="workspace-panel">
            <h2>数据库</h2>
            <p
              class="status-badge"
              :data-status="
                sample.database?.status === 'ready' ? 'SUCCEEDED' : 'FAILED'
              "
            >
              {{
                sample.database?.status === "ready"
                  ? "连接与迁移就绪"
                  : sample.database?.status === "migration_mismatch"
                    ? "数据库迁移未就绪"
                    : "数据库状态未知"
              }}
            </p>
            <p v-if="sample.database?.status !== 'unavailable'" class="muted">
              目标迁移 {{ sample.database?.expected_revision }} · 本次检查
              {{ sample.database?.latency_ms }} ms
            </p>
            <p v-else class="muted">
              本次检查失败或超时，请重试；未返回连接地址和底层错误。
            </p>
          </section>
          <section class="workspace-panel">
            <h2>Worker 心跳</h2>
            <p
              class="status-badge"
              :data-status="sample.workers?.ready ? 'SUCCEEDED' : 'FAILED'"
            >
              {{
                sample.workers?.ready
                  ? "已观测到就绪 Worker"
                  : "未观测到就绪 Worker"
              }}
            </p>
            <p>
              本机就绪 {{ sample.workers?.active }} · 过期或退出
              {{ sample.workers?.stale }}
            </p>
            <p v-if="sample.workers?.newest_age_seconds != null" class="muted">
              最新心跳距今 {{ sample.workers.newest_age_seconds }} 秒，有效窗口
              {{ sample.workers.threshold_seconds }} 秒。
            </p>
            <p class="muted">
              仅检查本机共享数据目录，不能据此判断其他主机是否运行。
            </p>
            <p
              v-if="
                sample.workers?.invalid ||
                sample.workers?.foreign ||
                sample.workers?.unobservable ||
                sample.workers?.truncated
              "
              class="diagnostic-note"
            >
              损坏记录 {{ sample.workers.invalid }} · 其他主机
              {{ sample.workers.foreign }} · 无法观察
              {{ sample.workers.unobservable }}
              <span v-if="sample.workers.truncated">
                · 记录超过扫描上限，统计不完整</span
              >
            </p>
          </section>
        </div>
        <section class="workspace-panel">
          <h2>当前租户任务队列</h2>
          <p v-if="queue?.status === 'forbidden'" class="muted">
            需要查看任务权限，未读取队列指标。
          </p>
          <p v-else-if="!queueReady" class="inline-error">
            队列状态未知，请刷新重试。未知状态不按零值统计。
          </p>
          <template v-else>
            <div class="diagnostic-metrics">
              <div
                v-for="[field, title] in metrics"
                :key="field"
                class="diagnostic-metric"
              >
                <strong>{{ queue[field] }}</strong
                ><span class="muted">{{ title }}</span>
              </div>
            </div>
            <p class="muted">
              可领取任务最长等待
              {{ queue.oldest_ready_seconds }}
              秒。等待从计划执行或重试时间中较晚者计算；状态为只读样本。
            </p>
            <p
              v-if="
                queue.expired_leases ||
                queue.missing_leases ||
                queue.long_waiting
              "
              class="diagnostic-note"
              role="status"
            >
              有需要关注的任务，请查看执行记录。正常 Worker
              会按既有租约与重试规则处理，不在此自动修复。
            </p>
            <h3>需要关注的任务</h3>
            <p class="muted">
              优先展示租约异常，再展示等待任务和最近一天失败记录，最多
              {{ queue.sample_limit }} 条。
            </p>
            <p v-if="!queue.samples?.length" class="empty-state">
              本次未发现符合上述条件的任务。
            </p>
            <div
              v-for="task in queue.samples"
              :key="task.task_code"
              class="resource-row diagnostic-task"
            >
              <div class="resource-main">
                <strong>{{ task.title }}</strong>
                <span class="muted"
                  >{{ reasons[task.reason] }} ·
                  {{ date(task.observed_at) }}</span
                >
                <small class="muted">{{ task.task_code }}</small>
              </div>
              <button
                class="secondary-button"
                @click="go('tasks/' + task.task_code)"
              >
                查看任务
              </button>
            </div>
          </template>
        </section>
        <section class="workspace-panel">
          <h2>运行方式</h2>
          <p>
            {{
              sample.accounts?.enabled
                ? "账号密码登录已启用"
                : "本地开发免登录模式"
            }}
          </p>
          <p>
            PMS 目录托管：{{
              sample.pms?.managed ? "已启用" : "本地运行，尚未登记托管"
            }}
          </p>
          <h3>MCP 网络允许列表</h3>
          <p class="muted diagnostic-hosts">
            {{
              sample.mcp_allowed_hosts?.join("、") || "尚未配置允许的服务主机"
            }}
          </p>
          <p class="muted">
            允许列表表示连接范围，服务连通性请在对应 MCP 配置中检测。
          </p>
        </section>
      </template>
    </template>
  </main>
</template>
