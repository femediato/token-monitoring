"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import TradeChart, { signalSpan, STRATEGY_STYLE } from "@/components/TradeChart";
import { Badge, ErrorNotice, Seg, Tile } from "@/components/ui";
import { useApi, type ChartData, type SignalRow, type SignalsReport } from "@/lib/api";
import { DASH, pct, price, short, signClass, usd, utc } from "@/lib/format";

const OUTCOME: Record<string, ["good" | "warning" | "critical" | "neutral", string]> = {
  target: ["good", "Target"],
  stop: ["critical", "Stop"],
  time: ["neutral", "Time"],
  open: ["warning", "Running"],
  waiting: ["neutral", "Waiting for candles"],
  no_data: ["neutral", "No data"],
};

const HOUR = 3600_000;
const FIVE_MIN = 300_000;

function rel(level: number, ref: number) {
  return pct((level / ref - 1) * 100, 1, true);
}

function Decision({ s }: { s: SignalRow }) {
  if (s.decision === "taken") return <Badge tone="good">Taken #{s.position_id}</Badge>;
  if (s.decision === "cancelled") return <Badge tone="warning" title={s.cancel_reason ?? undefined}>Cancelled</Badge>;
  return <Badge tone="neutral" title="Rejected by the portfolio risk rules">Rejected · {s.reject_reason?.replaceAll("_", " ")}</Badge>;
}

function Outcome({ s }: { s: SignalRow }) {
  const o = s.outcome;
  const [tone, label] = OUTCOME[o.status] ?? ["neutral", o.status];
  return (
    <span>
      <Badge tone={tone}>{label}</Badge>{" "}
      {o.net_pct != null && <span className={signClass(o.net_pct)}>{pct(o.net_pct, 1, true)}</span>}
    </span>
  );
}

function chartWindow(s: SignalRow): [string, string] {
  const [, to] = signalSpan(s, new Date().toISOString());
  const start = new Date(new Date(s.signal_bar).getTime() - 12 * HOUR);
  // Rounded up to the next 5 minutes: a stable request URL between renders.
  const now = Math.ceil(Date.now() / FIVE_MIN) * FIVE_MIN;
  const end = new Date(Math.min(now, new Date(to).getTime() + 3 * HOUR));
  return [start.toISOString(), end.toISOString()];
}

