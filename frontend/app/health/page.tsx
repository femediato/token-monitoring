"use client";

import { ErrorNotice, JobBadge, Tile } from "@/components/ui";
import { useApi, type Overview } from "@/lib/api";
import { ago, compactNum, DASH, num, utc } from "@/lib/format";

const PROVIDER: Record<string, string> = {
  discovery: "Jupiter",
  snapshots_active: "Jupiter",
  snapshots_dormant: "Jupiter",
  quotes: "Jupiter",
  pools: "DEX Screener",
  candles: "GeckoTerminal",
  candle_backfill: "GeckoTerminal",
  pool_discovery: "DEX Screener",
  security: "Helius",
  holders: "Helius",
  tracking: "—",
  heartbeat: "—",
};

export default function HealthPage() {
  const { data, error } = useApi<Overview>("/api/overview");
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <div className="muted">Loading…</div>;
  const f = data.freshness;
  const unhealthy = data.jobs.filter((j) => j.status !== "ok");
  const byProvider = new Map<string, number>();
  for (const r of data.requests_24h) {
    const p = PROVIDER[r.job] ?? "—";
    if (p !== "—") byProvider.set(p, (byProvider.get(p) ?? 0) + Number(r.requests));
  }

  return (
    <>
      <div className="page-head">
        <h1>Collection health</h1>
        <span className="sub">
          Collecting since {utc(data.collecting_since)} ({ago(data.collecting_since)}) · job stats cover the last 24h
        </span>
      </div>
      {unhealthy.length > 0 && (
        <div className="notice">
          {unhealthy.length} job{unhealthy.length > 1 ? "s" : ""} need attention: {unhealthy.map((j) => j.job).join(", ")}. Degraded jobs
          include failed runs or gaps longer than twice their cadence (restarts count).
        </div>
      )}
      <div className="tiles">
        <Tile label="Active tokens" value={num(f.tracked_active)} hint={`${num(f.active_stale)} without a fresh snapshot`} />
        <Tile label="Dormant tokens" value={num(f.tracked_dormant)} hint="polled hourly" />
        <Tile label="Excluded" value={num(f.excluded)} hint="non-meme asset classes" />
        <Tile
          label="Provider data lag"
          value={f.source_lag_p50_minutes == null ? DASH : `${f.source_lag_p50_minutes} min`}
          hint={`median · max ${f.source_lag_max_minutes ?? DASH} min · ${num(f.stale_source_values)} stale values (1h)`}
        />
        {[...byProvider.entries()].map(([p, n]) => (
          <Tile key={p} label={`${p} requests 24h`} value={compactNum(n)} />
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Jobs</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Source</th>
                <th>Status</th>
                <th className="num">Every</th>
                <th className="num">Runs</th>
                <th className="num">Failures</th>
                <th className="num">Coverage</th>
                <th className="num">Max gap</th>
                <th className="num">Gaps</th>
                <th className="num">Last success</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((j) => (
                <tr key={j.job}>
                  <td className="mono">{j.job}</td>
                  <td className="ink2">{PROVIDER[j.job] ?? DASH}</td>
                  <td><JobBadge status={j.status} /></td>
                  <td className="num">{j.expected_every_minutes}m</td>
                  <td className="num">{num(j.runs)}</td>
                  <td className="num">{num(j.failures)}</td>
                  <td className="num">{j.coverage_pct.toFixed(1)}%</td>
                  <td className="num">{j.max_gap_minutes == null ? DASH : `${j.max_gap_minutes}m`}</td>
                  <td className="num">{num(j.gaps)}</td>
                  <td className="num">{ago(j.last_success_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Universe by class</h2>
          <table>
            <thead>
              <tr>
                <th>Tracking</th>
                <th>Classification</th>
                <th className="num">Tokens</th>
              </tr>
            </thead>
            <tbody>
              {data.universe.map((u) => (
                <tr key={`${u.tracking_status}-${u.classification}`}>
                  <td>{u.tracking_status}</td>
                  <td className="ink2">{u.classification ?? DASH}</td>
                  <td className="num">{num(u.n)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Stored rows</h2>
          <table>
            <thead>
              <tr>
                <th>Table</th>
                <th className="num">Rows</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.table}>
                  <td className="mono">{r.table}</td>
                  <td className="num">≈ {num(r.n)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Hypertable counts are TimescaleDB estimates and lag until the next statistics refresh.</p>
        </div>
      </div>
    </>
  );
}
