"use client";

import Link from "next/link";
import { Badge, ErrorNotice, Tile } from "@/components/ui";
import { useApi, type Check, type StrategyParams, type StrategySetups, type StrategySignalRow, type StrategySignals } from "@/lib/api";
import { ago, DASH, pct, price, short, utc } from "@/lib/format";

const NAMES: Record<string, string> = { momentum: "A · Momentum breakout", pullback: "B · Pullback", range: "C · Range" };
const ORDER = ["momentum", "pullback", "range"] as const;

const CHECK_LABEL: Record<string, string> = {
  fresh_candles: "Fresh candles",
  fresh_evaluation: "Fresh score",
  eligible: "Eligible",
  cost_estimate: "Cost quote",
  breakout: "Breakout",
  relative_volume: "Rel. volume",
  buying_pressure: "Buying ≥ 60",
  no_risk_warnings: "No warnings",
  uptrend: "EMA20 > EMA50",
  retrace: "Retrace 5–10%",
  buying_improves: "Buying improves",
  recovery_candle: "Recovery candle",
  range_found: "Range",
  support_tests: "Support ×2",
  resistance_tests: "Resistance ×2",
  near_support: "Near support",
  reversal: "Reversal candle",
  reward_risk: "R/R ≥ 2",
};
const COMMON = new Set(["fresh_candles", "fresh_evaluation", "eligible", "cost_estimate"]);

function Chip({ k, v }: { k: string; v: Check }) {
  const label = CHECK_LABEL[k] ?? k;
  return (
    <span className={`chip ${v ? "on" : v === null ? "missing" : "off"}`} title={`${label}: ${v ? "passed" : v === null ? "no data" : "failed"}`}>
      {v ? "✓ " : v === null ? "? " : "✗ "}
      {label}
    </span>
  );
}

function Cell({ row }: { row?: StrategySignalRow }) {
  if (!row) return <td className="muted">{DASH}</td>;
  if (row.setup)
    return (
      <td>
        <Badge tone="good">Setup</Badge>{" "}
        <span className="small">
          entry {price(row.entry_ref)} · stop {price(row.stop)} · target {price(row.target)}
        </span>
      </td>
    );
  const total = Object.keys(row.checks).length;
  const passed = total - row.failed.length;
  const common = row.failed.filter((k) => COMMON.has(k));
  const rule = row.failed.filter((k) => !COMMON.has(k));
  return (
    <td
      style={{ whiteSpace: "normal", minWidth: 200 }}
      title={Object.entries(row.checks).map(([k, v]) => `${v ? "✓" : v === null ? "?" : "✗"} ${CHECK_LABEL[k] ?? k}`).join("\n")}>
      <span className="small muted">
        {passed}/{total} passed
        {common.length > 0 ? ` · blocked: ${common.map((k) => CHECK_LABEL[k] ?? k).join(", ")}` : ""}
      </span>
      <br />
      {rule.map((k) => (
        <Chip key={k} k={k} v={row.checks[k]} />
      ))}
    </td>
  );
}

