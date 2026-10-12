"use client";

import Link from "next/link";
import { useMemo } from "react";
import TimeChart, { type SeriesSpec } from "@/components/TimeChart";
import { STRATEGY_STYLE } from "@/components/TradeChart";
import { ErrorNotice, Tile } from "@/components/ui";
import { useApi, type AccuracyStats, type Num, type Performance, type SignalsReport, type StrategyStats } from "@/lib/api";
import { DASH, pct, signClass, usd } from "@/lib/format";

const ORDER = ["momentum", "pullback", "range", "all"] as const;
const name = (k: string) => (k === "all" ? "All strategies" : (STRATEGY_STYLE[k]?.name ?? k));
const toTime = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

function n(v: Num | undefined, digits = 2): string {
  return v == null ? DASH : v.toFixed(digits);
}

function Money({ v }: { v: Num | undefined }) {
  return <span className={signClass(v)}>{usd(v)}</span>;
}

function StratCell({ k }: { k: string }) {
  return (
    <td style={k === "all" ? { fontWeight: 600 } : undefined}>
      {k !== "all" && <span className="key-dot" style={{ background: `var(${STRATEGY_STYLE[k]?.color})` }} />}
      {name(k)}
    </td>
  );
}

function AccuracyRow({ k, label, a }: { k: string; label: string; a: AccuracyStats }) {
  return (
    <tr>
      {label === "All" ? <StratCell k={k} /> : <td />}
      <td className="ink2">{label}</td>
      <td className="num">{a.signals}</td>
      <td className="num">{a.resolved}</td>
      <td className="num">{pct(a.target_hit_pct, 0)}</td>
      <td className="num">{pct(a.positive_pct, 0)}</td>
      <td className={`num ${signClass(a.mean_net_pct)}`}>{pct(a.mean_net_pct, 2, true)}</td>
      <td className="num">{pct(a.median_net_pct, 2, true)}</td>
    </tr>
  );
}

