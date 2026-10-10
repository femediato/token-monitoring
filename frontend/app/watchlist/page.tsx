"use client";

import Link from "next/link";
import { CATEGORIES, Delta, Reasons, ScoreBar } from "@/components/score";
import { ErrorNotice, Tile } from "@/components/ui";
import { useApi, type Watchlist, type WatchlistEventRow } from "@/lib/api";
import { ago, pct, price, short, usd, utc } from "@/lib/format";

export default function WatchlistPage() {
  const { data, error } = useApi<Watchlist>("/api/watchlist");
  const events = useApi<WatchlistEventRow[]>("/api/watchlist/events?hours=168");
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <div className="muted">Loading…</div>;
  const groups = ["established", "emerging", "breakout", "other"].map((g) => [g, data.members.filter((m) => m.group === g).length] as const);

  return (
    <>
      <div className="page-head">
        <h1>Top-30 watchlist</h1>
        <span className="sub">
          Model {data.model_version} · scored {ago(data.evaluated_at)} · ranked {ago(data.hourly_evaluated_at)} · research signal, not a buy list
        </span>
      </div>
      <div className="tiles">
        <Tile label="Members" value={`${data.members.length} / ${data.size}`} hint={`entry ≥ ${data.entry_score}, retain ≥ ${data.retain_score}`} />
        {groups.map(([g, n]) => (
          <Tile key={g} label={g[0].toUpperCase() + g.slice(1)} value={n} hint={g === "other" ? "filled unused slots" : "target 10"} />
        ))}
      </div>

      {data.members.length === 0 ? (
        <div className="card explain" style={{ marginBottom: 16 }}>
          <h2>The watchlist is empty</h2>
          <p>
            A coin enters only when it is eligible, has data for at least 80% of the model, and scores at least {data.entry_score} in
            two consecutive hourly evaluations. Membership changes happen at 00, 06, 12 and 18 UTC (or as soon as two qualifying
            evaluations exist while the list is first formed); safety failures remove a coin immediately.
          </p>
          <p>
            See <Link href="/eligibility">Eligibility</Link> for every token&apos;s current score and why ineligible tokens are excluded.
          </p>
        </div>
      ) : (
        <div className="table-wrap" style={{ marginBottom: 16 }}>
          <table>
            <thead>
              <tr>
                <th className="num">Rank</th>
                <th>Token</th>
                <th>Group</th>
                <th className="num">Score</th>
                <th className="num" title="Change since the previous hourly evaluation">Δ 1h</th>
                {CATEGORIES.map((c) => (
                  <th key={c.key} className="num" title={`${c.weight}% of total`}>{c.label}</th>
                ))}
                <th className="num">Coverage</th>
                <th className="num">Price</th>
                <th className="num">Mkt cap</th>
                <th className="num">Liquidity</th>
                <th className="num">Vol 24h</th>
                <th>Since</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.members.map((m) => (
                <tr key={m.mint}>
                  <td className="num">{m.rank ?? "—"}</td>
                  <td>
                    <Link href={`/tokens/${m.mint}`}>
                      <span className="sym">{m.symbol ?? short(m.mint)}</span>
                      <span className="name">{m.name}</span>
                    </Link>
                  </td>
                  <td className="ink2">{m.group}</td>
                  <td className="num"><ScoreBar value={m.total} strong /></td>
                  <td className="num"><Delta now={m.hourly_total} prev={m.prev_hourly_total} /></td>
                  {CATEGORIES.map((c) => (
                    <td key={c.key} className="num"><ScoreBar value={m[c.key]} /></td>
                  ))}
                  <td className="num">{m.coverage == null ? "—" : pct(m.coverage * 100, 0)}</td>
                  <td className="num">{price(m.price_usd)}</td>
                  <td className="num">{usd(m.market_cap)}</td>
                  <td className="num">{usd(m.liquidity_usd)}</td>
                  <td className="num">{usd(m.volume_24h)}</td>
                  <td className="muted" title={utc(m.joined_at)}>{ago(m.joined_at)}</td>
                  <td>{m.eligible === false ? <Reasons reasons={m.reasons} max={2} /> : <span className="muted">eligible</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <h2>Membership changes (7 days)</h2>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Token</th>
              <th>Change</th>
              <th>Group</th>
              <th className="num">Score</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {(events.data ?? []).map((e, i) => (
              <tr key={`${e.mint}-${e.at}-${i}`}>
                <td title={utc(e.at)}>{ago(e.at)}</td>
                <td><Link href={`/tokens/${e.mint}`}><span className="sym">{e.symbol ?? short(e.mint)}</span></Link></td>
                <td className={e.action === "add" ? "up" : "down"}>{e.action === "add" ? "added" : "removed"}</td>
                <td className="ink2">{e.group ?? "—"}</td>
                <td className="num">{e.score == null ? "—" : e.score.toFixed(1)}</td>
                <td className="small ink2">{e.reason}</td>
              </tr>
            ))}
            {events.data?.length === 0 && (
              <tr><td colSpan={6} className="empty">No membership changes yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
