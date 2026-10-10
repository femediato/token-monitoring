"use client";

import Link from "next/link";
import { Badge, ErrorNotice, Tile } from "@/components/ui";
import { useApi, type EarlyAlertRow, type EarlyBoard, type EarlyReport, type Stat } from "@/lib/api";
import { ago, DASH, pct, short, signClass, usd, utc } from "@/lib/format";

const SIGNAL_LABEL: Record<string, string> = {
  volume_acceleration: "Volume ×1.5",
  buyer_growth: "Traders +30%",
  buying_pressure: "Buys ≥ 55%",
  liquidity_growth: "Depth +10%",
  price_restraint: "1h < +8%",
  positive_trend: "Above EMA20",
  participation_quality: "Organic",
};

const STATE: Record<string, ["good" | "warning" | "neutral", string]> = {
  high_priority: ["good", "High priority"],
  emerging: ["warning", "Emerging"],
  monitor: ["neutral", "Monitor"],
  none: ["neutral", "No signal"],
};

function Signals({ signals }: { signals: Record<string, boolean | null> }) {
  return (
    <span>
      {Object.keys(SIGNAL_LABEL).map((k) => {
        const v = signals[k];
        return (
          <span
            key={k}
            className={`chip ${v ? "on" : v === null ? "missing" : "off"}`}
            title={`${SIGNAL_LABEL[k]}: ${v ? "fired" : v === null ? "no data" : "not fired"}`}
          >
            {v ? "✓ " : v === null ? "? " : ""}
            {SIGNAL_LABEL[k]}
          </span>
        );
      })}
    </span>
  );
}

const ret = (v: number | null) => <span className={signClass(v)}>{pct(v, 1, true)}</span>;

function StatCells({ s, signed = true }: { s: Stat; signed?: boolean }) {
  return (
    <>
      <td className="num">{s.n}</td>
      <td className="num">{s.mean == null ? DASH : <span className={signClass(s.mean)}>{pct(s.mean, 2, signed)}</span>}</td>
      <td className="num">{pct(s.median, 2, signed)}</td>
      <td className="num">{s.win_rate == null ? DASH : pct(s.win_rate * 100, 0)}</td>
    </>
  );
}

const HORIZONS: [string, string][] = [
  ["ret_1h_pct", "1h"],
  ["ret_4h_pct", "4h"],
  ["ret_12h_pct", "12h"],
  ["ret_24h_pct", "24h"],
];

