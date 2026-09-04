import { useMemo, useState } from "react";
import { Select } from "./Pills";
import { MODEL_FILTERS, NO_PROJECT, SOURCE_FILTERS, usd } from "../format";
import type { SourceFilter, UsageGrain, UsagePeriod, UsagePoint, UsageRollup, UsageSlice } from "../types";

const PERIODS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "week", label: "7 Days" },
  { value: "30days", label: "30 Days" },
  { value: "month", label: "This Month" },
  { value: "all", label: "6 Months" },
  { value: "lifetime", label: "Lifetime" },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PALETTE = ["#5b8def", "#7eb8a8", "#e0b15a", "#d9897a", "#9b8fd4", "#7aaed9", "#c9a0c0"];
const OTHER = "#94a3b8";
const TIP_MAX = 5;
const W = 800;
const H = 268;
const PAD = { l: 52, r: 8, t: 28, b: 32 };

type Props = {
  usage: UsageRollup;
  period: UsagePeriod;
  source: SourceFilter;
  project: string | null;
  model: string;
  onPeriod: (period: UsagePeriod) => void;
  onSource: (source: SourceFilter) => void;
  onProject: (project: string | null) => void;
  onModel: (model: string) => void;
  onOpenProject: (name: string) => void;
};

function projectFilterOptions(names: string[], current: string | null): { value: string; label: string }[] {
  const set = new Set(names);
  if (current) set.add(current);
  const real = [...set].filter((n) => n !== NO_PROJECT).sort((a, b) => a.localeCompare(b));
  const options = [{ value: "", label: "All projects" }, ...real.map((n) => ({ value: n, label: n }))];
  if (set.has(NO_PROJECT)) options.push({ value: NO_PROJECT, label: NO_PROJECT });
  return options;
}

export function UsagePage({
  usage,
  period,
  source,
  project,
  model,
  onPeriod,
  onSource,
  onProject,
  onModel,
  onOpenProject,
}: Props) {
  const rows = usage.projects || [];
  const projects = projectFilterOptions(usage.projectNames || [], project);
  return (
    <div id="page-usage" className="page">
      <div className="usage-body">
        <div className="usage-hero">
          <div className="usage-hero-top">
            <div>
              <div id="usage-cost">{usd(usage.costUSD)}</div>
              <div id="usage-sub">
                {usage.calls} calls · {usage.sessions} sessions
              </div>
            </div>
            <div className="usage-filters">
              <Select label="Period" value={period} options={PERIODS} onChange={onPeriod} />
              <Select
                label="Source"
                value={source}
                options={SOURCE_FILTERS.map((opt) =>
                  opt.value === "" ? { ...opt, label: "All sources" } : opt,
                )}
                onChange={onSource}
              />
              <Select
                label="Project"
                value={project ?? ""}
                options={projects}
                onChange={(value) => onProject(value || null)}
              />
              <Select label="Model" value={model} options={MODEL_FILTERS} onChange={onModel} />
            </div>
          </div>
          <CostChart series={usage.series || []} granularity={usage.granularity || "day"} />
        </div>
        <div className="card usage-table-card">
          <h2>Top projects</h2>
          <table id="usage-projects">
            <thead>
              <tr>
                <th>Project</th>
                <th>Cost</th>
                <th>Sessions</th>
                <th>Avg/session</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name} onClick={() => onOpenProject(row.name)}>
                  <td>{row.name}</td>
                  <td className="cost">{usd(row.costUSD)}</td>
                  <td>{row.sessions}</td>
                  <td>{usd(row.avgCostPerSession)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length ? <div id="usage-empty" className="empty">No spend in this range</div> : null}
        </div>
      </div>
    </div>
  );
}

function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const padded = value * 1.18;
  const exp = Math.floor(Math.log10(padded));
  const pow = 10 ** exp;
  const n = padded / pow;
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
  return nice * pow;
}

