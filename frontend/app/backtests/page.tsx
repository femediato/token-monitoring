"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import TimeChart, { type SeriesSpec } from "@/components/TimeChart";
import { Badge, ErrorNotice, Seg, Tile } from "@/components/ui";
import { useApi, type BacktestList, type BacktestMetrics, type BacktestReport, type BacktestRunResult } from "@/lib/api";
import { DASH, pct, price, short, signClass, usd, utc } from "@/lib/format";

const SPLIT_LABEL: Record<string, string> = {
  development: "Development",
  validation: "Validation",
  holdout: "Holdout",
  all: "Whole window",
};
const STRATEGY: Record<string, string> = { momentum: "A · Momentum", pullback: "B · Pullback", range: "C · Range", early: "Early alert" };

function n(v: number | null | undefined, digits = 2, signed = false): string {
  if (v == null) return DASH;
  const s = v.toFixed(digits);
  return signed && v > 0 ? "+" + s : s;
}

function GateBadge({ status }: { status: string }) {
  if (status === "pass") return <Badge tone="good">Pass</Badge>;
  if (status === "fail") return <Badge tone="critical">Fail</Badge>;
  return <Badge tone="warning">Not enough data</Badge>;
}

function curve(run: BacktestRunResult | undefined, kind: "equity" | "drawdown") {
  if (!run) return [];
  let peak = -Infinity;
  return run.equity.map(([t, eq]) => {
    peak = Math.max(peak, eq);
    const value = kind === "equity" ? eq : peak > 0 ? -((peak - eq) / peak) * 100 : 0;
    return { time: Math.floor(new Date(t).getTime() / 1000), value };
  });
}

function MetricCells({ m }: { m: BacktestMetrics | undefined }) {
  if (!m) return <td colSpan={9} className="muted">{DASH}</td>;
  return (
    <>
      <td className="num">{m.trades}</td>
      <td className={`num ${signClass(m.net_return_pct)}`}>{pct(m.net_return_pct, 2, true)}</td>
      <td className="num">{pct(m.win_rate_pct, 0)}</td>
      <td className="num">{n(m.profit_factor)}</td>
      <td className={`num ${signClass(m.expectancy_usd)}`}>{m.expectancy_usd == null ? DASH : usd(m.expectancy_usd)}</td>
      <td className="num">{pct(m.max_drawdown_pct, 2)}</td>
      <td className="num" title={`${m.return_days} daily returns`}>
        {n(m.sharpe)} / {n(m.sortino)}
      </td>
      <td className="num">{pct(m.avg_cost_pct, 2)}</td>
      <td className="num">{pct(m.exposure_pct, 1)}</td>
    </>
  );
}

