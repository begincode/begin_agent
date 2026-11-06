import { afterEach, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import SystemStatus from "./SystemStatus.vue";
import { workspaceKey, type Workspace } from "../workspace";

const wrappers: ReturnType<typeof mount>[] = [];
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.unstubAllGlobals();
});
function snapshot(count = 0) {
  return {
    sampled_at: "2026-11-06T08:00:00Z",
    database: {
      status: "ready",
      expected_revision: "20261029_06",
      latency_ms: 0,
    },
    workers: {
      ready: true,
      active: 1,
      stale: 0,
      invalid: 0,
      foreign: 0,
      unobservable: 0,
      threshold_seconds: 60,
      newest_age_seconds: 0,
    },
    queue: {
      status: "ready",
      pending: count,
      ready: count,
      delayed: 0,
      running: 0,
      expired_leases: 0,
      missing_leases: 0,
      long_waiting: 0,
      failed_24h: 0,
      oldest_ready_seconds: 0,
      sample_limit: 10,
      samples: [],
    },
    accounts: { enabled: true },
    pms: { managed: false },
    mcp_allowed_hosts: [],
  };
}
function setup(
  fetcher: (...args: any[]) => any,
  permissions = ["tenant.manage", "tasks.read"],
) {
  const fetchMock = vi.fn(fetcher);
  vi.stubGlobal("fetch", fetchMock);
  const state: Workspace = {
    identity: ref({ tenant_code: "ten_one", user_code: "user", permissions }),
    data: ref({}),
    busy: ref(false),
    readonly: ref(false),
    can: (p) => state.identity.value!.permissions.includes(p),
    go: vi.fn(),
    load: async () => {},
    act: async (fn) => {
      await fn();
    },
  };
  const w = mount(SystemStatus, {
    global: { provide: { [workspaceKey as symbol]: state } },
  });
  wrappers.push(w);
  return { w, state, fetchMock };
}
const response = (data: unknown) => new Response(JSON.stringify(data));
it("shows loading and real zero counts without inventing an outage", async () => {
  let resolve!: (r: Response) => void;
  const { w } = setup(
    () =>
      new Promise<Response>((r) => {
        resolve = r;
      }),
  );
  expect(w.text()).toContain("正在读取系统状态");
  expect(w.get("button").attributes("disabled")).toBeDefined();
  resolve(response(snapshot()));
  await flushPromises();
  expect(w.text()).toContain("连接与迁移就绪");
  expect(w.text()).toContain("本次未发现");
  expect(w.findAll(".diagnostic-metric strong").map((e) => e.text())).toEqual(
    Array(8).fill("0"),
  );
  expect(w.text()).toContain("本地运行，尚未登记托管");
});
it("retries an initial error and marks a failed refresh as an old sample", async () => {
  let calls = 0;
  const { w } = setup(async () => {
    calls++;
    if (calls === 1 || calls === 3) throw new Error("网络中断");
    return response(snapshot());
  });
  await flushPromises();
  expect(w.text()).toContain("状态读取失败");
  expect(w.find(".diagnostic-metrics").exists()).toBe(false);
  await w.get("button").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("连接与迁移就绪");
  await w.get("button").trigger("click");
  await flushPromises();
  expect(w.text()).toContain("以下为上次样本，当前状态尚未确认");
  expect(w.find(".diagnostic-metrics").exists()).toBe(true);
});
it("does not request diagnostics without permission or display inaccessible task counts", async () => {
  const noAccess = setup(async () => response(snapshot()), []);
  await flushPromises();
  expect(noAccess.fetchMock).not.toHaveBeenCalled();
  expect(noAccess.w.text()).toContain("没有此页面的访问权限");
  const data = { ...snapshot(), queue: { status: "forbidden" } };
  const limited = setup(async () => response(data), ["tenant.manage"]);
  await flushPromises();
  expect(limited.w.text()).toContain("需要查看任务权限");
  expect(limited.w.find(".diagnostic-metrics").exists()).toBe(false);
});
it("discards late tenant responses and aborts obsolete and unmounted requests", async () => {
  const pending: { resolve: (r: Response) => void; signal: AbortSignal }[] = [];
  const { w, state } = setup(
    (_: string, options: RequestInit) =>
      new Promise<Response>((resolve) => {
        pending.push({ resolve, signal: options.signal as AbortSignal });
      }),
  );
  state.identity.value = { ...state.identity.value!, tenant_code: "ten_two" };
  await flushPromises();
  expect(pending[0].signal.aborted).toBe(true);
  pending[1].resolve(response(snapshot(2)));
  await flushPromises();
  pending[0].resolve(response(snapshot(9)));
  await flushPromises();
  expect(w.findAll(".diagnostic-metric strong")[0].text()).toBe("2");
  await w.get("button").trigger("click");
  w.unmount();
  expect(pending[2].signal.aborted).toBe(true);
});
it("clears samples when task or manager permissions are revoked", async () => {
  const { w, state } = setup(async () => response(snapshot(3)));
  await flushPromises();
  expect(w.find(".diagnostic-metrics").exists()).toBe(true);
  state.identity.value = { ...state.identity.value!, permissions: [] };
  await flushPromises();
  expect(w.text()).toContain("没有此页面的访问权限");
  expect(w.find(".diagnostic-metrics").exists()).toBe(false);
});
it("distinguishes unavailable probes and renders anomalies as task navigation", async () => {
  const data = snapshot();
  const task = {
    task_code: "tsk_issue",
    title: "长名称与租约异常",
    reason: "LEASE_EXPIRED",
    observed_at: data.sampled_at,
  };
  const { w, state } = setup(async () =>
    response({
      ...data,
      database: { status: "unavailable" },
      workers: {
        ready: false,
        active: 0,
        stale: 2,
        invalid: 1,
        foreign: 3,
        unobservable: 0,
        truncated: true,
      },
      queue: { ...data.queue, expired_leases: 1, samples: [task] },
    }),
  );
  await flushPromises();
  expect(w.text()).toContain("数据库状态未知");
  expect(w.text()).toContain("未观测到就绪 Worker");
  expect(w.text()).toContain("统计不完整");
  await w
    .findAll("button")
    .find((b) => b.text() === "查看任务")!
    .trigger("click");
  expect(state.go).toHaveBeenCalledWith("tasks/tsk_issue");
});
it("does not render unknown queue values as zero", async () => {
  const { w } = setup(async () =>
    response({ ...snapshot(), queue: { status: "unavailable" } }),
  );
  await flushPromises();
  expect(w.text()).toContain("队列状态未知");
  expect(w.find(".diagnostic-metric").exists()).toBe(false);
});
it("shows shared-database workers and confirms only the observed alert", async () => {
  let ack = false;
  const { w, fetchMock } = setup(async (path, options) => {
    if (options?.method === "POST") {
      ack = true;
      return response({ acknowledged: true });
    }
    return response({
      ...snapshot(),
      cluster_workers: {
        status: "ready",
        active: 0,
        stale: 2,
        threshold_seconds: 45,
      },
      alert_status: "ready",
      alerts: [
        {
          fingerprint: "a".repeat(64),
          message: "Worker 心跳缺失",
          acknowledged: ack,
        },
      ],
    });
  });
  await flushPromises();
  expect(w.text()).toContain("数据库 Worker 心跳");
  await w
    .findAll("button")
    .find((b) => b.text() === "确认已查看")!
    .trigger("click");
  await flushPromises();
  expect(w.text()).toContain("本账号已确认");
  expect(
    fetchMock.mock.calls.find((c) => c[1]?.method === "POST")![0],
  ).toContain("/alerts/" + "a".repeat(64) + "/ack");
});
