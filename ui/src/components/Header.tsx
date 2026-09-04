import { hrefForPage, type Page, type ProxySnapshot } from "../types";

const PAGES: { id: Page; label: string }[] = [
  { id: "traffic", label: "Sessions" },
  { id: "usage", label: "Usage" },
  { id: "proxy", label: "Proxy" },
  { id: "info", label: "Info" },
];

type Props = {
  page: Page;
  proxy: ProxySnapshot;
  onPage: (page: Page) => void;
  onProxy: (on: boolean) => void;
  onIntercept: (on: boolean) => void;
};

export function Header({ page, proxy, onPage, onProxy, onIntercept }: Props) {
  return (
    <header id="bar">
      <span className="brand">claude-sniff</span>
      <nav id="nav">
        {PAGES.map((item) => (
          <a
            key={item.id}
            href={hrefForPage(item.id)}
            className={item.id === page ? "tab active" : "tab"}
            onClick={(ev) => {
              if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
              ev.preventDefault();
              onPage(item.id);
            }}
          >
            {item.label}
          </a>
        ))}
      </nav>
      <div id="status">
        <div className={proxy.proxy ? "mode on" : "mode"}>
          <span>Proxy</span>
          <label className="switch">
            <input type="checkbox" checked={proxy.proxy} onChange={(ev) => onProxy(ev.target.checked)} />
            <span></span>
          </label>
        </div>
        <div className={proxy.intercept ? "mode on" : "mode"}>
          <span>Intercept</span>
          <label className="switch">
            <input
              type="checkbox"
              checked={proxy.intercept}
              disabled={!proxy.proxy}
              onChange={(ev) => onIntercept(ev.target.checked)}
            />
            <span></span>
          </label>
        </div>
        {proxy.queue.length ? <span className="pill hold">{proxy.queue.length} held</span> : null}
      </div>
    </header>
  );
}
