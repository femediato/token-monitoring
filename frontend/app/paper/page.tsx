"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import TimeChart, { type SeriesSpec } from "@/components/TimeChart";
import { Badge, ErrorNotice, Tile } from "@/components/ui";
import { useApi, type Num, type PaperPositionDetail, type PaperReport } from "@/lib/api";
import { DASH, pct, price, short, signClass, usd, utc } from "@/lib/format";

const STRATEGY: Record<string, string> = { momentum: "A · Momentum", pullback: "B · Pullback", range: "C · Range" };
const KIND_TONE: Record<string, "good" | "warning" | "critical" | "neutral"> = {
  fill: "good",
  exit: "good",
  reject: "neutral",
  cancel: "warning",
  exit_failed: "critical",
};

function n(v: Num | undefined, digits = 2): string {
  return v == null ? DASH : v.toFixed(digits);
}

function toTime(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function Sym({ mint, symbols }: { mint: string; symbols: Record<string, string | null> }) {
  return (
    <Link href={`/tokens/${mint}`}>
      <span className="sym">{symbols[mint] ?? short(mint)}</span>
    </Link>
  );
}

function PositionDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, error } = useApi<PaperPositionDetail>(`/api/paper/positions/${id}`);
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <div className="card muted">Loading position #{id}…</div>;
  const p = data.position;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <h2>
          Position #{p.id} · {data.symbol ?? short(p.token_mint)} · {STRATEGY[p.strategy] ?? p.strategy} · {p.status}
        </h2>
        <button className="small" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="small muted" style={{ marginTop: 0 }}>
        Signal candle {utc(p.signal_bar)}, decided {utc(p.signal_at)}. Entry reference {price(p.entry_ref)}, stop {price(p.stop)},
        target {price(p.target)}, max hold {p.max_hold_hours} h. Sized {usd(p.size_usd)} ({p.sizing_binding} limit), planned risk{" "}
        {usd(p.planned_risk_usd)}, quoted one-way cost {pct(p.quoted_cost_pct, 2)}. {p.strategy_version} · {p.paper_version}.
      </p>
      {data.issues.length > 0 ? (
        <div className="notice" style={{ marginBottom: 12 }}>
          <b>Reconciliation issues:</b> {data.issues.join("; ")}
        </div>
      ) : (
        <p className="small">
          <Badge tone="good">Reconciles</Badge> events, fills, quantities, fees, proceeds and P/L agree.
        </p>
      )}
      <h3 className="small">Timeline</h3>
      <table>
        <thead>
          <tr>
            <th>At</th>
            <th>Event</th>
            <th>Reason</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {data.events.map((e) => (
            <tr key={e.id}>
              <td className="small">{utc(e.at)}</td>
              <td>
                <Badge tone={KIND_TONE[e.kind] ?? "neutral"}>{e.kind}</Badge>
              </td>
              <td className="small">{e.reason ?? DASH}</td>
              <td className="small mono" style={{ wordBreak: "break-all" }}>
                {Object.entries(e.data)
                  .map(([k, v]) => `${k}=${typeof v === "number" ? +v.toPrecision(6) : String(v)}`)
                  .join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 className="small" style={{ marginTop: 12 }}>
        Fills (executable Jupiter quotes)
      </h3>
      <div className="table-wrap" style={{ border: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Side</th>
              <th>Quoted</th>
              <th className="num">USDC</th>
              <th className="num">Tokens</th>
              <th className="num">Reference</th>
              <th className="num">Fill</th>
              <th className="num">Cost</th>
              <th className="num">Fee</th>
              <th className="num">Trigger</th>
              <th className="num">Backtest model</th>
              <th className="num">Shortfall</th>
              <th>Route / error</th>
            </tr>
          </thead>
          <tbody>
            {data.fills.map((f) => (
              <tr key={f.id}>
                <td>{f.side}</td>
                <td className="small">{utc(f.quoted_at ?? f.decided_at)}</td>
                <td className="num">{usd(f.usd)}</td>
                <td className="num">{f.qty == null ? DASH : f.qty.toPrecision(6)}</td>
                <td className="num">{price(f.ref_price)}</td>
                <td className="num">{price(f.fill_price)}</td>
                <td className="num">{pct(f.cost_pct, 2)}</td>
                <td className="num">{usd(f.fee_usd)}</td>
                <td className="num">{price(f.trigger_price)}</td>
                <td className="num" title={f.model_note ?? "waiting for candles"}>
                  {f.model_price == null ? (f.model_note ?? "pending") : price(f.model_price)}
                </td>
                <td className={`num ${signClass(f.shortfall_vs_model_pct == null ? null : -f.shortfall_vs_model_pct)}`}>
                  {pct(f.shortfall_vs_model_pct, 2, true)}
                </td>
                <td className="small">
                  {f.error ? <span className="down">{f.error}</span> : (f.route_labels ?? []).join(" → ")}
                  {f.raw_ids && f.raw_ids.length > 0 && <span className="muted"> · raw #{f.raw_ids.join(", #")}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function PaperPage() {
  const { data: r, error } = useApi<PaperReport>("/api/paper");
  const [picked, setPicked] = useState<number | null>(null);

  const equitySpec = useMemo<SeriesSpec[]>(
    () => [
      {
        kind: "line",
        label: "Equity",
        color: "--series-1",
        format: "usd",
        data: (r?.equity ?? []).map((e) => ({ time: toTime(e.at), value: e.equity_usd })),
      },
    ],
    [r],
  );
  const ddSpec = useMemo<SeriesSpec[]>(() => {
    let peak = -Infinity;
    const data = (r?.equity ?? []).map((e) => {
      peak = Math.max(peak, e.equity_usd);
      return { time: toTime(e.at), value: peak > 0 ? -((peak - e.equity_usd) / peak) * 100 : 0 };
    });
    return [{ kind: "line", label: "Drawdown %", color: "--series-1", format: "count", data }];
  }, [r]);

  if (error) return <ErrorNotice error={error} />;
  const head = (
    <div className="page-head">
      <h1>Paper portfolio</h1>
      <span className="sub">{r?.version ?? "…"} · virtual money: fills are executable Jupiter quotes, nothing is signed or sent</span>
    </div>
  );
  if (!r) return head;
  if (!r.account)
    return (
      <>
        {head}
        <div className="card empty">The paper engine has not run yet (collector job &quot;paper_trading&quot;, every minute).</div>
      </>
    );

  const a = r.account;
  const m = r.metrics;
  const ex = r.execution;
  const rec = r.reconciliation;
  const obs = ex.vs_backtest.observed;
  const ass = ex.vs_backtest.assumed;

  return (
    <>
      {head}
      <div className="notice" style={{ marginBottom: 16 }}>
        <b>
          Day {r.progress.days.toFixed(1)} of {r.progress.days_required}, {r.progress.trades} of {r.progress.trades_required} completed
          trades.
        </b>{" "}
        The plan requires at least {r.progress.days_required} days of forward paper trading (and Phase 9 at least{" "}
        {r.progress.trades_required} trades) before live execution is even considered. Until then these numbers describe the
        pipeline, not an edge.
      </div>

      <div className="tiles">
        <Tile label="Equity" value={usd(a.equity_usd)} hint={`started at ${usd(a.starting_equity_usd)}`} />
        <Tile label="Net return" value={<span className={signClass(m.net_return_pct)}>{pct(m.net_return_pct, 2, true)}</span>} hint="marked equity" />
        <Tile label="Max drawdown" value={pct(m.max_drawdown_pct, 2)} hint="from the 1-minute marks" />
        <Tile label="Cash / in positions" value={usd(a.cash_usd)} hint={`${usd(a.positions_usd)} in ${r.open.filter((p) => p.status === "open").length} open positions`} />
        <Tile label="Trades" value={m.trades} hint={`win rate ${pct(m.win_rate_pct, 0)} · PF ${n(m.profit_factor)}`} />
        <Tile
          label="Reconciliation"
          value={rec.ok ? <Badge tone="good">OK</Badge> : <Badge tone="critical">{rec.issues.length} issues</Badge>}
          hint={`${rec.positions} positions; cash rebuilt from fills ${usd(rec.cash_from_fills_usd)}`}
        />
      </div>

      {!rec.ok && (
        <div className="notice" style={{ marginBottom: 16 }}>
          <b>Reconciliation issues:</b>{" "}
          {rec.issues.map((i, k) => (
            <span key={k}>
              {i.position ? (
                <a href="#" onClick={(e) => { e.preventDefault(); setPicked(i.position); }}>
                  #{i.position}
                </a>
              ) : (
                "account"
              )}
              : {i.issue}.{" "}
            </span>
          ))}
        </div>
      )}

      {picked != null && <PositionDetail id={picked} onClose={() => setPicked(null)} />}

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Open positions and orders</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Token</th>
                <th>Strategy</th>
                <th>Entered</th>
                <th className="num">Size</th>
                <th className="num">Entry</th>
                <th className="num">Last</th>
                <th className="num">Unrealised</th>
                <th className="num">Stop</th>
                <th className="num">Target</th>
                <th>Exit by</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {r.open.map((p) => {
                const value = p.qty != null && p.last_price != null ? p.qty * p.last_price : null;
                const upnl = value == null ? null : value - p.size_usd;
                return (
                  <tr key={p.id}>
                    <td>
                      <a href="#" onClick={(e) => { e.preventDefault(); setPicked(p.id); }}>
                        {p.id}
                      </a>
                    </td>
                    <td>
                      <Sym mint={p.token_mint} symbols={r.symbols} />
                    </td>
                    <td>{STRATEGY[p.strategy] ?? p.strategy}</td>
                    <td className="small">{p.status === "pending" ? "order pending" : utc(p.entry_at)}</td>
                    <td className="num">{usd(p.size_usd)}</td>
                    <td className="num">{price(p.entry_price)}</td>
                    <td className="num" title={p.last_price_at ? `marked ${utc(p.last_price_at)}` : undefined}>
                      {price(p.last_price)}
                    </td>
                    <td className={`num ${signClass(upnl)}`}>{usd(upnl)}</td>
                    <td className="num">{price(p.stop)}</td>
                    <td className="num">{price(p.target)}</td>
                    <td className="small">{utc(p.exit_by)}</td>
                    <td className="small">{p.pending_exit ? <span className="down">{p.pending_exit} exit failed ×{p.exit_attempts}, retrying</span> : DASH}</td>
                  </tr>
                );
              })}
              {r.open.length === 0 && (
                <tr>
                  <td colSpan={12} className="empty">
                    No open positions.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Equity and drawdown</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Marked every minute to the latest small sell quote of each open position (15-minute points shown). Since {utc(r.running_since)}.
        </p>
        <TimeChart series={equitySpec} />
        <h3 className="small" style={{ margin: "12px 0 6px" }}>
          Drawdown from peak (%)
        </h3>
        <TimeChart series={ddSpec} />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Execution quality — paper fills vs. the backtest model</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Each fill is a full-size Jupiter quote; its cost is measured against a $10 quote taken just before. &quot;Backtest
          model&quot; is the price the backtest&apos;s fill model would have used for the same order (next candle open or trigger level,
          plus the quoted cost); shortfall is how much worse the paper fill was. These observations calibrate the backtest&apos;s
          execution assumptions.
        </p>
        <table>
          <thead>
            <tr>
              <th>Side</th>
              <th className="num">Attempts</th>
              <th className="num">Failed</th>
              <th className="num">Realised cost</th>
              <th className="num">Quoted estimate</th>
              <th className="num">Price impact</th>
              <th className="num">Shortfall vs model (n)</th>
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
        <h3 className="small" style={{ margin: "12px 0 6px" }}>
          Backtest base assumptions
        </h3>
        <table>
          <thead>
            <tr>
              <th>Assumption</th>
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

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Closed trades</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Token</th>
                <th>Strategy</th>
                <th className="num">Paid</th>
                <th className="num">Entry</th>
                <th className="num">Exit fill</th>
                <th className="num">Costs in / out</th>
                <th className="num">P/L</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {r.trades.map((t) => (
                <tr key={t.position}>
                  <td>
                    <a href="#" onClick={(e) => { e.preventDefault(); setPicked(t.position); }}>
                      {t.position}
                    </a>
                  </td>
                  <td className="small">{utc(t.entry_at)}</td>
                  <td className="small">{utc(t.exit_at)}</td>
                  <td>
                    <Sym mint={t.mint} symbols={r.symbols} />
                  </td>
                  <td>{STRATEGY[t.strategy] ?? t.strategy}</td>
                  <td className="num">{usd(t.size_usd)}</td>
                  <td className="num">{price(t.entry_price)}</td>
                  <td className="num">{price(t.exit_fill_price)}</td>
                  <td className="num">
                    {pct(t.entry_cost_pct, 2)} / {pct(t.exit_cost_pct, 2)}
                  </td>
                  <td className={`num ${signClass(t.pnl_usd)}`}>
                    {usd(t.pnl_usd)} <span className="small">({pct(t.return_pct, 1, true)})</span>
                  </td>
                  <td className="small">{t.reason}</td>
                </tr>
              ))}
              {r.trades.length === 0 && (
                <tr>
                  <td colSpan={11} className="empty">
                    No closed trades yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Latest decisions</h2>
        <p className="muted small" style={{ marginTop: 0 }}>
          Every signal is recorded once per strategy, token and signal candle, with the risk rule that accepted or rejected it.
        </p>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>At</th>
                <th>Event</th>
                <th>Token</th>
                <th>Strategy</th>
                <th>Reason</th>
                <th>Position</th>
              </tr>
            </thead>
            <tbody>
              {r.events.map((e) => (
                <tr key={e.id}>
                  <td className="small">{utc(e.at)}</td>
                  <td>
                    <Badge tone={KIND_TONE[e.kind] ?? "neutral"}>{e.kind}</Badge>
                  </td>
                  <td>{e.token_mint ? <Sym mint={e.token_mint} symbols={r.symbols} /> : DASH}</td>
                  <td>{e.strategy ? (STRATEGY[e.strategy] ?? e.strategy) : DASH}</td>
                  <td className="small">{e.reason ?? DASH}</td>
                  <td>
                    {e.position_id ? (
                      <a href="#" onClick={(ev) => { ev.preventDefault(); setPicked(e.position_id); }}>
                        #{e.position_id}
                      </a>
                    ) : (
                      DASH
                    )}
                  </td>
                </tr>
              ))}
              {r.events.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty">
                    No signals yet. Setups are rare: see the Strategies page for every check.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