export default function EarlyPage() {
  const board = useApi<EarlyBoard>("/api/early/board?min_score=50");
  const alerts = useApi<EarlyAlertRow[]>("/api/early/alerts?days=14");
  const report = useApi<EarlyReport>("/api/early/report?days=90");
  if (board.error) return <ErrorNotice error={board.error} />;
  const b = board.data;
  const r = report.data;

  return (
    <>
      <div className="page-head">
        <h1>Early opportunities</h1>
        <span className="sub">
          Model {b?.model_version ?? "…"} · evaluated {ago(b?.evaluated_at)} · alerts are research signals, not buy orders
        </span>
      </div>
      <div className="tiles">
        {(["high_priority", "emerging", "monitor"] as const).map((s) => (
          <Tile key={s} label={STATE[s][1]} value={b?.states[s] ?? 0} hint={s === "high_priority" ? "≥ 6 of 7 signals" : s === "emerging" ? "5 of 7 signals" : "4 of 7 signals"} />
        ))}
        <Tile label="Alerts (90 days)" value={r?.alerts ?? DASH} hint={`${r?.with_controls ?? 0} with matched controls`} />
        <Tile label="Entry available" value={r ? `${r.entry.available} / ${r.entry.checked}` : DASH} hint="$1k buy quote ≤ 2% cost" />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Signal board</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Every token with at least 4 of 7 signals now. Alerts are raised only for eligible tokens at 5 or more signals, at most once per
          token every 6 hours.
        </p>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Token</th>
                <th>State</th>
                <th className="num">Score</th>
                <th>Signals</th>
                <th>Eligible</th>
                <th className="num">1h</th>
                <th className="num">Vol 1h</th>
                <th className="num">Mkt cap</th>
                <th className="num">Liquidity</th>
              </tr>
            </thead>
            <tbody>
              {(b?.items ?? []).map((t) => (
                <tr key={t.mint}>
                  <td><Link href={`/tokens/${t.mint}`}><span className="sym">{t.symbol ?? short(t.mint)}</span></Link></td>
                  <td><Badge tone={STATE[t.state]?.[0] ?? "neutral"}>{STATE[t.state]?.[1] ?? t.state}</Badge></td>
                  <td className="num">{t.score.toFixed(0)}</td>
                  <td><Signals signals={t.signals} /></td>
                  <td className={t.eligible ? "" : "muted"}>{t.eligible ? "yes" : "no"}</td>
                  <td className="num">{ret(t.ret_1h_pct)}</td>
                  <td className="num">{usd(t.volume_1h)}</td>
                  <td className="num">{usd(t.market_cap)}</td>
                  <td className="num">{usd(t.liquidity_usd)}</td>
                </tr>
              ))}
              {b?.items.length === 0 && <tr><td colSpan={9} className="empty">No token has 4 or more signals right now.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Does it work? Alerts vs. matched controls (90 days)</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Controls are eligible tokens with no early signal at the same moment, closest in market cap and liquidity. Uplift is each
          alert&apos;s return minus the mean of its own controls. Treat results as noise until there are at least ~30 alerts with
          controls and the 95% interval excludes zero.
        </p>
        {r && (
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Horizon</th>
                  <th className="num">Alerts n</th><th className="num">Mean</th><th className="num">Median</th><th className="num">Win</th>
                  <th className="num">Controls n</th><th className="num">Mean</th><th className="num">Median</th><th className="num">Win</th>
                  <th className="num">Uplift n</th><th className="num">Mean uplift</th><th className="num">95% interval</th>
                </tr>
              </thead>
              <tbody>
                {HORIZONS.map(([k, label]) => {
                  const h = r.horizons[k];
                  return (
                    <tr key={k}>
                      <td>{label}</td>
                      <StatCells s={h.alerts} />
                      <StatCells s={h.controls} />
                      <td className="num">{h.uplift.n}</td>
                      <td className="num">{h.uplift.mean == null ? DASH : <span className={signClass(h.uplift.mean)}>{pct(h.uplift.mean, 2, true)}</span>}</td>
                      <td className="num muted">{h.uplift.ci95 ? `${pct(h.uplift.ci95[0], 2, true)} … ${pct(h.uplift.ci95[1], 2, true)}` : DASH}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="small ink2">
              Before the alert, price had already moved {pct(r.pre_alert.pre_return_1h_pct?.median, 2, true)} (1h, median) and{" "}
              {pct(r.pre_alert.pre_return_4h_pct?.median, 2, true)} (4h). 24h excursions — alerts: best {pct(r.excursions.alert.mfe_24h_pct.median, 1, true)},
              worst {pct(r.excursions.alert.mae_24h_pct.median, 1, true)}; controls: best {pct(r.excursions.control.mfe_24h_pct.median, 1, true)}, worst{" "}
              {pct(r.excursions.control.mae_24h_pct.median, 1, true)} (medians).
            </p>
          </div>
        )}
      </div>

      <div className="card">
        <h2>Alerts (14 days)</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Token</th>
                <th>State</th>
                <th>Signals</th>
                <th className="num" title="Price move in the hour before the alert">Pre 1h</th>
                <th className="num" title="Price move in the 4 hours before the alert">Pre 4h</th>
                <th className="num" title="Cost of a $1,000 buy quoted right after the alert">Entry cost</th>
                <th className="num">1h</th>
                <th className="num">4h</th>
                <th className="num">12h</th>
                <th className="num">24h</th>
                <th className="num" title="Mean 24h return of the matched controls">Controls 24h</th>
                <th className="num" title="Best / worst move within 24h">Best / worst</th>
              </tr>
            </thead>
            <tbody>
              {(alerts.data ?? []).map((a) => (
                <tr key={a.id}>
                  <td title={utc(a.at)}>{ago(a.at)}</td>
                  <td><Link href={`/tokens/${a.mint}`}><span className="sym">{a.symbol ?? short(a.mint)}</span></Link></td>
                  <td><Badge tone={STATE[a.state]?.[0] ?? "neutral"}>{STATE[a.state]?.[1] ?? a.state}</Badge></td>
                  <td><Signals signals={a.signals} /></td>
                  <td className="num">{ret(a.pre_return_1h_pct)}</td>
                  <td className="num">{ret(a.pre_return_4h_pct)}</td>
                  <td className="num">{a.entry_error ? <span className="muted" title={a.entry_error}>n/a</span> : pct(a.entry_cost_pct, 2)}</td>
                  <td className="num">{ret(a.ret_1h_pct)}</td>
                  <td className="num">{ret(a.ret_4h_pct)}</td>
                  <td className="num">{ret(a.ret_12h_pct)}</td>
                  <td className="num">{ret(a.ret_24h_pct)}</td>
                  <td className="num">{a.controls ? ret(a.control_ret_24h_pct) : <span className="muted">none</span>}</td>
                  <td className="num">{a.mfe_24h_pct == null ? DASH : `${pct(a.mfe_24h_pct, 1, true)} / ${pct(a.mae_24h_pct, 1, true)}`}</td>
                </tr>
              ))}
              {alerts.data?.length === 0 && <tr><td colSpan={13} className="empty">No alerts yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