export default function StrategiesPage() {
  const sig = useApi<StrategySignals>("/api/strategies/signals");
  const setups = useApi<StrategySetups>("/api/strategies/setups?days=7");
  const params = useApi<StrategyParams>("/api/strategies");
  if (sig.error) return <ErrorNotice error={sig.error} />;
  const s = sig.data;
  const st = setups.data;
  const p = params.data?.params;

  const byToken = new Map<string, { symbol: string | null; rows: Record<string, StrategySignalRow>; candle: string | null }>();
  for (const r of s?.items ?? []) {
    const e = byToken.get(r.mint) ?? { symbol: r.symbol, rows: {}, candle: r.last_candle_at };
    e.rows[r.strategy] = r;
    byToken.set(r.mint, e);
  }
  const totals = Object.fromEntries((st?.totals ?? []).map((t) => [t.strategy, t]));

  return (
    <>
      <div className="page-head">
        <h1>Strategies</h1>
        <span className="sub">
          {s?.version ?? "…"} · evaluated {ago(s?.evaluated_at)} · paper decisions only — nothing here places an order
        </span>
      </div>
      <div className="tiles">
        <Tile label="Tokens checked" value={byToken.size} hint="eligible now (the strategy universe)" />
        {ORDER.map((k) => (
          <Tile
            key={k}
            label={`${NAMES[k]} setups`}
            value={totals[k]?.setups ?? 0}
            hint={`last 7 days, of ${totals[k]?.evaluated ?? 0} checks`}
          />
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Live checks</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Every entry condition, every 15 minutes, for every eligible token. Cells list the conditions not met — ✗ failed, ? input missing
          (never read as a pass); hover a cell for every check. A
          setup needs every check, including fresh candles (≤ {p?.data.max_candle_age_minutes ?? 25} min), a fresh score, eligibility and a
          sell quote ≤ {p?.risk.max_quote_age_minutes ?? 45} min old costing ≤ {p?.risk.max_cost_pct ?? 2}%.
        </p>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Token</th>
                <th>Last candle</th>
                {ORDER.map((k) => (
                  <th key={k}>{NAMES[k]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...byToken.entries()].map(([mint, t]) => (
                <tr key={mint}>
                  <td>
                    <Link href={`/tokens/${mint}`}>
                      <span className="sym">{t.symbol ?? short(mint)}</span>
                    </Link>
                  </td>
                  <td className="small">{t.candle ? ago(new Date(new Date(t.candle).getTime() + 300_000).toISOString()) : "none"}</td>
                  {ORDER.map((k) => (
                    <Cell key={k} row={t.rows[k]} />
                  ))}
                </tr>
              ))}
              {byToken.size === 0 && (
                <tr>
                  <td colSpan={5} className="empty">
                    No evaluation yet — the job runs every 15 minutes, 10 minutes into each quarter hour.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Setups (7 days)</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Token</th>
                <th>Strategy</th>
                <th className="num">Entry ref</th>
                <th className="num">Stop</th>
                <th className="num">Target</th>
                <th className="num">Max hold</th>
                <th className="num">Cost (1-way)</th>
                <th className="num">R/R</th>
              </tr>
            </thead>
            <tbody>
              {(st?.items ?? []).map((r) => (
                <tr key={`${r.at}-${r.mint}-${r.strategy}`}>
                  <td className="small">{utc(r.at)}</td>
                  <td>
                    <Link href={`/tokens/${r.mint}`}>
                      <span className="sym">{r.symbol ?? short(r.mint)}</span>
                    </Link>
                  </td>
                  <td>{NAMES[r.strategy]}</td>
                  <td className="num">{price(r.entry_ref)}</td>
                  <td className="num">{price(r.stop)}</td>
                  <td className="num">{price(r.target)}</td>
                  <td className="num">{r.max_hold_hours}h</td>
                  <td className="num">{pct(r.cost_pct, 2)}</td>
                  <td className="num">{r.values?.reward_risk != null ? r.values.reward_risk.toFixed(2) : DASH}</td>
                </tr>
              ))}
              {st?.items.length === 0 && (
                <tr>
                  <td colSpan={9} className="empty">
                    No setups yet. With ~20 eligible tokens and strict rules, setups are expected to be rare.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>What blocks entries (7 days)</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          How often each condition was not met (failed or missing) across all checks. A check can block alongside others.
        </p>
        <div className="grid-3" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
          {ORDER.map((k) => {
            const rows = (st?.blockers ?? []).filter((b) => b.strategy === k);
            const n = totals[k]?.evaluated ?? 0;
            return (
              <div key={k}>
                <h3 className="small" style={{ margin: "0 0 6px" }}>{NAMES[k]}</h3>
                <table>
                  <tbody>
                    {rows.map((b) => (
                      <tr key={b.check}>
                        <td>{CHECK_LABEL[b.check] ?? b.check}</td>
                        <td className="num">{n ? pct((b.n / n) * 100, 0) : DASH}</td>
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr>
                        <td className="empty">No data yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      </div>

      {p && (
        <div className="card explain">
          <h2>Rules ({params.data?.version})</h2>
          <p>
            <b>A · Momentum breakout.</b> A 5-minute close crosses above the previous completed 1-hour high; relative volume ≥{" "}
            {p.momentum.min_relative_volume}×; Buying-pressure score ≥ {p.momentum.min_buying_score}; no risk flags and organic label
            not low. Exit: +{p.momentum.exit.take_profit_pct}% / −{p.momentum.exit.stop_loss_pct}% / {p.momentum.exit.max_hold_hours}h.
          </p>
          <p>
            <b>B · Pullback.</b> EMA{p.pullback.ema_fast} &gt; EMA{p.pullback.ema_slow} on 5-minute closes; a {p.pullback.min_retrace_pct}–
            {p.pullback.max_retrace_pct}% retrace from the {(p.pullback.high_lookback_candles * 5) / 60}h high; 1h buy share higher than an
            hour earlier; a bullish candle closing above the previous candle&apos;s high. Exit: +{p.pullback.exit.take_profit_pct}% / −
            {p.pullback.exit.stop_loss_pct}% / {p.pullback.exit.max_hold_hours}h.
          </p>
          <p>
            <b>C · Range.</b> Over {(p.range.window_candles * 5) / 60}h: ≥ {p.range.min_support_tests} support and ≥{" "}
            {p.range.min_resistance_tests} resistance tests, price back near support, a reversal candle, reward/risk ≥{" "}
            {p.range.min_reward_risk} after round-trip costs. Exit near resistance, {p.range.stop_buffer_pct}% below support, or after{" "}
            {p.range.max_hold_hours}h (provisional until calibrated in Phase 6).
          </p>
          <p>
            <b>Risk.</b> Paper equity ${p.risk.starting_equity_usd.toLocaleString()}; loss at the stop incl. costs ≤{" "}
            {p.risk.risk_per_trade_pct}% of equity; ≤ {p.risk.max_positions} positions and ≤ {p.risk.max_open_risk_pct}% planned open
            risk; no new entries after a {p.risk.daily_loss_stop_pct}% daily loss; size ≤ ${p.risk.quoted_size_usd} (the quoted size) and ≤{" "}
            {p.risk.max_liquidity_share_pct}% of liquidity; no leverage.
          </p>
        </div>
      )}
    </>
  );
}
