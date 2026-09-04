import type { MonitorAdapter, UsageQuery } from "./monitor";
import type { ProxySnapshot, Session, TrafficRecord, UsageRollup } from "./types";

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { cache: "no-store", signal });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(url: string, payload?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload ?? {}),
  });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json() as Promise<T>;
}

export function createHttpAdapter(): MonitorAdapter {
  return {
    getProxy: () => fetchJson<ProxySnapshot>("/api/proxy"),
    setProxy: (patch) => postJson<ProxySnapshot>("/api/proxy", patch),
    async listSessions() {
      const data = await fetchJson<{ sessions?: Session[] }>("/api/sessions");
      return data.sessions || [];
    },
    async sessionRecords(key: string) {
      const data = await fetchJson<{ records?: TrafficRecord[] }>(`/api/sessions/${key}`);
      return data.records || [];
    },
    usage: (query: UsageQuery, signal?: AbortSignal) => {
      const params = new URLSearchParams({ period: query.period });
      if (query.source) params.set("source", query.source);
      if (query.model) params.set("model", query.model);
      if (query.project) params.set("project", query.project);
      return fetchJson<UsageRollup>(`/api/usage?${params}`, signal);
    },
    forward: (body: string) => postJson<ProxySnapshot>("/api/intercept/forward", { body }),
    drop: () => postJson<ProxySnapshot>("/api/intercept/drop"),
  };
}
