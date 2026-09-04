import { matchesSession, NO_PROJECT, sessionKey } from "./format";
import {
  EMPTY_PROXY,
  EMPTY_USAGE,
  type DetailTab,
  type Page,
  type ProxySnapshot,
  type Session,
  type SourceFilter,
  type TrafficRecord,
  type UsagePeriod,
  type UsageRollup,
} from "./types";

export type UsageQuery = {
  period: UsagePeriod;
  source: SourceFilter;
  model: string;
  project: string | null;
};

export type MonitorAdapter = {
  getProxy: () => Promise<ProxySnapshot>;
  setProxy: (patch: { proxy?: boolean; intercept?: boolean }) => Promise<ProxySnapshot>;
  listSessions: () => Promise<Session[]>;
  sessionRecords: (key: string) => Promise<TrafficRecord[]>;
  usage: (query: UsageQuery, signal?: AbortSignal) => Promise<UsageRollup>;
  forward: (body: string) => Promise<ProxySnapshot>;
  drop: () => Promise<ProxySnapshot>;
};

export type MonitorSnapshot = {
  page: Page;
  proxy: ProxySnapshot;
  sessions: Session[];
  selected: string | null;
  records: TrafficRecord[];
  index: number;
  tab: DetailTab;
  source: SourceFilter;
  project: string | null;
  model: string;
  usagePeriod: UsagePeriod;
  usage: UsageRollup;
};

function coerceLive(data: ProxySnapshot): { client: string; id: string }[] {
  if (!Array.isArray(data.live)) return [];
  const out: { client: string; id: string }[] = [];
  for (const item of data.live) {
    if (!item || !item.client || !item.id) continue;
    out.push({ client: item.client, id: item.id });
  }
  return out;
}

function coerceProxy(data: ProxySnapshot): ProxySnapshot {
  return {
    proxy: !!data.proxy,
    intercept: !!data.intercept,
    error: data.error || null,
    head: data.head || null,
    queue: data.queue || [],
    listen: data.listen || null,
    ca: data.ca || null,
    sniffed: !!data.sniffed,
    live: coerceLive(data),
  };
}

function sameSessionList(a: Session[], b: Session[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (
      x.client !== y.client ||
      x.id !== y.id ||
      x.request_count !== y.request_count ||
      x.ts !== y.ts ||
      x.costUSD !== y.costUSD ||
      x.source !== y.source ||
      x.project !== y.project
    ) {
      return false;
    }
  }
  return true;
}

function sameLive(
  a: { client: string; id: string }[],
  b: { client: string; id: string }[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].client !== b[i].client || a[i].id !== b[i].id) return false;
  }
  return true;
}

function sameProxy(a: ProxySnapshot, b: ProxySnapshot): boolean {
  if (
    a.proxy !== b.proxy ||
    a.intercept !== b.intercept ||
    a.error !== b.error ||
    a.listen !== b.listen ||
    a.ca !== b.ca ||
    a.sniffed !== b.sniffed ||
    a.queue.length !== b.queue.length
  ) {
    return false;
  }
  if ((a.head?.id ?? null) !== (b.head?.id ?? null) || (a.head?.body || "") !== (b.head?.body || "")) {
    return false;
  }
  for (let i = 0; i < a.queue.length; i++) {
    if (a.queue[i].id !== b.queue[i].id) return false;
  }
  return sameLive(a.live || [], b.live || []);
}