function axisUsd(n: number): string {
  if (n === 0) return "$0";
  if (n >= 1000) {
    const k = n / 1000;
    return `$${Number.isInteger(k) ? k : +k.toFixed(1)}k`;
  }
  if (Number.isInteger(n)) return `$${n}`;
  if (n < 0.01) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function grainTitle(grain: UsageGrain): string {
  if (grain === "hour") return "Hourly";
  if (grain === "month") return "Monthly";
  return "Daily";
}

function pointLabel(t: string, grain: UsageGrain): string {
  if (grain === "hour") return t.slice(11, 16);
  if (grain === "month") {
    const [y, m] = t.split("-");
    return `${MONTHS[Number(m) - 1]} ${y?.slice(2)}`;
  }
  const parts = t.split("-");
  return `${Number(parts[2])} ${MONTHS[Number(parts[1]) - 1]}`;
}

function tipTitle(t: string, grain: UsageGrain): string {
  if (grain === "hour") return `${t.slice(0, 10)} ${t.slice(11, 16)}`;
  if (grain === "month") {
    const [y, m] = t.split("-");
    return `${MONTHS[Number(m) - 1]} ${y}`;
  }
  return pointLabel(t, grain);
}

function labelIndexes(n: number, max = 8): number[] {
  if (n <= max) return Array.from({ length: n }, (_, i) => i);
  const out = [0];
  for (let i = 1; i <= max - 2; i++) out.push(Math.round((i * (n - 1)) / (max - 1)));
  out.push(n - 1);
  return [...new Set(out)];
}

function colorOf(name: string, names: string[]): string {
  const i = names.indexOf(name);
  return i < 0 ? OTHER : PALETTE[i % PALETTE.length];
}

function tipRows(projects: UsageSlice[]): UsageSlice[] {
  const rows = projects.filter((row) => row.costUSD > 0);
  if (rows.length <= TIP_MAX) return rows;
  const head = rows.slice(0, TIP_MAX);
  const rest = rows.slice(TIP_MAX).reduce((sum, row) => sum + row.costUSD, 0);
  return [...head, { name: "Other", costUSD: rest }];
}

function CostChart({ series, granularity }: { series: UsagePoint[]; granularity: UsageGrain }) {
  const [hover, setHover] = useState<number | null>(null);
  const names = useMemo(() => {
    const totals = new Map<string, number>();
    for (const p of series) {
      for (const row of p.projects || []) totals.set(row.name, (totals.get(row.name) || 0) + row.costUSD);
    }
    return [...totals.keys()].sort((a, b) => (totals.get(b) || 0) - (totals.get(a) || 0));
  }, [series]);
  if (!series.length) return <div className="empty">No spend in this range</div>;
  const innerW = W - PAD.l - PAD.r;
  const innerH = H - PAD.t - PAD.b;
  const peak = Math.max(0, ...series.map((p) => p.costUSD));
  const max = niceMax(peak);
  const ticks = Array.from({ length: 5 }, (_, i) => (max * i) / 4);
  const n = series.length;
  const slot = innerW / n;
  const gap = n > 60 ? 1 : n > 24 ? 2 : n > 12 ? 4 : 8;
  const barW = Math.max(2, slot - gap);
  const showValues = barW >= 36;
  const xLabels = new Set(labelIndexes(n, n > 40 ? 6 : 8));
  const point = hover != null ? series[hover] : undefined;
  const barH = (cost: number) => (cost > 0 ? (cost / max) * innerH : 0);
  const rows = point ? tipRows(point.projects || []) : [];

  return (
    <div className="cost-chart-wrap">
      <div className="cost-chart-head">{grainTitle(granularity)} cost</div>
      <div className="cost-chart-plot" onMouseLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} className="cost-chart" role="img" aria-label={`${grainTitle(granularity)} cost`}>
          {ticks.map((tick) => {
            const y = PAD.t + innerH - (tick / max) * innerH;
            return (
              <g key={tick}>
                <line className="cost-grid" x1={PAD.l} y1={y} x2={W - PAD.r} y2={y} />
                <text className="cost-axis" x={PAD.l - 8} y={y} textAnchor="end" dominantBaseline="middle">
                  {axisUsd(tick)}
                </text>
              </g>
            );
          })}
          {hover != null ? (
            <rect
              className="cost-band"
              x={PAD.l + hover * slot}
              y={PAD.t}
              width={slot}
              height={innerH}
            />
          ) : null}
          {series.map((p, i) => {
            const slices = (p.projects || []).filter((row) => row.costUSD > 0);
            const h = barH(p.costUSD);
            const x = PAD.l + i * slot + (slot - barW) / 2;
            const y = PAD.t + innerH - h;
            let yb = PAD.t + innerH;
            return (
              <g key={p.t}>
                {slices.length
                  ? [...slices].reverse().map((row) => {
                      const sh = barH(row.costUSD);
                      yb -= sh;
                      return (
                        <rect
                          key={row.name}
                          className={hover === i ? "cost-bar active" : "cost-bar"}
                          x={x}
                          y={yb}
                          width={barW}
                          height={sh}
                          style={{ fill: colorOf(row.name, names) }}
                        />
                      );
                    })
                  : h > 0
                    ? (
                        <rect
                          className={hover === i ? "cost-bar active" : "cost-bar"}
                          x={x}
                          y={y}
                          width={barW}
                          height={h}
                        />
                      )
                    : null}
                {showValues && p.costUSD > 0 ? (
                  <text className="cost-val" x={x + barW / 2} y={y - 6} textAnchor="middle">
                    {usd(p.costUSD)}
                  </text>
                ) : null}
              </g>
            );
          })}
          <line className="cost-base" x1={PAD.l} y1={PAD.t + innerH} x2={W - PAD.r} y2={PAD.t + innerH} />
          {series.map((p, i) =>
            xLabels.has(i) ? (
              <text
                key={`x${p.t}`}
                className="cost-axis"
                x={PAD.l + i * slot + slot / 2}
                y={H - 10}
                textAnchor="middle"
              >
                {pointLabel(p.t, granularity)}
              </text>
            ) : null,
          )}
          {series.map((p, i) => (
            <rect
              key={`h${p.t}`}
              x={PAD.l + i * slot}
              y={PAD.t}
              width={slot}
              height={innerH + 20}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          ))}
        </svg>
        {point && hover != null ? (
          <div
            className={hover > n * 0.62 ? "cost-tip left" : "cost-tip"}
            style={{
              left: `${((PAD.l + (hover > n * 0.62 ? hover : hover + 1) * slot) / W) * 100}%`,
              top: `${((PAD.t + 4) / H) * 100}%`,
            }}
          >
            <div className="cost-tip-t">{tipTitle(point.t, granularity)}</div>
            <div className="cost-tip-v">{usd(point.costUSD)}</div>
            {rows.length ? (
              <ul className="cost-tip-rows">
                {rows.map((row) => (
                  <li key={row.name}>
                    <span
                      className="cost-tip-dot"
                      style={{ background: row.name === "Other" ? OTHER : colorOf(row.name, names) }}
                    />
                    <span className="cost-tip-name">{row.name}</span>
                    <span className="cost-tip-amt">{usd(row.costUSD)}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
