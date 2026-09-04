import { useEffect, useState } from "react";
import { createHttpAdapter } from "./api";
import { createMonitor, type Monitor, type MonitorAdapter, type MonitorSnapshot } from "./monitor";
import { hrefForPage, pageFromPath } from "./types";

export function useMonitor(adapter?: MonitorAdapter): {
  snapshot: MonitorSnapshot;
  monitor: Monitor;
} {
  const [monitor] = useState(() => createMonitor(adapter ?? createHttpAdapter(), pageFromPath(location.pathname)));
  const [snapshot, setSnapshot] = useState(() => monitor.snapshot());
  useEffect(() => monitor.subscribe(setSnapshot), [monitor]);
  useEffect(() => {
    if (monitor.snapshot().page === "usage") void monitor.setPage("usage");
    void monitor.tick();
    const id = setInterval(() => void monitor.tick(), 1000);
    return () => clearInterval(id);
  }, [monitor]);
  useEffect(() => {
    const onPop = () => {
      void monitor.setPage(pageFromPath(location.pathname));
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [monitor]);
  useEffect(() => {
    const href = hrefForPage(snapshot.page);
    if (location.pathname !== href) history.pushState(null, "", href);
  }, [snapshot.page]);
  return { snapshot, monitor };
}