export function createMemoryAdapter(init?: {
  proxy?: ProxySnapshot;
  sessions?: Session[];
  records?: Record<string, TrafficRecord[]>;
  usage?: UsageRollup;
}) {
  const data = {
    proxy: coerceProxy(init?.proxy ?? EMPTY_PROXY),
    sessions: init?.sessions ?? [],
    records: { ...(init?.records ?? {}) },
    usage: init?.usage ?? EMPTY_USAGE,
  };
  const calls = {
    getProxy: 0,
    setProxy: 0,
    listSessions: 0,
    sessionRecords: 0,
    usage: 0,
    forward: 0,
    drop: 0,
  };
  const adapter: MonitorAdapter = {
    async getProxy() {
      calls.getProxy += 1;
      return coerceProxy(data.proxy);
    },
    async setProxy(patch) {
      calls.setProxy += 1;
      data.proxy = coerceProxy({ ...data.proxy, ...patch });
      return coerceProxy(data.proxy);
    },
    async listSessions() {
      calls.listSessions += 1;
      return data.sessions.slice();
    },
    async sessionRecords(key) {
      calls.sessionRecords += 1;
      return (data.records[key] ?? []).slice();
    },
    async usage() {
      calls.usage += 1;
      return {
        ...data.usage,
        projects: data.usage.projects.slice(),
        projectNames: (data.usage.projectNames ?? []).slice(),
        series: data.usage.series.map((p) => ({
          ...p,
          projects: (p.projects ?? []).map((row) => ({ ...row })),
        })),
      };
    },
    async forward() {
      calls.forward += 1;
      return coerceProxy(data.proxy);
    },
    async drop() {
      calls.drop += 1;
      return coerceProxy(data.proxy);
    },
  };
  return { adapter, data, calls };
}