export default function PerformancePage() {
  const { data: r, error } = useApi<Performance>("/api/analytics/performance");
  const sig = useApi<SignalsReport>("/api/analytics/signals?days=365");

  const charts = useMemo(() => {
    const eq = r?.equity ?? [];
    let peak = -Infinity;
    const equity: SeriesSpec[] = [
      { kind: "line", label: "Equity", color: "--series-1", format: "usd", data: eq.map((e) => ({ time: toTime(e.at), value: e.equity_usd })) },
    ];
    const dd: SeriesSpec[] = [
      {
        kind: "line",
        label: "Drawdown %",
        color: "--series-1",
        format: "count",
        data: eq.map((e) => {
          peak = Math.max(peak, e.equity_usd);
          return { time: toTime(e.at), value: peak > 0 ? -((peak - e.equity_usd) / peak) * 100 : 0 };
        }),
      },
    ];
    const daily: SeriesSpec[] = [
      {
        kind: "histogram",
        label: "Daily realised P/L",
        color: "--series-1",
        format: "usd",
        data: (r?.daily ?? []).map((d) => ({ time: toTime(d.day + "T00:00:00Z"), value: d.pnl_usd, color: d.pnl_usd >= 0 ? "--up" : "--down" })),
      },
    ];
    return { equity, dd, daily };
  }, [r]);

  if (error) return <ErrorNotice error={error} />;
  const head = (
    <div className="page-head">
      <h1>Performance analytics</h1>
      <span className="sub">
        Forward paper trading ({r?.paper_version ?? "…"}) · {r?.version ?? "…"} · historical simulations are on <Link href="/backtests">Backtests</Link>
      </span>
    </div>
  );
  if (!r) return head;
  if (!r.account)
    return (
      <>
        {head}
        <div className="card empty">The paper engine has not run yet.</div>
      </>
    );
  const m = r.metrics;
  const all = r.by_strategy.all;
  const ex = r.execution;
  const obs = ex.vs_backtest.observed;
  const ass = ex.vs_backtest.assumed;
  const acc = sig.data?.accuracy;

  return (
    <>
      {head}
      <div className="notice" style={{ marginBottom: 16 }}>
        <b>
          Day {r.progress.days.toFixed(1)} of {r.progress.days_required}, {r.progress.trades} of {r.progress.trades_required} trades.
        </b>{" "}
        With this few trades the figures below describe what happened, not an edge: a handful of outcomes can move every ratio.
      </div>

      <div className="tiles">
        <Tile label="Net return" value={<span className={signClass(m.net_return_pct)}>{pct(m.net_return_pct, 2, true)}</span>} hint={`${usd(m.net_pnl_usd)} realised`} />
        <Tile label="Max drawdown" value={pct(m.max_drawdown_pct, 2)} hint="from the 1-minute marks" />
        <Tile label="Profit factor" value={n(m.profit_factor)} hint={`gross ${usd(m.gross_profit_usd)} / ${usd(m.gross_loss_usd)}`} />
        <Tile label="Win rate" value={pct(m.win_rate_pct, 0)} hint={`${all.wins} of ${all.trades} trades`} />
        <Tile label="Expectancy" value={<Money v={m.expectancy_usd} />} hint={`${pct(m.expectancy_pct, 2, true)} per trade`} />
        <Tile label="Trading costs" value={usd(all.costs.total_usd)} hint={`${pct(all.costs.pct_of_volume, 2)} of ${usd(all.costs.volume_usd)} traded`} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Equity curve</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Marked every minute to the latest small sell quote of each open position; 15-minute points shown.
        </p>
        <TimeChart series={charts.equity} />
        <h3 className="small" style={{ margin: "12px 0 6px" }}>
          Drawdown from peak (%)
        </h3>
        <TimeChart series={charts.dd} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Strategy comparison</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="num">Trades</th>
                <th className="num">Win rate</th>
                <th className="num">Profit factor</th>
                <th className="num">Expectancy</th>
                <th className="num">Net P/L</th>
                <th className="num" title="What the price moves alone made: net P/L plus all trading costs">Before costs</th>
                <th className="num">Costs</th>
                <th className="num">Best / worst</th>
                <th className="num">Avg hold</th>
                <th>Exits</th>
              </tr>
            </thead>
            <tbody>
              {ORDER.map((k) => {
                const s: StrategyStats | undefined = r.by_strategy[k];
                if (!s) return null;
                return (
                  <tr key={k}>
                    <StratCell k={k} />
                    <td className="num">{s.trades}</td>
                    <td className="num">{pct(s.win_rate_pct, 0)}</td>
                    <td className="num">{n(s.profit_factor)}</td>
                    <td className="num">
                      <Money v={s.expectancy_usd} /> <span className="small muted">({pct(s.expectancy_pct, 2, true)})</span>
                    </td>
                    <td className="num">
                      <Money v={s.net_pnl_usd} />
                    </td>
                    <td className="num">
                      <Money v={s.trades ? s.pnl_before_costs_usd : null} />
                    </td>
                    <td className="num">{s.trades ? usd(s.costs.total_usd) : DASH}</td>
                    <td className="num">{s.trades ? `${pct(s.best_pct, 1, true)} / ${pct(s.worst_pct, 1, true)}` : DASH}</td>
                    <td className="num">{s.avg_hold_hours == null ? DASH : `${s.avg_hold_hours.toFixed(1)}h`}</td>
                    <td className="small ink2">
                      {Object.entries(s.exits)
                        .map(([e, c]) => `${e} ${c}`)
                        .join(" · ") || DASH}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }} id="accuracy">
        <h2>Signal accuracy</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Every signal since the start, followed on the 5-minute candles with the backtest&apos;s exit rules and the quoted cost each way (no
          slippage, no failed swaps): how good the signals are before execution. Rejected signals were turned down by the portfolio risk
          rules. Signals still running or without candles are not counted as resolved. See <Link href="/signals">Trading signals</Link> for each
          one on its chart.
        </p>
        {!acc ? (
          <div className="muted">Loading…</div>
        ) : (
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Signals</th>
                  <th className="num">n</th>
                  <th className="num">Resolved</th>
                  <th className="num">Hit take-profit</th>
                  <th className="num">Positive after costs</th>
                  <th className="num">Mean net</th>
                  <th className="num">Median net</th>
                </tr>
              </thead>
              <tbody>
                {ORDER.filter((k) => acc[k]).flatMap((k) => [
                  <AccuracyRow key={`${k}-all`} k={k} label="All" a={acc[k].all} />,
                  <AccuracyRow key={`${k}-taken`} k={k} label="Taken" a={acc[k].taken} />,
                  <AccuracyRow key={`${k}-rejected`} k={k} label="Rejected" a={acc[k].rejected} />,
                ])}
              </tbody>
            </table>
            <h3 className="small" style={{ margin: "12px 0 6px" }}>
              Taken signals: paper result vs. ideal
            </h3>
            <p className="muted small" style={{ marginTop: 0 }}>
              The gap is what execution cost on the same signals: real quotes instead of candle prices, stops caught by the 1-minute
              polling instead of at the level, and fees.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th className="num">Trades compared</th>
                  <th className="num">Ideal (mean)</th>
                  <th className="num">Paper (mean)</th>
                  <th className="num">Execution gap</th>
                </tr>
              </thead>
              <tbody>
                {ORDER.filter((k) => acc[k]).map((k) => {
                  const t = acc[k].taken;
                  const gap = t.paper_mean_return_pct != null && t.ideal_mean_net_pct != null ? t.paper_mean_return_pct - t.ideal_mean_net_pct : null;
                  return (
                    <tr key={k}>
                      <StratCell k={k} />
                      <td className="num">{t.paper_n}</td>
                      <td className={`num ${signClass(t.ideal_mean_net_pct)}`}>{pct(t.ideal_mean_net_pct, 2, true)}</td>
                      <td className={`num ${signClass(t.paper_mean_return_pct)}`}>{pct(t.paper_mean_return_pct, 2, true)}</td>
                      <td className={`num ${signClass(gap)}`}>{gap == null ? DASH : `${gap > 0 ? "+" : ""}${gap.toFixed(2)} pp`}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Daily results</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Realised P/L of the trades closed each UTC day; the equity change also includes open positions at the day&apos;s last mark.
        </p>
        <TimeChart series={charts.daily} />
        <div className="table-wrap" style={{ border: 0, marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>Day (UTC)</th>
                <th className="num">Trades closed</th>
                <th className="num">Wins</th>
                <th className="num">Realised P/L</th>
                <th className="num">Costs</th>
                <th className="num">Equity at close</th>
                <th className="num">Equity change</th>
              </tr>
            </thead>
            <tbody>
              {[...r.daily].reverse().map((d) => (
                <tr key={d.day}>
                  <td>{d.day}</td>
                  <td className="num">{d.trades}</td>
                  <td className="num">{d.wins}</td>
                  <td className="num">
                    <Money v={d.pnl_usd} />
                  </td>
                  <td className="num">{usd(d.costs_usd)}</td>
                  <td className="num">{usd(d.equity_usd)}</td>
                  <td className={`num ${signClass(d.return_pct)}`}>{pct(d.return_pct, 2, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>Trading costs</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Entry and exit costs are measured against a $10 reference quote taken just before each fill, and include the $0.05 swap fee per
          side. Stop overshoot is how far below its stop a stop exit actually sold: a price move caught late by the 1-minute polling, not a
          fee, but a real cost of how stops are executed.
        </p>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th className="num">Traded</th>
                <th className="num">Entry costs</th>
                <th className="num">Exit costs</th>
                <th className="num">of which fees</th>
                <th className="num">Total costs</th>
                <th className="num">% of traded</th>
                <th className="num">Per trade</th>
                <th className="num">Stop overshoot</th>
              </tr>
            </thead>
            <tbody>
              {ORDER.map((k) => {
                const c = r.by_strategy[k]?.costs;
                if (!c) return null;
                return (
                  <tr key={k}>
                    <StratCell k={k} />
                    <td className="num">{usd(c.volume_usd)}</td>
                    <td className="num">{usd(c.entry_usd)}</td>
                    <td className="num">{usd(c.exit_usd)}</td>
                    <td className="num">{usd(c.fees_usd)}</td>
                    <td className="num">{usd(c.total_usd)}</td>
                    <td className="num">{pct(c.pct_of_volume, 2)}</td>
                    <td className="num">{usd(c.per_trade_usd)}</td>
                    <td className="num">{usd(c.stop_overshoot_usd)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <h3 className="small" style={{ margin: "16px 0 6px" }}>
          Execution quality: paper fills vs. the backtest model
        </h3>
        <table>
          <thead>
            <tr>
              <th>Side</th>
              <th className="num">Attempts</th>
              <th className="num">Failed</th>
              <th className="num">Realised cost</th>
              <th className="num">Quoted estimate</th>
              <th className="num">Price impact</th>
              <th className="num" title="How much worse the paper fill was than the price the backtest would have assumed">Shortfall vs model (n)</th>
            </tr>
          </thead>
          <tbody>
            {(["buy", "sell"] as const).map((s) => (
              <tr key={s}>
                <td>{s === "buy" ? "Entries" : "Exits"}</td>
                <td className="num">{ex[s].attempts}</td>
                <td className="num">
                  {ex[s].failed} <span className="muted small">({pct(ex[s].failure_pct, 1)})</span>
                </td>
                <td className="num">{pct(ex[s].cost_pct, 2)}</td>
                <td className="num">{pct(ex[s].quoted_cost_pct, 2)}</td>
                <td className="num">{pct(ex[s].price_impact_pct, 2)}</td>
                <td className="num">
                  {pct(ex[s].shortfall_vs_model_pct, 2, true)} <span className="muted small">({ex[s].model_n})</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Backtest assumption</th>
              <th className="num">Backtest (bt-v1 base)</th>
              <th className="num">Observed in paper</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Realised cost ÷ quoted cost</td>
              <td className="num">× {n(ass.cost_multiplier)}</td>
              <td className="num">{obs.cost_multiplier == null ? DASH : `× ${n(obs.cost_multiplier)}`}</td>
            </tr>
            <tr>
              <td>Stop fill below trigger</td>
              <td className="num">{pct(ass.stop_slippage_pct, 1)}</td>
              <td className="num">
                {pct(obs.stop_slippage_pct, 2)} <span className="muted small">({ex.stops} stops)</span>
              </td>
            </tr>
            <tr>
              <td>Failed swaps (quote unavailable)</td>
              <td className="num">{pct(ass.swap_failure_pct, 1)}</td>
              <td className="num">{pct(obs.swap_failure_pct, 1)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}
