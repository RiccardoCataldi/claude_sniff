import type { Session, SourceFilter, TrafficRecord, UsagePeriod } from "./types";

export const NO_PROJECT = "(no project)";

export const SOURCE_FILTERS: { value: SourceFilter; label: string }[] = [
  { value: "", label: "All" },
  { value: "proxy", label: "proxy" },
  { value: "direct", label: "direct" },
];

export const MODEL_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All models" },
  { value: "sonnet-4.6", label: "Sonnet" },
  { value: "opus-4.7", label: "Opus" },
  { value: "haiku-4.5", label: "Haiku" },
];

export const PERIOD_FILTERS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "7 Days" },
  { value: "30days", label: "30 Days" },
  { value: "month", label: "This Month" },
  { value: "all", label: "6 Months" },
  { value: "lifetime", label: "Lifetime" },
];

export function periodCutoff(period: UsagePeriod): Date | null {
  const now = new Date();
  if (period === "lifetime") return null;
  if (period === "today") return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (period === "week") return new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  if (period === "30days") return new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
  if (period === "month") return new Date(now.getFullYear(), now.getMonth(), 1);
  if (period === "all") return new Date(now.getTime() - 6 * 30 * 24 * 60 * 60 * 1000);
  return null;
}

export function matchesSession(
  session: Session,
  source: SourceFilter,
  project: string | null,
  model: string,
  period?: UsagePeriod,
): boolean {
  if (source && session.source !== source) return false;
  if (project != null) {
    if (project === NO_PROJECT) {
      if (session.project) return false;
    } else if (session.project !== project) return false;
  }
  if (model && !(session.families || []).includes(model)) return false;
  if (period) {
    const cutoff = periodCutoff(period);
    if (cutoff && session.ts && new Date(session.ts) < cutoff) return false;
  }
  return true;
}

export function sessionKey(session: Session): string {
  return `${session.client}/${session.id}`;
}

export function clock(ts: string | null | undefined): string {
  if (!ts || typeof ts !== "string" || ts.length < 19) return "";
  return ts.slice(11, 19);
}

export function shortId(id: string | undefined): string {
  if (!id) return "session";
  const tail = id.split("-").pop() || id;
  return tail.length <= 12 ? tail : tail.slice(-8);
}

export function usd(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

export function pathOf(url: string | undefined): string {
  if (!url) return "";
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    return (parts.slice(-1)[0] || u.pathname) + u.search;
  } catch {
    return url;
  }
}

export function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function bodyOf(rec: TrafficRecord): Record<string, unknown> {
  return asObject(rec.request?.body);
}

export function respOf(rec: TrafficRecord): Record<string, unknown> {
  return asObject(rec.response?.body);
}

export function modelOf(rec: TrafficRecord): string {
  const model = respOf(rec).model || bodyOf(rec).model;
  return typeof model === "string" ? model : "";
}

export function usageOf(rec: TrafficRecord): string {
  const u = asObject(respOf(rec).usage);
  const inn = u.input_tokens ?? u.prompt_tokens;
  const out = u.output_tokens ?? u.completion_tokens;
  const bits: string[] = [];
  if (inn != null || out != null) {
    const total = (Number(inn) || 0) + (Number(out) || 0);
    if (inn != null) bits.push(`In: ${inn}`);
    if (out != null) bits.push(`Out: ${out}`);
    bits.push(`Total: ${total.toLocaleString("en-US")}`);
  }
  bits.push(`Cost: ${usd(rec.costUSD)}`);
  return bits.join("  |  ");
}

export function systemText(body: Record<string, unknown>): string {
  const s = body.system;
  if (typeof s === "string") return s;
  if (!Array.isArray(s)) return "";
  return s
    .map((block) => (typeof block === "string" ? block : asObject(block).text))
    .filter((text): text is string => typeof text === "string" && Boolean(text))
    .join("\n\n");
}

export type ContentPart = { kind: "text"; text: string } | { kind: "json"; label: string; value: unknown };

export function contentParts(content: unknown): ContentPart[] {
  if (typeof content === "string") return content ? [{ kind: "text", text: content }] : [];
  if (!Array.isArray(content)) return [];
  const parts: ContentPart[] = [];
  for (const block of content) {
    if (typeof block === "string") {
      if (block) parts.push({ kind: "text", text: block });
      continue;
    }
    const obj = asObject(block);
    if (typeof obj.text === "string" && obj.text) parts.push({ kind: "text", text: obj.text });
    else if (obj.type === "tool_use") {
      const name = typeof obj.name === "string" ? obj.name : "";
      parts.push({ kind: "json", label: name ? `TOOL USE ${name}` : "TOOL USE", value: obj.input ?? {} });
    } else if (obj.type === "tool_result") {
      if (typeof obj.content === "string") {
        if (obj.content) parts.push({ kind: "text", text: obj.content });
      } else {
        parts.push({ kind: "json", label: "TOOL RESULT", value: obj.content ?? "" });
      }
    } else if (typeof obj.thinking === "string" && obj.thinking) {
      parts.push({ kind: "text", text: obj.thinking });
    }
  }
  return parts;
}

export function envBlock(listen: string | null | undefined, ca: string | null | undefined): string {
  const proxy = listen || "http://127.0.0.1:9090";
  const cert = ca || "$HOME/.mitmproxy/mitmproxy-ca-cert.pem";
  return [
    `export HTTP_PROXY=${proxy}`,
    `export HTTPS_PROXY=${proxy}`,
    `export NODE_EXTRA_CA_CERTS=${cert}`,
    `export SSL_CERT_FILE=${cert}`,
    `export REQUESTS_CA_BUNDLE=${cert}`,
    `export AWS_CA_BUNDLE=${cert}`,
    `claude`,
  ].join("\n");
}
