import { describe, expect, test } from "bun:test";
import { createMemoryAdapter, createMonitor } from "./monitor";
import { EMPTY_PROXY, hrefForPage, pageFromPath, type Session, type TrafficRecord } from "./types";

function session(id: string, extra?: Partial<Session>): Session {
  return { client: "c", id, request_count: 1, source: "proxy", ...extra };
}

function rec(status: number): TrafficRecord {
  return { status };
}

describe("monitor", () => {
  test("tick 1 loads control, sessions, and records and selects the tail", async () => {
    const { adapter, calls } = createMemoryAdapter({
      sessions: [session("a")],
      records: { "c/a": [rec(200), rec(201)] },
      proxy: { ...EMPTY_PROXY, listen: "http://127.0.0.1:9090" },
    });
    const m = createMonitor(adapter);
    await m.tick();
    const s = m.snapshot();
    expect(calls.getProxy).toBe(1);
    expect(calls.listSessions).toBe(1);
    expect(calls.sessionRecords).toBe(1);
    expect(s.proxy.listen).toBe("http://127.0.0.1:9090");
    expect(s.selected).toBe("c/a");
    expect(s.records).toHaveLength(2);
    expect(s.index).toBe(1);
  });

  test("odd ticks skip session reload; even ticks do not", async () => {
    const { adapter, calls, data } = createMemoryAdapter({
      sessions: [session("a")],
      records: { "c/a": [rec(200)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    await m.tick();
    const n = calls.listSessions;
    data.sessions = [session("a"), session("b")];
    await m.tick();
    expect(calls.listSessions).toBe(n);
    expect(m.snapshot().sessions).toHaveLength(1);
    await m.tick();
    expect(calls.listSessions).toBe(n + 1);
    expect(m.snapshot().sessions).toHaveLength(2);
  });

  test("intercept on at tick start skips session reload even on tick 1", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.toggleIntercept(true);
    await m.tick();
    expect(calls.listSessions).toBe(0);
    expect(calls.getProxy).toBe(1);
  });

  test("usage page loads usage on enter and not on tick", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.setPage("usage");
    const usageCalls = calls.usage;
    expect(usageCalls).toBe(1);
    await m.tick();
    await m.tick();
    expect(calls.usage).toBe(usageCalls);
    expect(calls.listSessions).toBe(0);
    expect(calls.getProxy).toBe(2);
  });

  test("a newer usage query wins over an in-flight one", async () => {
    const memory = createMemoryAdapter();
    const pending: { query: { period: string }; resolve: (value: unknown) => void }[] = [];
    const m = createMonitor({
      ...memory.adapter,
      usage: (query) =>
        new Promise((resolve) => {
          pending.push({ query, resolve });
        }),
    });
    const first = m.setPage("usage");
    const second = m.setUsagePeriod("today");
    expect(pending).toHaveLength(2);
    pending[0]!.resolve({
      label: "week",
      costUSD: 9,
      calls: 9,
      sessions: 9,
      projects: [],
      projectNames: [],
      granularity: "day",
      series: [],
    });
    pending[1]!.resolve({
      label: "Today",
      costUSD: 1.5,
      calls: 4,
      sessions: 2,
      projects: [],
      projectNames: [],
      granularity: "hour",
      series: [],
    });
    await first;
    await second;
    expect(m.snapshot().usage.calls).toBe(4);
    expect(m.snapshot().usagePeriod).toBe("today");
  });

  test("unchanged control does not re-emit", async () => {
    const { adapter } = createMemoryAdapter({
      proxy: { ...EMPTY_PROXY, listen: "http://127.0.0.1:9090" },
    });
    const m = createMonitor(adapter);
    const seen: unknown[] = [];
    m.subscribe((s) => seen.push(s));
    await m.setPage("proxy");
    await m.tick();
    const n = seen.length;
    await m.tick();
    await m.tick();
    expect(seen.length).toBe(n);
  });

  test("proxy page shows only live sniffed sessions, not catalog history", async () => {
    const { adapter, calls } = createMemoryAdapter({
      sessions: [session("old"), session("now")],
      records: { "c/old": [rec(200)], "c/now": [rec(201)] },
      proxy: { ...EMPTY_PROXY, proxy: true, live: [{ client: "c", id: "now" }] },
    });
    const m = createMonitor(adapter);
    await m.setPage("proxy");
    expect(calls.listSessions).toBe(1);
    expect(m.snapshot().sessions.map((s) => s.id)).toEqual(["now"]);
    expect(m.snapshot().selected).toBe("c/now");
    expect(m.snapshot().records[0]?.status).toBe(201);
    await m.tick();
    expect(calls.listSessions).toBe(2);
    expect(calls.getProxy).toBe(2);
  });

  test("sessions page still shows catalog history while proxy is live", async () => {
    const { adapter } = createMemoryAdapter({
      sessions: [session("old"), session("now")],
      records: { "c/old": [rec(200)], "c/now": [rec(201)] },
      proxy: { ...EMPTY_PROXY, proxy: true, live: [{ client: "c", id: "now" }] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    expect(m.snapshot().sessions.map((s) => s.id)).toEqual(["old", "now"]);
    expect(m.snapshot().selected).toBe("c/old");
  });

  test("proxy page with intercept on still loads live sessions", async () => {
    const { adapter, calls } = createMemoryAdapter({
      sessions: [session("old"), session("now")],
      records: { "c/now": [rec(201)] },
      proxy: { ...EMPTY_PROXY, proxy: true, live: [{ client: "c", id: "now" }] },
    });
    const m = createMonitor(adapter);
    await m.toggleIntercept(true);
    await m.setPage("proxy");
    await m.tick();
    expect(calls.listSessions).toBeGreaterThan(0);
    expect(calls.getProxy).toBe(2);
    expect(m.snapshot().sessions.map((s) => s.id)).toEqual(["now"]);
    expect(calls.usage).toBe(0);
  });

  test("proxy page reloads live sessions every tick", async () => {
    const { adapter, calls, data } = createMemoryAdapter({
      sessions: [session("a")],
      proxy: { ...EMPTY_PROXY, proxy: true, live: [{ client: "c", id: "a" }] },
    });
    const m = createMonitor(adapter);
    await m.setPage("proxy");
    const n = calls.listSessions;
    await m.tick();
    await m.tick();
    expect(calls.listSessions).toBe(n + 2);
    data.proxy.live = [{ client: "c", id: "b" }];
    data.sessions = [session("a"), session("b")];
    await m.tick();
    expect(m.snapshot().sessions.map((s) => s.id)).toEqual(["b"]);
  });

  test("proxy off on the proxy page hides catalog history", async () => {
    const { adapter, calls } = createMemoryAdapter({
      sessions: [session("a")],
      records: { "c/a": [rec(200)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    expect(m.snapshot().sessions).toHaveLength(1);
    await m.setPage("proxy");
    expect(calls.listSessions).toBe(1);
    expect(m.snapshot().sessions).toEqual([]);
    expect(m.snapshot().records).toEqual([]);
    expect(m.snapshot().selected).toBe(null);
  });

  test("stale control responses are ignored", async () => {
    const memory = createMemoryAdapter();
    const pending: ((value: typeof EMPTY_PROXY) => void)[] = [];
    const m = createMonitor({
      ...memory.adapter,
      getProxy: () =>
        new Promise((resolve) => {
          pending.push(resolve);
        }),
    });
    const first = m.tick();
    const second = m.tick();
    pending[1]!({ ...EMPTY_PROXY, listen: "http://new" });
    pending[0]!({ ...EMPTY_PROXY, sniffed: true });
    await first;
    await second;
    expect(m.snapshot().proxy.listen).toBe("http://new");
    expect(m.snapshot().proxy.sniffed).toBe(false);
  });

  test("selectRequest off the tail pins the index when new records arrive", async () => {
    const { adapter, data } = createMemoryAdapter({
      sessions: [session("a")],
      records: { "c/a": [rec(200), rec(201)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    m.selectRequest(0);
    data.records["c/a"] = [rec(200), rec(201), rec(202)];
    await m.tick();
    expect(m.snapshot().records).toHaveLength(3);
    expect(m.snapshot().index).toBe(0);
  });

  test("selectRequest on the tail follows new records", async () => {
    const { adapter, data } = createMemoryAdapter({
      sessions: [session("a")],
      records: { "c/a": [rec(200), rec(201)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    m.selectRequest(1);
    data.records["c/a"] = [rec(200), rec(201), rec(202)];
    await m.tick();
    expect(m.snapshot().index).toBe(2);
  });

  test("a vanished session falls back to the first and reloads records", async () => {
    const { adapter, data } = createMemoryAdapter({
      sessions: [session("a"), session("b")],
      records: { "c/a": [rec(200)], "c/b": [rec(500)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    await m.selectSession("c/b");
    expect(m.snapshot().records[0]?.status).toBe(500);
    data.sessions = [session("a")];
    await m.tick();
    expect(m.snapshot().selected).toBe("c/a");
    expect(m.snapshot().records[0]?.status).toBe(200);
  });

  test("toggleProxy writes control then reloads sessions", async () => {
    const { adapter, calls } = createMemoryAdapter({ sessions: [session("a")] });
    const m = createMonitor(adapter);
    await m.toggleProxy(true);
    expect(m.snapshot().proxy.proxy).toBe(true);
    expect(calls.setProxy).toBe(1);
    expect(calls.listSessions).toBe(1);
  });

  test("openProject copies the usage query and does not fetch sessions", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.setPage("usage");
    await m.setSource("direct");
    await m.setModel("sonnet-4.6");
    const n = calls.listSessions;
    m.openProject("repo");
    const s = m.snapshot();
    expect(s.page).toBe("traffic");
    expect(s.project).toBe("repo");
    expect(s.source).toBe("direct");
    expect(s.model).toBe("sonnet-4.6");
    expect(calls.listSessions).toBe(n);
  });

  test("setPage traffic from usage fetches sessions", async () => {
    const { adapter, calls } = createMemoryAdapter({ sessions: [session("a")] });
    const m = createMonitor(adapter);
    await m.setPage("usage");
    const n = calls.listSessions;
    await m.setPage("traffic");
    expect(calls.listSessions).toBe(n + 1);
    expect(m.snapshot().selected).toBe("c/a");
  });

  test("usage query changes fetch only while on the usage page", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.setUsagePeriod("today");
    expect(calls.usage).toBe(0);
    await m.setPage("usage");
    const n = calls.usage;
    await m.setUsagePeriod("month");
    expect(calls.usage).toBe(n + 1);
    expect(m.snapshot().usagePeriod).toBe("month");
  });

  test("filtering out the selected session selects the first visible", async () => {
    const { adapter } = createMemoryAdapter({
      sessions: [session("a", { project: "one" }), session("b", { project: "two" })],
      records: { "c/a": [rec(200)], "c/b": [rec(500)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    expect(m.snapshot().selected).toBe("c/a");
    await m.setProject("two");
    expect(m.snapshot().selected).toBe("c/b");
    expect(m.snapshot().records[0]?.status).toBe(500);
  });

  test("filtering to nothing clears the selection", async () => {
    const { adapter } = createMemoryAdapter({
      sessions: [session("a", { project: "one" })],
      records: { "c/a": [rec(200)] },
    });
    const m = createMonitor(adapter);
    await m.tick();
    await m.setProject("missing");
    expect(m.snapshot().selected).toBe(null);
    expect(m.snapshot().records).toEqual([]);
  });

  test("project filter fetches usage only on the usage page", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.setPage("usage");
    const n = calls.usage;
    await m.setProject("repo");
    expect(calls.usage).toBe(n + 1);
    expect(m.snapshot().project).toBe("repo");
    await m.setPage("traffic");
    await m.setProject("lab");
    expect(calls.usage).toBe(n + 1);
    expect(m.snapshot().project).toBe("lab");
  });

  test("source and model stay shared after leaving usage", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.setPage("usage");
    const n = calls.usage;
    await m.setSource("direct");
    await m.setModel("opus-4.7");
    expect(calls.usage).toBe(n + 2);
    await m.setPage("traffic");
    await m.setSource("proxy");
    expect(calls.usage).toBe(n + 2);
    expect(m.snapshot().source).toBe("proxy");
    expect(m.snapshot().model).toBe("opus-4.7");
  });

  test("forward and drop hit the adapter", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter);
    await m.forward("{}");
    await m.drop();
    expect(calls.forward).toBe(1);
    expect(calls.drop).toBe(1);
  });

  test("starts on the given page", async () => {
    const { adapter, calls } = createMemoryAdapter();
    const m = createMonitor(adapter, "usage");
    expect(m.snapshot().page).toBe("usage");
    expect(calls.usage).toBe(0);
    await m.setPage("usage");
    expect(calls.usage).toBe(1);
  });
});

describe("routes", () => {
  test("hrefForPage maps pages to paths", () => {
    expect(hrefForPage("traffic")).toBe("/");
    expect(hrefForPage("usage")).toBe("/usage");
    expect(hrefForPage("proxy")).toBe("/proxy");
    expect(hrefForPage("info")).toBe("/info");
  });

  test("pageFromPath maps paths to pages", () => {
    expect(pageFromPath("/")).toBe("traffic");
    expect(pageFromPath("/usage")).toBe("usage");
    expect(pageFromPath("/usage/")).toBe("usage");
    expect(pageFromPath("/proxy")).toBe("proxy");
    expect(pageFromPath("/proxy/")).toBe("proxy");
    expect(pageFromPath("/info")).toBe("info");
    expect(pageFromPath("/info/")).toBe("info");
    expect(pageFromPath("/sessions")).toBe("traffic");
  });
});
