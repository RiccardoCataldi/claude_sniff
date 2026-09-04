import { Header } from "./components/Header";
import { InfoPage } from "./components/InfoPage";
import { ProxyPage } from "./components/ProxyPage";
import { TrafficPage } from "./components/TrafficPage";
import { UsagePage } from "./components/UsagePage";
import { useMonitor } from "./useMonitor";

export default function App() {
  const { snapshot: s, monitor: m } = useMonitor();

  const traffic = (live: boolean) => (
    <TrafficPage
      live={live}
      proxyOn={s.proxy.proxy}
      sessions={s.sessions}
      selected={s.selected}
      records={s.records}
      index={s.index}
      tab={s.tab}
      source={s.source}
      project={s.project}
      model={s.model}
      period={s.usagePeriod}
      onSelectSession={(key) => void m.selectSession(key)}
      onSelectRequest={m.selectRequest}
      onTab={m.setTab}
      onSource={(source) => void m.setSource(source)}
      onProject={(project) => void m.setProject(project)}
      onModel={(model) => void m.setModel(model)}
      onPeriod={(period) => void m.setUsagePeriod(period)}
      onRefresh={() => void m.tick()}
    />
  );

  return (
    <>
      <Header
        page={s.page}
        proxy={s.proxy}
        onPage={(page) => void m.setPage(page)}
        onProxy={(on) => void m.toggleProxy(on)}
        onIntercept={(on) => void m.toggleIntercept(on)}
      />
      {s.page === "traffic" ? traffic(false) : null}
      {s.page === "usage" ? (
        <UsagePage
          usage={s.usage}
          period={s.usagePeriod}
          source={s.source}
          project={s.project}
          model={s.model}
          onPeriod={(period) => void m.setUsagePeriod(period)}
          onSource={(source) => void m.setSource(source)}
          onProject={(project) => void m.setProject(project)}
          onModel={(model) => void m.setModel(model)}
          onOpenProject={m.openProject}
        />
      ) : null}
      {s.page === "proxy" ? (
        <ProxyPage
          proxy={s.proxy}
          onForward={(body) => void m.forward(body)}
          onDrop={() => void m.drop()}
        >
          {traffic(true)}
        </ProxyPage>
      ) : null}
      {s.page === "info" ? <InfoPage proxy={s.proxy} /> : null}
    </>
  );
}
