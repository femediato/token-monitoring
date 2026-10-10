"use client";

import { useMemo } from "react";
import TimeChart, { toPoints, type SeriesSpec } from "@/components/TimeChart";
import { CATEGORIES, Reasons, ScoreBar } from "@/components/score";
import { useApi, type ModelInfo, type TokenScores } from "@/lib/api";
import { ago, pct, usd } from "@/lib/format";

type F = Record<string, unknown>;
const n = (v: unknown) => (typeof v === "number" ? v : null);
const x = (v: unknown, d = 2) => (n(v) == null ? "—" : `${(v as number).toFixed(d)}×`);
const p = (v: unknown, d = 1) => (n(v) == null ? "—" : pct(v as number, d, true));
const buckets = (f: F, k: string) =>
  n(f[k]) == null || n(f.buckets_observed) == null ? "—" : `${f[k]} of ${f.buckets_observed}`;

/** The raw input behind each component, as stored with the evaluation. */
const INPUTS: Record<string, (f: F) => string> = {
  relative_volume: (f) => `${x(f.relvol_ratio)} of avg hourly`,
  trader_growth: (f) => `${x(f.trader_growth_ratio)} traders vs 1h ago`,
  transaction_acceleration: (f) => `${x(f.tx_accel_ratio)} trades vs 1h ago`,
  volume_consistency: (f) => `${buckets(f, "active_buckets")} buckets active`,
  return_1h: (f) => p(f.ret_1h_pct),
  return_4h: (f) => p(f.ret_4h_pct),
  trend_strength: (f) => (n(f.ema_ratio) == null ? "—" : `EMA20/EMA50 ${p((f.ema_ratio as number) * 100, 2)}`),
  distance_from_high: (f) => (n(f.dist_from_4h_high_pct) == null ? "—" : `${(f.dist_from_4h_high_pct as number).toFixed(1)}% below 4h high`),
  dex_liquidity: (f) => usd(n(f.liquidity_usd)),
  volume_liquidity_ratio: (f) => x(f.vol_liq_ratio),
  sell_execution_cost: (f) => (n(f.sell_cost_pct) == null ? "—" : `${(f.sell_cost_pct as number).toFixed(2)}% on $1k`),
  liquidity_stability: (f) => (n(f.depth_ratio_24h) == null ? "— (needs 24h of pool data)" : `${x(f.depth_ratio_24h)} pool depth vs 24h ago`),
  buy_volume_share: (f) => (n(f.buy_share_1h) == null ? "—" : pct((f.buy_share_1h as number) * 100, 1)),
  net_buying_vs_liquidity: (f) => (n(f.net_buy_liq_1h) == null ? "—" : pct((f.net_buy_liq_1h as number) * 100, 2, true)),
  buying_persistence: (f) => `${buckets(f, "positive_buckets")} buckets net buying`,
  top10_concentration: (f) => (n(f.top10_pct) == null ? "—" : pct(f.top10_pct as number, 1)),
  largest_holder: (f) => (n(f.top1_pct) == null ? "—" : pct(f.top1_pct as number, 2)),
  liquidity_control: () => "not assessed (constant 50)",
  trading_quality: (f) => `Jupiter organic: ${f.organic_label ?? "—"}`,
};

export default function ScoreCard({ mint }: { mint: string }) {
  const { data } = useApi<TokenScores>(`/api/tokens/${mint}/scores?hours=168`);
  const model = useApi<ModelInfo>("/api/model", { refreshInterval: 0 });
  const spec = useMemo<SeriesSpec[]>(
    () => [{ kind: "line", label: "Total score", color: "--series-1", format: "count", data: toPoints(data?.history ?? [], (h) => h.t, (h) => h.total) }],
    [data],
  );
  if (!data) return null;
  const l = data.latest;
  if (!l) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Selection score</h2>
        <div className="empty">Not evaluated yet (scoring runs every 15 minutes for active tokens).</div>
      </div>
    );
  }
  // Component order follows the model definition (stored JSON does not keep key order).
  const rows = CATEGORIES.flatMap((cat) => {
    const names = model.data?.components[cat.key]?.map((m) => m.name) ?? Object.keys(l.components?.[cat.key] ?? {});
    return names.map((name, i) => ({ cat, name, value: l.components?.[cat.key]?.[name] ?? null, first: i === 0 }));
  });
  const weights = Object.fromEntries(
    Object.entries(model.data?.components ?? {}).flatMap(([cat, comps]) => comps.map((c) => [`${cat}.${c.name}`, c.weight])),
  );

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="chart-head">
        <h2>
          Selection score{" "}
          <span className="muted small" style={{ fontWeight: 400 }}>
            model {data.model_version} · evaluated {ago(l.evaluated_at)}
          </span>
        </h2>
      </div>
      {l.eligible ? (
        <div className="tiles">
          <div className="tile">
            <div className="label">Total {l.rank ? `· rank ${l.rank}` : ""}</div>
            <div className="value">{l.total?.toFixed(1)}</div>
            <div className="hint">coverage {l.coverage == null ? "—" : pct(l.coverage * 100, 0)} · {l.group}</div>
          </div>
          {CATEGORIES.map((c) => (
            <div key={c.key} className="tile">
              <div className="label">{c.label} ({c.weight}%)</div>
              <div className="value">{l[c.key]?.toFixed(0) ?? "—"}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className={`notice${l.critical ? " error-box" : ""}`}>
          {l.critical ? "Critical safety failure — " : "Ineligible — "}not scored. Reasons: <Reasons reasons={l.reasons} max={10} />
        </div>
      )}
      {l.eligible && l.components && (
        <div className="grid cols-2">
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Input</th>
                  <th className="num">Weight</th>
                  <th className="num">Score</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ cat, name, value, first }) => (
                  <tr key={`${cat.key}.${name}`}>
                    <td>
                      {first && <span className="muted small">{cat.label} · </span>}
                      {name.replaceAll("_", " ")}
                    </td>
                    <td className="small ink2">{INPUTS[name]?.(l.features) ?? "—"}</td>
                    <td className="num muted">{weights[`${cat.key}.${name}`] ? `${Math.round(weights[`${cat.key}.${name}`] * 100)}%` : ""}</td>
                    <td className="num"><ScoreBar value={value} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h2 className="small muted" style={{ fontWeight: 500 }}>Total score, last 7 days</h2>
            <TimeChart series={spec} />
            {data.watchlist_events.length > 0 && (
              <table style={{ marginTop: 12 }}>
                <tbody>
                  {data.watchlist_events.map((e, i) => (
                    <tr key={i}>
                      <td className="muted">{ago(e.at)}</td>
                      <td className={e.action === "add" ? "up" : "down"}>{e.action === "add" ? "joined watchlist" : "left watchlist"}</td>
                      <td className="small ink2">{e.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
