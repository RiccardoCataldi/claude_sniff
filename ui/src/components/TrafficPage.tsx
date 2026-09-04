import { useMemo, type ReactNode } from "react";
import Markdown from "react-markdown";
import { JsonTree } from "./JsonEditor";
import { Select } from "./Pills";
import {
  bodyOf,
  clock,
  contentParts,
  matchesSession,
  MODEL_FILTERS,
  modelOf,
  NO_PROJECT,
  pathOf,
  PERIOD_FILTERS,
  respOf,
  sessionKey,
  shortId,
  SOURCE_FILTERS,
  systemText,
  usd,
  usageOf,
} from "../format";
import type { DetailTab, Session, SourceFilter, TrafficRecord, UsagePeriod } from "../types";

type Props = {
  live?: boolean;
  proxyOn: boolean;
  sessions: Session[];
  selected: string | null;
  records: TrafficRecord[];
  index: number;
  tab: DetailTab;
  source: SourceFilter;
  project: string | null;
  model: string;
  period: UsagePeriod;
  onSelectSession: (key: string) => void;
  onSelectRequest: (index: number) => void;
  onTab: (tab: DetailTab) => void;
  onSource: (source: SourceFilter) => void;
  onProject: (project: string | null) => void;
  onModel: (model: string) => void;
  onPeriod: (period: UsagePeriod) => void;
  onRefresh: () => void;
};

function projectOptions(sessions: Session[], current: string | null): string[] {
  const names = new Set<string>();
  let none = false;
  for (const s of sessions) {
    if (s.project) names.add(s.project);
    else none = true;
  }
  if (current === NO_PROJECT) none = true;
  else if (current) names.add(current);
  const list = [...names].sort((a, b) => a.localeCompare(b));
  if (none) list.push(NO_PROJECT);
  return list;
}