function SignalChart({ s }: { s: SignalRow }) {
  const [start, end] = chartWindow(s);
  const q = `/api/analytics/chart/${s.mint}?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  const { data, error } = useApi<ChartData>(q);
  const st = STRATEGY_STYLE[s.strategy];
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
        <h2>
          <Link href={`/tokens/${s.mint}`}>{s.symbol ?? short(s.mint)}</Link> · {st?.name ?? s.strategy} · signal {utc(s.at)}
        </h2>
        <Decision s={s} />
      </div>
      <p className="small ink2" style={{ marginTop: 0 }}>
        Entry reference {price(s.entry_ref)} · take-profit {price(s.target)} ({rel(s.target, s.entry_ref)}) · stop-loss {price(s.stop)} (
        {rel(s.stop, s.entry_ref)}) · max hold {s.max_hold_hours} h · quoted cost {pct(s.cost_pct, 2)} one way.{" "}
        {s.decision === "taken" &&
          (s.status === "closed" ? (
            <>
              Paper: bought {utc(s.entry_at)} at {price(s.entry_price)}, sold {utc(s.exit_at)} at {price(s.exit_price)} ({s.exit_reason}),{" "}
              <span className={signClass(s.pnl_usd)}>
                {usd(s.pnl_usd)} ({pct(s.paper_return_pct, 1, true)})
              </span>
              .
            </>
          ) : (
            <>Paper position open since {utc(s.entry_at)}.</>
          ))}{" "}
        Ideal outcome on the candles: <Outcome s={s} />
      </p>
      {error ? <ErrorNotice error={error} /> : data ? <TradeChart data={data} highlight={s.id} /> : <div className="muted">Loading chart…</div>}
      {data && data.signals.length > 1 && (
        <p className="small muted" style={{ marginBottom: 0 }}>
          {data.signals.length - 1} other signal{data.signals.length > 2 ? "s" : ""} on this token in the window are drawn fainter.
        </p>
      )}
    </div>
  );
}

type Strat = "all" | "momentum" | "pullback" | "range";
type Dec = "all" | "taken" | "rejected";

export default function SignalsPage() {
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const [strat, setStrat] = useState<Strat>("all");
  const [dec, setDec] = useState<Dec>("all");
  const [picked, setPicked] = useState<number | null>(null);
  const { data, error } = useApi<SignalsReport>(`/api/analytics/signals?days=${days}`);

  const rows = useMemo(
    () => (data?.items ?? []).filter((s) => (strat === "all" || s.strategy === strat) && (dec === "all" || s.decision === dec)),
    [data, strat, dec],
  );
  if (error) return <ErrorNotice error={error} />;
  const selected = rows.find((s) => s.id === picked) ?? rows[0];
  const acc = data?.accuracy[strat === "all" ? "all" : strat];

  return (
    <>
      <div className="page-head">
        <h1>Trading signals</h1>
        <span className="sub">
          Every paper signal with its levels, the risk decision and what followed · {data?.version ?? "…"} · nothing here places an order
        </span>
      </div>
      <div className="toolbar" style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <Seg label="Window" value={days} onChange={setDays} options={[{ value: "7", label: "7 days" }, { value: "30", label: "30 days" }, { value: "90", label: "90 days" }]} />
        <Seg
          label="Strategy"
          value={strat}
          onChange={setStrat}
          options={[{ value: "all", label: "All strategies" }, ...(["momentum", "pullback", "range"] as const).map((k) => ({ value: k, label: STRATEGY_STYLE[k].name }))]}
        />
        <Seg label="Decision" value={dec} onChange={setDec} options={[{ value: "all", label: "All" }, { value: "taken", label: "Taken" }, { value: "rejected", label: "Rejected" }]} />
      </div>
      <div className="tiles">
        <Tile label="Signals" value={acc?.all.signals ?? DASH} hint={`${acc?.taken.signals ?? 0} taken · ${acc?.rejected.signals ?? 0} rejected by risk rules`} />
        <Tile label="Resolved" value={acc?.all.resolved ?? DASH} hint="target, stop or time reached on the candles" />
        <Tile label="Hit take-profit" value={pct(acc?.all.target_hit_pct, 0)} hint="of resolved signals" />
        <Tile label="Positive after costs" value={pct(acc?.all.positive_pct, 0)} hint={`mean ${pct(acc?.all.mean_net_pct, 2, true)} per signal`} />
        <Tile label="Median per signal" value={<span className={signClass(acc?.all.median_net_pct)}>{pct(acc?.all.median_net_pct, 2, true)}</span>} hint={<Link href="/performance#accuracy">Accuracy by strategy →</Link>} />
      </div>

      {selected ? <SignalChart key={selected.id} s={selected} /> : data && <div className="card empty" style={{ marginBottom: 16 }}>No signals match these filters.</div>}

      <div className="card">
        <h2>Signals</h2>
        <p className="muted small" style={{ marginTop: -6 }}>
          Click a row to chart it. &quot;Ideal outcome&quot; follows the signal on the 5-minute candles after the decision with the backtest&apos;s exit
          rules (entry at the next candle&apos;s open, stop first if a candle touches both levels) after the quoted cost each way, with no slippage
          or failed swaps. Rejected signals show what the risk rules passed up.
        </p>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Token</th>
                <th>Strategy</th>
                <th>Decision</th>
                <th className="num">Entry ref</th>
                <th className="num">Take-profit</th>
                <th className="num">Stop-loss</th>
                <th className="num">Hold</th>
                <th>Paper result</th>
                <th>Ideal outcome</th>
                <th className="num" title="Best and worst move after the entry, up to the ideal exit">Best / worst</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className={`clickable${s.id === selected?.id ? " picked" : ""}`} onClick={() => setPicked(s.id)}>
                  <td className="small">{utc(s.at)}</td>
                  <td>
                    <span className="sym">{s.symbol ?? short(s.mint)}</span>
                  </td>
                  <td>
                    <span className="key-dot" style={{ background: `var(${STRATEGY_STYLE[s.strategy]?.color ?? "--ink-2"})` }} />
                    {STRATEGY_STYLE[s.strategy]?.name ?? s.strategy}
                  </td>
                  <td>
                    <Decision s={s} />
                  </td>
                  <td className="num">{price(s.entry_ref)}</td>
                  <td className="num">{rel(s.target, s.entry_ref)}</td>
                  <td className="num">{rel(s.stop, s.entry_ref)}</td>
                  <td className="num">{s.max_hold_hours}h</td>
                  <td className="small">
                    {s.decision !== "taken" ? (
                      DASH
                    ) : s.status === "closed" ? (
                      <span className={signClass(s.pnl_usd)}>
                        {usd(s.pnl_usd)} ({pct(s.paper_return_pct, 1, true)}) · {s.exit_reason}
                      </span>
                    ) : (
                      "open"
                    )}
                  </td>
                  <td className="small">
                    <Outcome s={s} />
                  </td>
                  <td className="num small">
                    {s.outcome.best_pct == null ? DASH : `${pct(s.outcome.best_pct, 1, true)} / ${pct(s.outcome.worst_pct, 1, true)}`}
                  </td>
                </tr>
              ))}
              {data && rows.length === 0 && (
                <tr>
                  <td colSpan={11} className="empty">
                    No signals yet. Live entry checks are on the <Link href="/strategies">Strategies</Link> page.
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
