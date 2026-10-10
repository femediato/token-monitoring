"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import TimeChart, { toPoints, type SeriesSpec } from "@/components/TimeChart";
import { ErrorNotice, Seg, SecurityBadge, Tile, TrackingBadge } from "@/components/ui";
import { useApi, type Candle, type Quote, type SeriesPoint, type TokenDetail } from "@/lib/api";
import { ago, compactNum, days, DASH, num, pct, price, short, signClass, usd, utc } from "@/lib/format";

const RANGES = [
  { value: "6", label: "6h" },
  { value: "24", label: "24h" },
  { value: "72", label: "3d" },
  { value: "168", label: "7d" },
  { value: "336", label: "14d" },
];

export default function TokenPage({ params }: { params: Promise<{ mint: string }> }) {
  const { mint } = use(params);
  const [hours, setHours] = useState("24");
  const detail = useApi<TokenDetail>(`/api/tokens/${mint}`);
  const series = useApi<{ bucket_seconds: number; points: SeriesPoint[] }>(`/api/tokens/${mint}/series?hours=${hours}`);
  const candles = useApi<{ pool: string | null; candles: Candle[] }>(`/api/tokens/${mint}/candles?hours=${hours}`);
  const quotes = useApi<Quote[]>(`/api/tokens/${mint}/quotes?limit=20`);

  const pts = series.data?.points ?? [];
  const priceSpec = useMemo<SeriesSpec[]>(() => {
    const cs = candles.data?.candles ?? [];
    if (cs.length) {
      return [
        {
          kind: "candles",
          label: "5-min candles",
          format: "price",
          data: cs.map((c) => ({ time: Math.floor(new Date(c.t).getTime() / 1000), open: c.open, high: c.high, low: c.low, close: c.close })),
        },
      ];
    }
    return [{ kind: "line", label: "Price (snapshots)", color: "--series-1", format: "price", data: toPoints(pts, (p) => p.t, (p) => p.price_usd) }];
  }, [candles.data, pts]);
  const liqSpec = useMemo<SeriesSpec[]>(
    () => [{ kind: "line", label: "Liquidity", color: "--series-1", format: "usd", data: toPoints(pts, (p) => p.t, (p) => p.liquidity_usd) }],
    [pts],
  );
  const flowSpec = useMemo<SeriesSpec[]>(
    () => [
      { kind: "line", label: "Buy volume (1h rolling)", color: "--series-1", format: "usd", data: toPoints(pts, (p) => p.t, (p) => p.buy_volume_1h) },
      { kind: "line", label: "Sell volume (1h rolling)", color: "--series-2", format: "usd", data: toPoints(pts, (p) => p.t, (p) => p.sell_volume_1h) },
    ],
    [pts],
  );
  const tradersSpec = useMemo<SeriesSpec[]>(
    () => [{ kind: "line", label: "Unique traders (1h rolling)", color: "--series-1", format: "count", data: toPoints(pts, (p) => p.t, (p) => p.traders_1h) }],
    [pts],
  );

  if (detail.error) return <ErrorNotice error={detail.error} />;
  const d = detail.data;
  if (!d) return <div className="muted">Loading…</div>;
  const t = d.token;
  const usingCandles = (candles.data?.candles.length ?? 0) > 0;

  return (
    <>
      <div className="page-head">
        <Link href="/" className="small">← Universe</Link>
        <h1>
          {t.symbol ?? short(t.mint)} <span className="muted" style={{ fontWeight: 400 }}>{t.name}</span>
        </h1>
        <TrackingBadge status={t.tracking_status} reason={t.tracking_reason} />
        <SecurityBadge status={t.security_status} flags={t.risk_flags} />
        <span className="mono muted" title="Mint address">{t.mint}</span>
        <a href={`https://solscan.io/token/${t.mint}`} target="_blank" rel="noreferrer" className="small">Solscan ↗</a>
        <a href={`https://dexscreener.com/solana/${t.mint}`} target="_blank" rel="noreferrer" className="small">DEX Screener ↗</a>
      </div>

      <div className="tiles">
        <Tile label="Price" value={price(t.price_usd)} hint={<span className={signClass(t.price_change_24h)}>{pct(t.price_change_24h, 1, true)} 24h</span>} />
        <Tile label="Market cap" value={usd(t.market_cap)} hint={`FDV ${usd(t.fdv)}`} />
        <Tile label="Liquidity" value={usd(t.liquidity_usd)} />
        <Tile label="Volume 24h" value={usd(t.volume_24h)} hint={`${pct(t.buy_share_24h, 0)} buys`} />
        <Tile label="Traders 24h" value={compactNum(t.traders_24h)} hint={`${compactNum(t.trades_24h)} trades`} />
        <Tile label="Holders" value={compactNum(t.holder_count)} hint={`Top-10 ${pct(t.top10_pct_adjusted, 1)}`} />
        <Tile label="Sell $1k slippage" value={t.quote_error ? "failed" : pct(t.sell_slippage_pct, 2)} />
        <Tile label="Age" value={days(t.age_days)} hint={`Tracked since ${ago(t.first_seen_at)}`} />
      </div>
      <p className="muted small" style={{ marginTop: -6 }}>
        Snapshot {ago(t.observed_at)} · provider updated {ago(t.source_updated_at)} · class {t.classification} ({t.classification_reason})
      </p>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="chart-head">
          <h2>Price</h2>
          <Seg label="Time range" value={hours} onChange={setHours} options={RANGES} />
        </div>
        {!usingCandles && (
          <div className="notice">
            No 5-minute candles for this token: GeckoTerminal&apos;s free tier only covers the 30 most liquid tokens. Showing
            the 5-minute snapshot price (close only) instead.
          </div>
        )}
        <TimeChart series={priceSpec} tall />
      </div>

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Liquidity</h2>
          <TimeChart series={liqSpec} />
        </div>
        <div className="card">
          <h2>Buy vs sell volume</h2>
          <TimeChart series={flowSpec} />
        </div>
        <div className="card">
          <h2>Unique traders</h2>
          <TimeChart series={tradersSpec} />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Activity windows</h2>
        <div className="table-wrap" style={{ border: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Window</th>
                <th className="num">Price chg</th>
                <th className="num">Volume</th>
                <th className="num">Buy vol</th>
                <th className="num">Sell vol</th>
                <th className="num">Buy share</th>
                <th className="num">Buys</th>
                <th className="num">Sells</th>
                <th className="num">Traders</th>
                <th className="num">Net buyers</th>
                <th className="num">Organic buyers</th>
                <th className="num">Liquidity chg</th>
                <th className="num">Holder chg</th>
              </tr>
            </thead>
            <tbody>
              {d.windows.map((w) => (
                <tr key={w.window}>
                  <td>{w.window}</td>
                  <td className={`num ${signClass(w.price_change_pct)}`}>{pct(w.price_change_pct, 2, true)}</td>
                  <td className="num">{usd(w.volume_usd)}</td>
                  <td className="num">{usd(w.buy_volume_usd)}</td>
                  <td className="num">{usd(w.sell_volume_usd)}</td>
                  <td className="num">{w.volume_usd ? pct(((w.buy_volume_usd ?? 0) / w.volume_usd) * 100, 0) : DASH}</td>
                  <td className="num">{num(w.num_buys)}</td>
                  <td className="num">{num(w.num_sells)}</td>
                  <td className="num">{num(w.num_traders)}</td>
                  <td className="num">{num(w.num_net_buyers)}</td>
                  <td className="num">{num(w.num_organic_buyers)}</td>
                  <td className={`num ${signClass(w.liquidity_change_pct)}`}>{pct(w.liquidity_change_pct, 2, true)}</td>
                  <td className="num">{pct(w.holder_change_pct, 3, true)}</td>
                </tr>
              ))}
              {d.windows.length === 0 && (
                <tr>
                  <td colSpan={13} className="empty">No recent snapshot.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h2>Security (on-chain)</h2>
          {d.security ? (
            <dl className="kv">
              <dt>Status</dt>
              <dd><SecurityBadge status={d.security.status} /></dd>
              <dt>Risk flags</dt>
              <dd>{d.security.risk_flags.length ? d.security.risk_flags.map((f) => <span key={f} className="tag">{f}</span>) : "none"}</dd>
              <dt>Mint authority</dt>
              <dd className="mono">{d.security.mint_authority ?? "disabled"}</dd>
              <dt>Freeze authority</dt>
              <dd className="mono">{d.security.freeze_authority ?? "disabled"}</dd>
              <dt>Token program</dt>
              <dd className="mono">{short(d.security.token_program, 6)}</dd>
              <dt>Extensions</dt>
              <dd>{d.security.extensions?.length ? d.security.extensions.map((e) => <span key={e} className="tag">{e}</span>) : "none"}</dd>
              <dt>Checked</dt>
              <dd>{utc(d.security.checked_at)}</dd>
            </dl>
          ) : (
            <div className="empty">Not checked yet.</div>
          )}
        </div>
        <div className="card">
          <h2>Pools (DEX Screener)</h2>
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Pool</th>
                  <th>DEX</th>
                  <th className="num">Liquidity</th>
                  <th className="num">Quote reserve</th>
                  <th className="num">Vol 24h</th>
                  <th className="num">Buys/Sells 24h</th>
                </tr>
              </thead>
              <tbody>
                {d.pools.map((p) => (
                  <tr key={p.address}>
                    <td className="mono">
                      {short(p.address)} {p.is_primary && <span className="tag">primary</span>}
                    </td>
                    <td>{p.dex_id}{p.labels?.length ? ` (${p.labels.join(", ")})` : ""}</td>
                    <td className="num">{usd(p.liquidity_usd)}</td>
                    <td className="num">{compactNum(p.reserve_quote)} {p.quote_symbol}</td>
                    <td className="num">{usd(p.volume_24h)}</td>
                    <td className="num">{num(p.buys_24h)} / {num(p.sells_24h)}</td>
                  </tr>
                ))}
                {d.pools.length === 0 && (
                  <tr><td colSpan={6} className="empty">No pools collected yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h2>Largest holders</h2>
        {d.holders?.error && <div className="notice error-box">Last holder check failed: {d.holders.error}</div>}
        {d.holders && !d.holders.error ? (
          <>
            <p className="muted small" style={{ marginTop: 0 }}>
              Top-10 adjusted {pct(d.holders.top10_pct_adjusted, 2)} (raw {pct(d.holders.top10_pct_raw, 2)}) · excluded{" "}
              {pct(d.holders.excluded_pct, 2)} of supply · method {d.holders.method_version} · {utc(d.holders.observed_at)}. Program-owned
              accounts (pools, bonding curves, lockers) and burns are excluded; unresolved owners are counted.
            </p>
            <div className="table-wrap" style={{ border: 0 }}>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Token account</th>
                    <th>Owner</th>
                    <th>Owner program</th>
                    <th className="num">% of supply</th>
                    <th>Counted</th>
                  </tr>
                </thead>
                <tbody>
                  {d.holders.accounts.map((a, i) => (
                    <tr key={a.account}>
                      <td className="muted">{i + 1}</td>
                      <td className="mono">{short(a.account, 6)}</td>
                      <td className="mono">{short(a.owner, 6)}</td>
                      <td className="mono muted">{a.owner_program ? short(a.owner_program, 6) : DASH}</td>
                      <td className="num">{pct(a.pct_of_supply, 2)}</td>
                      <td>{a.excluded ? <span className="tag">excluded: {a.excluded}</span> : "yes"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          !d.holders && <div className="empty">Not analysed yet (only tokens with ≥ $100K liquidity, 25 per hour).</div>
        )}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h2>Sell quotes ($1,000 → USDC)</h2>
          <div className="table-wrap" style={{ border: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th className="num">Out (USDC)</th>
                  <th className="num">Slippage</th>
                  <th className="num">Impact</th>
                  <th>Route</th>
                </tr>
              </thead>
              <tbody>
                {(quotes.data ?? []).map((q) => (
                  <tr key={q.quoted_at}>
                    <td>{utc(q.quoted_at)}</td>
                    {q.error ? (
                      <td colSpan={4} className="muted" title={q.error}>failed: {q.error.slice(0, 60)}</td>
                    ) : (
                      <>
                        <td className="num">{q.out_usd?.toFixed(2) ?? DASH}</td>
                        <td className="num">{pct(q.slippage_pct, 2)}</td>
                        <td className="num">{pct(q.price_impact_pct, 3)}</td>
                        <td className="small">{q.route_labels?.join(" → ")}</td>
                      </>
                    )}
                  </tr>
                ))}
                {quotes.data?.length === 0 && (
                  <tr><td colSpan={5} className="empty">No quotes (needs ≥ $150K liquidity and ≥ $500K 24h volume).</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <h2>Discovery &amp; tracking history</h2>
          {d.discovery && d.discovery.appearances > 0 && (
            <dl className="kv" style={{ marginBottom: 12 }}>
              <dt>First discovered</dt>
              <dd>{utc(d.discovery.first_at)}</dd>
              <dt>Appearances</dt>
              <dd>{d.discovery.appearances} (best rank {d.discovery.best_rank ?? DASH}, last {ago(d.discovery.last_at)})</dd>
              <dt>Lists</dt>
              <dd>{d.discovery.lists?.map((l) => <span key={l} className="tag">{l}</span>)}</dd>
            </dl>
          )}
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Change</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {d.tracking_events.map((e, i) => (
                <tr key={i}>
                  <td>{utc(e.at)}</td>
                  <td>{e.from_status ?? "new"} → {e.to_status}</td>
                  <td className="small ink2">{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
