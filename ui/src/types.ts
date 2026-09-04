export type Page = "traffic" | "usage" | "proxy" | "info";

export function hrefForPage(page: Page): string {
  if (page === "usage") return "/usage";
  if (page === "proxy") return "/proxy";
  if (page === "info") return "/info";
  return "/";
}

export function pageFromPath(pathname: string): Page {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/usage") return "usage";
  if (path === "/proxy") return "proxy";
  if (path === "/info") return "info";
  return "traffic";
}
export type DetailTab = "chat" | "system" | "tools" | "json";
export type UsagePeriod = "today" | "week" | "30days" | "month" | "all" | "lifetime";
export type SourceFilter = "" | "proxy" | "direct";

export type Session = {
  client: string;
  id: string;
  request_count: number;
  ts?: string | null;
  costUSD?: number;
  source: "proxy" | "direct";
  project?: string | null;
  families?: string[];
};

export type TrafficRecord = {
  ts?: string;
  method?: string;
  url?: string;
  status?: number;
  costUSD?: number;
  request?: { body?: unknown };
  response?: { body?: unknown };
};

export type HeldHead = {
  id: string;
  session_id: string;
  method?: string;
  url?: string;
  body?: string;
};

export type HeldQueueItem = {
  id: string;
  session_id: string;
  method?: string;
  url?: string;
};

export type LiveSession = {
  client: string;
  id: string;
};

export type ProxySnapshot = {
  proxy: boolean;
  intercept: boolean;
  error?: string | null;
  head?: HeldHead | null;
  queue: HeldQueueItem[];
  listen?: string | null;
  ca?: string | null;
  sniffed?: boolean;
  live?: LiveSession[];
};

export type UsageProject = {
  name: string;
  costUSD: number;
  sessions: number;
  avgCostPerSession: number;
};

export type UsageGrain = "hour" | "day" | "month";

export type UsageSlice = {
  name: string;
  costUSD: number;
};

export type UsagePoint = {
  t: string;
  costUSD: number;
  calls: number;
  projects: UsageSlice[];
};

export type UsageRollup = {
  label: string;
  costUSD: number;
  calls: number;
  sessions: number;
  projects: UsageProject[];
  projectNames: string[];
  granularity: UsageGrain;
  series: UsagePoint[];
};

export const EMPTY_PROXY: ProxySnapshot = {
  proxy: false,
  intercept: false,
  error: null,
  head: null,
  queue: [],
  listen: null,
  ca: null,
  sniffed: false,
  live: [],
};

export const EMPTY_USAGE: UsageRollup = {
  label: "",
  costUSD: 0,
  calls: 0,
  sessions: 0,
  projects: [],
  projectNames: [],
  granularity: "day",
  series: [],
};