export default function BacktestsPage() {
  const list = useApi<BacktestList>("/api/backtests");
  const [picked, setPicked] = useState<number | null>(null);
  const id = picked ?? list.data?.items[0]?.id ?? null;
  const rep = useApi<BacktestReport>(id ? `/api/backtests/${id}` : null, { refreshInterval: 0 });
  const [split, setSplit] = useState("all");
  const [scenario, setScenario] = useState("base");
  const [baseline, setBaseline] = useState("mcap");
  const [horizon, setHorizon] = useState("4");
  const [tradesOf, setTradesOf] = useState("selection");

  const r = rep.data;
  const runOf = (u: string, s = scenario) => r?.runs.find((x) => x.universe === u && x.scenario === s);
  const universes = useMemo(() => [...new Set((r?.runs ?? []).map((x) => x.universe))], [r]);
  const splitNames = r ? [...r.splits.map((s) => s.name).filter((s) => r.runs[0]?.metrics[s]), "all"] : ["all"];
  const sel = runOf("selection");
  const base = runOf(baseline);
  const label = (u: string) => list.data?.universes[u] ?? u;

  const equitySpec = useMemo<SeriesSpec[]>(
    () => [
      { kind: "line", label: label("selection"), color: "--series-1", format: "usd", data: curve(sel, "equity") },
      { kind: "line", label: label(baseline), color: "--series-2", format: "usd", data: curve(base, "equity") },
    ],
    [sel, base, baseline, list.data],
  );
  const ddSpec = useMemo<SeriesSpec[]>(
    () => [
      { kind: "line", label: label("selection"), color: "--series-1", format: "count", data: curve(sel, "drawdown") },
      { kind: "line", label: label(baseline), color: "--series-2", format: "count", data: curve(base, "drawdown") },
    ],
    [sel, base, baseline, list.data],
  );

  if (list.error) return <ErrorNotice error={list.error} />;
  const head = (
    <div className="page-head">
      <h1>Backtests</h1>
      <span className="sub">
        {list.data?.version ?? "…"} · research only: simulated results on stored data, not evidence of future profit
      </span>
    </div>
  );
  if (list.data && list.data.items.length === 0)
    return (
      <>
        {head}
        <div className="card empty">
          No backtest stored yet. Run <code>make backtest</code> (whole history, holdout sealed).
        </div>
      </>
    );

  const allSel = sel?.metrics.all;
  const tradeRun = runOf(tradesOf);
  const fwd = r?.forward[horizon];

  return (
    <>
      {head}
      <div className="toolbar" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}>
        <label className="small muted">
          Run{" "}
          <select value={id ?? ""} onChange={(e) => setPicked(Number(e.target.value))}>
            {list.data?.items.map((it) => (
              <option key={it.id} value={it.id}>
                #{it.id} · {utc(it.created_at)} · {it.data.days_available} days{it.sealed ? "" : " · holdout unsealed"}
                {Object.keys(it.overrides).length ? " · overrides" : ""}
              </option>
            ))}
          </select>
        </label>
        <Seg label="Scenario" value={scenario} onChange={setScenario} options={(list.data?.scenarios ?? ["base", "stress"]).map((s) => ({ value: s, label: s === "base" ? "Base costs" : "Stress (1.5× costs)" }))} />
      </div>
      {rep.error && <ErrorNotice error={rep.error} />}
      {r && !r.data.sufficient && (
        <div className="notice" style={{ marginBottom: 16 }}>
          <b>Insufficient data.</b> This window covers {r.data.days_available} days of point-in-time data; the plan requires{" "}
          {r.data.days_target}. With so few trades the numbers below test the pipeline, not the strategies — no conclusion about an edge
          can be drawn yet.
        </div>
      )}
      {r && (
        <>
          <div className="tiles">
            <Tile label="Window" value={`${r.window.days} days`} hint={`${utc(r.window.start)} → ${utc(r.window.end)}`} />
            <Tile label="Trades (selection)" value={allSel?.trades ?? DASH} hint={`${scenario} scenario, whole simulated window`} />
            <Tile label="Net return" value={<span className={signClass(allSel?.net_return_pct)}>{pct(allSel?.net_return_pct, 2, true)}</span>} hint="selection, marked equity" />
            <Tile label="Max drawdown" value={pct(allSel?.max_drawdown_pct, 2)} hint="selection" />
            <Tile label="Holdout" value={r.window.sealed ? "Sealed" : "Unsealed"} hint={r.window.sealed ? "last third not simulated" : "simulated in this run"} />
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <h2>Release gates (Phase 9, indicative)</h2>
            <table>
              <tbody>
                {r.gates.map((g) => (
                  <tr key={g.gate}>
                    <td>{g.gate}</td>
                    <td className="num">{n(g.value)}</td>
                    <td className="muted small">target {g.threshold}</td>
                    <td>
                      <GateBadge status={g.status} />
                    </td>
                    <td className="muted small">
                      {g.trades} out-of-sample trades ({g.min_trades} needed)
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <h2>Selection vs. baselines — trading results</h2>
              <Seg label="Split" value={split} onChange={setSplit} options={splitNames.map((s) => ({ value: s, label: SPLIT_LABEL[s] ?? s }))} />
            </div>
            <p className="muted small" style={{ marginTop: 0 }}>
              The same strat-v1 rules and risk limits trade each universe. Trades count in the split they were entered; return and
              drawdown use marked equity inside the split. Sharpe / Sortino need ≥ 14 daily returns. &quot;Candles&quot; is the share
              of member-steps with fresh candles — a universe without candles cannot trade, so low coverage handicaps it.
            </p>
            <div className="table-wrap" style={{ border: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Universe</th>
                    <th className="num">Trades</th>
                    <th className="num">Net return</th>
                    <th className="num">Win rate</th>
                    <th className="num">Profit factor</th>
                    <th className="num">Expectancy</th>
                    <th className="num">Max DD</th>
                    <th className="num">Sharpe / Sortino</th>
                    <th className="num">Avg cost</th>
                    <th className="num">Exposure</th>
                    <th className="num">Candles</th>
                  </tr>
                </thead>
                <tbody>
                  {universes.map((u) => {
                    const run = runOf(u);
                    return (
                      <tr key={u}>
                        <td>{run?.label ?? u}</td>
                        <MetricCells m={run?.metrics[split]} />
                        <td className="num">{pct(run?.candle_coverage_pct, 0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <h2>Equity and drawdown</h2>
              <Seg
                label="Compare with"
                value={baseline}
                onChange={setBaseline}
                options={universes.filter((u) => u !== "selection").map((u) => ({ value: u, label: u }))}
              />
            </div>
            <TimeChart series={equitySpec} />
            <h3 className="small" style={{ margin: "12px 0 6px" }}>
              Drawdown from peak (%)
            </h3>
            <TimeChart series={ddSpec} />
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <h2>Selection test — forward returns of members</h2>
              <Seg label="Horizon" value={horizon} onChange={setHorizon} options={Object.keys(r.forward).map((h) => ({ value: h, label: `${h}h` }))} />
            </div>
            <p className="muted small" style={{ marginTop: 0 }}>
              Strategy-independent: at every whole hour, the equal-weight return of each universe&apos;s members over the next {horizon}h,
              from evaluation prices (available for every token, including ones that later failed). Net subtracts the quoted round-trip
              cost where known. Uplift = our selection&apos;s mean minus the baseline&apos;s. Split: {SPLIT_LABEL[split] ?? split}.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Universe</th>
                  <th className="num">Observations</th>
                  <th className="num">Missing</th>
                  <th className="num">Mean</th>
                  <th className="num">Median</th>
                  <th className="num">Hit rate</th>
                  <th className="num">Net mean (n)</th>
                  <th className="num">Selection uplift</th>
                </tr>
              </thead>
              <tbody>
                {universes.filter((u) => fwd?.[u]).map((u) => {
                  const x = fwd?.[u][split];
                  return (
                    <tr key={u}>
                      <td>{label(u)}</td>
                      <td className="num">{x?.n ?? DASH}</td>
                      <td className="num">{x?.missing ?? DASH}</td>
                      <td className={`num ${signClass(x?.mean_pct)}`}>{pct(x?.mean_pct, 2, true)}</td>
                      <td className="num">{pct(x?.median_pct, 2, true)}</td>
                      <td className="num">{pct(x?.hit_rate_pct, 0)}</td>
                      <td className="num">
                        {pct(x?.net_mean_pct, 2, true)} <span className="muted small">({x?.net_n ?? 0})</span>
                      </td>
                      <td className={`num ${signClass(x?.uplift_pct)}`}>{u === "selection" ? DASH : pct(x?.uplift_pct, 2, true)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <h2>
                Trades — {label(tradesOf)}, {scenario}
              </h2>
              <Seg label="Universe" value={tradesOf} onChange={setTradesOf} options={universes.map((u) => ({ value: u, label: u }))} />
            </div>
            <div className="table-wrap" style={{ border: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Token</th>
                    <th>Strategy</th>
                    <th className="num">Size</th>
                    <th className="num">Entry price</th>
                    <th className="num">Exit trigger</th>
                    <th className="num">Exit fill</th>
                    <th className="num">Cost (1-way)</th>
                    <th className="num">P/L</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(tradeRun?.trades ?? []).map((t) => (
                    <tr key={t.position}>
                      <td className="small">{utc(t.entry_at)}</td>
                      <td className="small">{utc(t.exit_at)}</td>
                      <td>
                        <Link href={`/tokens/${t.mint}`}>
                          <span className="sym">{r.symbols[t.mint] ?? short(t.mint)}</span>
                        </Link>
                      </td>
                      <td>{STRATEGY[t.strategy] ?? t.strategy}</td>
                      <td className="num">{usd(t.size_usd)}</td>
                      <td className="num">{price(t.entry_price)}</td>
                      <td className="num">{price(t.exit_price)}</td>
                      <td className="num">{price(t.exit_fill_price)}</td>
                      <td className="num">{pct(t.cost_pct, 2)}</td>
                      <td className={`num ${signClass(t.pnl_usd)}`}>
                        {usd(t.pnl_usd)} <span className="small">({pct(t.return_pct, 1, true)})</span>
                      </td>
                      <td className="small">{t.reason}</td>
                    </tr>
                  ))}
                  {tradeRun && tradeRun.trades.length === 0 && (
                    <tr>
                      <td colSpan={11} className="empty">
                        No closed trades in this run.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            {tradeRun && tradeRun.open_positions.length > 0 && (
              <p className="muted small">
                Still open at the end (marked to the last candle): {tradeRun.open_positions.map((p) => `${r.symbols[p.mint] ?? short(p.mint)} (${p.strategy})`).join(", ")}
              </p>
            )}
          </div>

          <div className="card explain">
            <h2>Assumptions and reproducibility</h2>
            <p>
              <b>Splits</b> (plan: days 1–45 / 46–60 / 61–90, applied proportionally):{" "}
              {r.splits.map((s) => `${SPLIT_LABEL[s.name]} ${utc(s.start)} → ${utc(s.end)}`).join(" · ")}. Simulated until{" "}
              {utc(r.window.simulated_until)}.
            </p>
            <p>
              <b>Execution.</b> Candles reach the signal step {r.params.backtest.candle_latency_minutes} min after they close (the live
              delay); fills and exits use the price path itself.{" "}
              {Object.values(r.params.backtest.scenarios).map((s) => (
                <span key={s.name}>
                  <i>{s.name}</i>: entry at the open {s.entry_delay_minutes ? `≥ ${s.entry_delay_minutes} min` : "right"} after the
                  decision, costs × {s.cost_multiplier}, stops fill {s.stop_slippage_pct}% below the trigger, {s.swap_failure_pct}% of
                  swaps fail.{" "}
                </span>
              ))}
            </p>
            <p>
              <b>Universes.</b> Selection: top 30 eligible by score at every 15-minute evaluation (the live strategy universe). Fixed:
              the selection at the window start. Market cap / volume: top 30 at each hourly evaluation among tokens without a critical
              safety failure. Random: per selected token, one of the 5 nearest non-selected tokens by market cap and liquidity, drawn
              with a fixed seed. Early: the selection plus an entry on every early alert. Tokens that later failed stay in every
              ranking.
            </p>
            <p className="small">
              Versions {r.version} · {r.strategy_version} · model {r.model_version}. Inputs: {r.data.tokens} tokens,{" "}
              {r.data.evaluations.toLocaleString()} evaluations, {r.data.candles.toLocaleString()} candles. Input digest{" "}
              <code>{r.data.input_digest.slice(0, 16)}…</code>, result digest <code>{r.result_digest.slice(0, 16)}…</code> — rerunning
              the same window on the same stored data reproduces both.
              {Object.keys(r.params.overrides).length > 0 && <> Overrides: <code>{JSON.stringify(r.params.overrides)}</code>.</>}
            </p>
            <details>
              <summary className="small">Every parameter</summary>
              <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(r.params, null, 1)}</pre>
            </details>
          </div>
        </>
      )}
    </>
  );
}