export function TrafficPage({
  live = false,
  proxyOn,
  sessions,
  selected,
  records,
  index,
  tab,
  source,
  project,
  model,
  period,
  onSelectSession,
  onSelectRequest,
  onTab,
  onSource,
  onProject,
  onModel,
  onPeriod,
  onRefresh,
}: Props) {
  const visible = useMemo(
    () => (live ? sessions : sessions.filter((s) => matchesSession(s, source, project, model, period))),
    [live, sessions, source, project, model, period],
  );
  const projects = useMemo(() => projectOptions(sessions, project), [sessions, project]);

  const sess = sessions.find((s) => sessionKey(s) === selected);
  const rec = records[index];
  const direct = sess?.source === "direct";

  return (
    <div id="page-traffic" className="page">
      <aside id="sessions-pane">
        <header>
          <span>{live ? "LIVE" : "SESSIONS"}</span>
          <button type="button" id="refresh" title="Refresh" aria-label="Refresh" onClick={onRefresh}>
            &#8635;
          </button>
        </header>
        {live ? null : (
          <div className="session-filters">
            <Select label="Period" value={period} options={PERIOD_FILTERS} onChange={onPeriod} />
            <Select label="Source" value={source} options={SOURCE_FILTERS} onChange={onSource} />
            <Select
              label="Project"
              value={project ?? ""}
              options={[
                { value: "", label: "All projects" },
                ...projects.map((name) => ({ value: name, label: name })),
              ]}
              onChange={(value) => onProject(value || null)}
            />
            <Select label="Model" value={model} options={MODEL_FILTERS} onChange={onModel} />
          </div>
        )}
        <div id="sessions">
          {!visible.length ? (
            <div className="empty">
              {live
                ? proxyOn
                  ? "Waiting for sniffed traffic."
                  : "Turn Proxy on to sniff."
                : proxyOn
                  ? "Waiting for proxy sessions. Configure exports in the Proxy page."
                  : "Direct costs appear here. Turn Proxy on in the header to capture."}
            </div>
          ) : (
            visible.map((s) => {
              const key = sessionKey(s);
              return (
                <div
                  key={key}
                  className={key === selected ? "session active" : "session"}
                  title={s.id}
                  onClick={() => onSelectSession(key)}
                >
                  <div className="session-top">
                    <div className="sid">{shortId(s.id)}</div>
                    <span className="cost">{usd(s.costUSD)}</span>
                  </div>
                  <div className="meta">
                    {s.source === "proxy" ? <span className="badge proxy">proxy</span> : null}
                    {s.project ? <span className="project">{s.project}</span> : null}
                    <span>{clock(s.ts)}</span>
                    <span className="n">{s.request_count}</span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </aside>
      <section id="requests-pane">
        <header>
          <span>REQUESTS</span>
          <span id="session-label">{sess ? sess.project || shortId(sess.id) : selected || ""}</span>
          <span id="req-count" className="count">
            {records.length ? String(records.length) : ""}
          </span>
        </header>
        <div id="requests">
          {!records.length ? (
            <div className="empty">
              {live
                ? proxyOn
                  ? "Waiting for sniffed traffic."
                  : "Turn Proxy on to sniff."
                : proxyOn
                  ? "Select a session"
                  : "Direct costs only. Turn Proxy on to capture chat."}
            </div>
          ) : (
            records.map((item, i) => {
              const status = item.status;
              const cls = [
                "request",
                i === index ? "active" : "",
                status != null && status >= 500 ? "err" : "",
                status != null && status >= 400 && status < 500 ? "warn" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const model = modelOf(item);
              const path = item.url ? pathOf(item.url) : "";
              return (
                <div key={i} className={cls} onClick={() => onSelectRequest(i)}>
                  <div className="idx">{String(i + 1).padStart(3, "0")}</div>
                  <div className="request-body">
                    <div className="request-line">
                      {path ? (
                        <>
                          <span className="method">{item.method || "POST"}</span>
                          <span className="path">{path}</span>
                        </>
                      ) : null}
                      {model ? <span className="model">{model}</span> : null}
                      <span className="cost">{usd(item.costUSD)}</span>
                    </div>
                    <div className="meta">
                      <span>{clock(item.ts)}</span>
                      {status != null ? (
                        <span className={status >= 400 ? "status bad" : "status"}>{status}</span>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </section>
      <main id="detail-pane">
        <header>
          <span id="detail-model">{rec ? modelOf(rec) : ""}</span>
          <span id="detail-usage">{rec ? usageOf(rec) : ""}</span>
          {!direct ? (
            <nav id="tabs">
              {(["chat", "system", "tools", "json"] as DetailTab[]).map((name) => (
                <button
                  key={name}
                  type="button"
                  className={tab === name ? "active" : undefined}
                  onClick={() => onTab(name)}
                >
                  {name === "chat" ? "Chat" : name === "system" ? "System" : name === "tools" ? "Tools" : "JSON"}
                </button>
              ))}
            </nav>
          ) : null}
        </header>
        {!rec ? (
          <div id="detail">
            <div className="empty">
              {live
                ? proxyOn
                  ? "Waiting for sniffed traffic."
                  : "Turn Proxy on to sniff."
                : proxyOn
                  ? "Select a request"
                  : "No proxy rounds. Cost is in the session list."}
            </div>
          </div>
        ) : direct || tab === "json" ? (
          <div key={`${selected}-${index}`} className="record-editor">
            <JsonTree value={rec} />
          </div>
        ) : (
          <div id="detail">
            <DetailBody rec={rec} tab={tab} proxyOn={proxyOn} />
          </div>
        )}
      </main>
    </div>
  );
}

function keepTags(text: string): string {
  return text.replace(/<\/?[a-z][\w:-]*[^>]*>/gi, (tag) => `\`${tag}\``);
}

function RichText({ text }: { text: string }) {
  const chunks: { key: number; tag?: string; body: string }[] = [];
  const re = /<([a-z][\w:-]*)>([\s\S]*?)<\/\1>/gi;
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) chunks.push({ key: last, body: text.slice(last, at) });
    chunks.push({ key: at, tag: m[1], body: m[2].trim() });
    last = at + m[0].length;
  }
  if (last < text.length) chunks.push({ key: last, body: text.slice(last) });
  const nodes: ReactNode[] = [];
  for (const chunk of chunks) {
    if (!chunk.body) continue;
    const md = <Markdown>{keepTags(chunk.body)}</Markdown>;
    nodes.push(
      chunk.tag ? (
        <div key={chunk.key} className="markup">
          <div className="role">{chunk.tag.replace(/[-_]/g, " ").toUpperCase()}</div>
          {md}
        </div>
      ) : (
        <div key={chunk.key}>{md}</div>
      ),
    );
  }
  return nodes.length ? <div className="prose">{nodes}</div> : null;
}

function Block({ role, text, className }: { role: string; text: string; className: string }) {
  if (!text) return null;
  return (
    <div className={`block ${className}`}>
      <div className="role">{role}</div>
      <RichText text={text} />
    </div>
  );
}

function DetailBody({ rec, tab, proxyOn }: { rec: TrafficRecord; tab: DetailTab; proxyOn: boolean }) {
  if (tab === "system") {
    const text = systemText(bodyOf(rec));
    return text ? <Block role="SYSTEM" text={text} className="system" /> : <div className="empty">No system prompt</div>;
  }
  if (tab === "tools") {
    const tools = bodyOf(rec).tools;
    if (!Array.isArray(tools) || !tools.length) return <div className="empty">No tools in this request</div>;
    return (
      <>
        {tools.map((tool, i) => {
          const obj = tool !== null && typeof tool === "object" ? (tool as Record<string, unknown>) : {};
          const schema = obj.input_schema ?? obj.parameters ?? null;
          return (
            <div key={i} className="tool-card">
              <h3>{typeof obj.name === "string" ? obj.name : "tool"}</h3>
              {typeof obj.description === "string" && obj.description ? <RichText text={obj.description} /> : null}
              {schema ? <JsonTree value={schema} /> : null}
            </div>
          );
        })}
      </>
    );
  }
  return <ChatView rec={rec} proxyOn={proxyOn} />;
}

function Message({ role, content, className }: { role: string; content: unknown; className: string }) {
  const parts = contentParts(content);
  if (!parts.length) return null;
  return (
    <div className={`block ${className}`}>
      <div className="role">{role}</div>
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <RichText key={i} text={part.text} />
        ) : (
          <div key={i} className="block-json">
            <div className="role">{part.label}</div>
            <JsonTree value={part.value} />
          </div>
        ),
      )}
    </div>
  );
}

function ChatView({ rec, proxyOn }: { rec: TrafficRecord; proxyOn: boolean }) {
  const body = bodyOf(rec);
  const resp = respOf(rec);
  const blocks = [
    <Block key="system" role="SYSTEM" text={systemText(body)} className="system" />,
    ...(Array.isArray(body.messages) ? body.messages : []).flatMap((msg, i) => {
      if (!msg || typeof msg !== "object") return [];
      const obj = msg as Record<string, unknown>;
      const role = (typeof obj.role === "string" ? obj.role : "user").toLowerCase();
      return [
        <Message
          key={`m${i}`}
          role={role.toUpperCase()}
          content={obj.content}
          className={role === "assistant" ? "assistant" : role}
        />,
      ];
    }),
    <Block
      key="thinking"
      role="THINKING"
      text={typeof resp.assembled_thinking === "string" ? resp.assembled_thinking : ""}
      className="thinking"
    />,
    <Block
      key="assistant"
      role="ASSISTANT"
      text={typeof resp.assembled_text === "string" ? resp.assembled_text : ""}
      className="assistant"
    />,
    Array.isArray(resp.tool_calls) && resp.tool_calls.length ? (
      <div key="tools" className="block tool">
        <div className="role">TOOL CALLS</div>
        <JsonTree value={resp.tool_calls} />
      </div>
    ) : null,
  ].filter(Boolean);
  if (blocks.length) return <>{blocks}</>;
  if (rec.url) return <div className="empty">No chat text in this round</div>;
  return (
    <div className="empty">
      {proxyOn
        ? "Direct costs only. Pin HTTP(S)_PROXY (and the CA) in each new Claude process, then send a new message."
        : "Direct costs only. Turn Proxy on to capture chat."}
    </div>
  );
}