export function createMonitor(adapter: MonitorAdapter, initialPage: Page = "traffic") {
  let page: Page = initialPage;
  let proxy = EMPTY_PROXY;
  let sessions: Session[] = [];
  let selected: string | null = null;
  let records: TrafficRecord[] = [];
  let index = -1;
  let tab: DetailTab = "chat";
  let source: SourceFilter = "";
  let project: string | null = null;
  let model = "";
  let usagePeriod: UsagePeriod = "week";
  let usage = EMPTY_USAGE;
  let followTail = true;
  let ticks = 0;
  const gen = { proxy: 0, sessions: 0, records: 0, usage: 0 };
  let usageAbort: AbortController | null = null;
  const listeners = new Set<(s: MonitorSnapshot) => void>();

  function snapshot(): MonitorSnapshot {
    return {
      page,
      proxy,
      sessions,
      selected,
      records,
      index,
      tab,
      source,
      project,
      model,
      usagePeriod,
      usage,
    };
  }

  function emit() {
    const s = snapshot();
    for (const fn of listeners) fn(s);
  }

  function syncSelection(): boolean {
    const match = page === "proxy" ? () => true : (s: Session) => matchesSession(s, source, project, model);
    const still = selected != null && sessions.some((s) => sessionKey(s) === selected && match(s));
    if (still) return false;
    const next = sessions.find(match);
    const nextKey = next ? sessionKey(next) : null;
    if (nextKey === selected) return false;
    selected = nextKey;
    followTail = true;
    index = -1;
    records = [];
    return true;
  }

  function applyProxy(data: ProxySnapshot) {
    const next = coerceProxy(data);
    if (sameProxy(proxy, next)) return;
    proxy = next;
    emit();
  }

  async function loadProxy() {
    const my = ++gen.proxy;
    const data = await adapter.getProxy();
    if (my !== gen.proxy) return;
    applyProxy(data);
  }

  function liveSessions(list: Session[]): Session[] {
    if (!proxy.proxy) return [];
    const keys = proxy.live || [];
    if (!keys.length) return [];
    const byKey = new Map(list.map((s) => [sessionKey(s), s]));
    const out: Session[] = [];
    for (const item of keys) {
      const found = byKey.get(`${item.client}/${item.id}`);
      if (found) out.push(found);
    }
    return out;
  }

  async function loadSessions() {
    const my = ++gen.sessions;
    if (page === "proxy" && (!proxy.proxy || !(proxy.live || []).length)) {
      if (my !== gen.sessions) return;
      const prev = sessions;
      sessions = [];
      const moved = syncSelection();
      if (!moved && sameSessionList(prev, [])) return;
      emit();
      return;
    }
    const list = await adapter.listSessions();
    if (my !== gen.sessions) return;
    const prev = sessions;
    const next = page === "proxy" ? liveSessions(list) : list;
    sessions = next;
    const moved = syncSelection();
    if (!moved && sameSessionList(prev, next)) return;
    emit();
  }

  async function loadRecords() {
    const key = selected;
    const my = ++gen.records;
    if (!key) {
      if (my !== gen.records) return;
      if (!records.length && index === -1) return;
      records = [];
      index = -1;
      emit();
      return;
    }
    const recs = await adapter.sessionRecords(key);
    if (my !== gen.records) return;
    let nextIndex = index;
    if (!recs.length) nextIndex = -1;
    else if (followTail || index < 0 || index >= recs.length) nextIndex = recs.length - 1;
    if (recs.length === records.length && nextIndex === index) return;
    records = recs;
    index = nextIndex;
    emit();
  }

  async function loadUsage() {
    usageAbort?.abort();
    const ac = new AbortController();
    usageAbort = ac;
    const my = ++gen.usage;
    const q = { period: usagePeriod, source, model, project };
    try {
      const data = await adapter.usage(q, ac.signal);
      if (my !== gen.usage) return;
      usage = data;
      emit();
    } catch (err) {
      if (ac.signal.aborted) return;
      console.warn(err);
    }
  }

  async function tick() {
    ticks += 1;
    const currentPage = page;
    const intercept = proxy.intercept;
    try {
      await loadProxy();
    } catch (err) {
      console.warn(err);
    }
    const sniffing = currentPage === "proxy";
    const catalog = currentPage === "traffic" && !intercept;
    if (!sniffing && !catalog) return;
    if (catalog && ticks % 2 !== 0 && ticks !== 1) return;
    try {
      await loadSessions();
      await loadRecords();
    } catch (err) {
      console.warn(err);
    }
  }

  async function loadUsageIfOnUsagePage() {
    if (page !== "usage") return;
    try {
      await loadUsage();
    } catch (err) {
      console.warn(err);
    }
  }

  return {
    snapshot,
    subscribe(fn: (s: MonitorSnapshot) => void) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    tick,
    async selectSession(key: string) {
      selected = key;
      followTail = true;
      index = -1;
      emit();
      await loadRecords();
    },
    selectRequest(i: number) {
      followTail = i === records.length - 1;
      index = i;
      emit();
    },
    setTab(next: DetailTab) {
      tab = next;
      emit();
    },
    async setSource(next: SourceFilter) {
      source = next;
      const reload = syncSelection();
      emit();
      if (reload) await loadRecords();
      await loadUsageIfOnUsagePage();
    },
    async setProject(next: string | null) {
      project = next;
      const reload = syncSelection();
      emit();
      if (reload) await loadRecords();
      await loadUsageIfOnUsagePage();
    },
    async setModel(next: string) {
      model = next;
      const reload = syncSelection();
      emit();
      if (reload) await loadRecords();
      await loadUsageIfOnUsagePage();
    },
    async setPage(next: Page) {
      if (next !== page) {
        page = next;
        emit();
      } else if (next !== "usage") {
        return;
      }
      if (next === "usage") {
        await loadUsageIfOnUsagePage();
        return;
      }
      if (next === "proxy") {
        try {
          await loadProxy();
          await loadSessions();
          await loadRecords();
        } catch (err) {
          console.warn(err);
        }
        return;
      }
      if (next === "traffic") {
        try {
          await loadSessions();
          await loadRecords();
        } catch (err) {
          console.warn(err);
        }
      }
    },
    async setUsagePeriod(period: UsagePeriod) {
      usagePeriod = period;
      emit();
      await loadUsageIfOnUsagePage();
    },
    openProject(name: string) {
      project = name || NO_PROJECT;
      page = "traffic";
      const reload = syncSelection();
      emit();
      if (reload) void loadRecords();
    },
    async toggleProxy(on: boolean) {
      try {
        applyProxy(await adapter.setProxy({ proxy: on }));
        await loadSessions();
      } catch (err) {
        console.warn(err);
      }
    },
    async toggleIntercept(on: boolean) {
      try {
        applyProxy(await adapter.setProxy({ intercept: on }));
        if (page === "proxy" || (!on && page === "traffic")) {
          await loadSessions();
          await loadRecords();
        }
      } catch (err) {
        console.warn(err);
      }
    },
    async forward(body: string) {
      try {
        applyProxy(await adapter.forward(body));
      } catch (err) {
        console.warn(err);
      }
    },
    async drop() {
      try {
        applyProxy(await adapter.drop());
      } catch (err) {
        console.warn(err);
      }
    },
  };
}

export type Monitor = ReturnType<typeof createMonitor>;
