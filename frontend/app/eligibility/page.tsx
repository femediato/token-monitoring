"use client";

import Link from "next/link";
import { useState } from "react";
import { CATEGORIES, Reasons, ScoreBar } from "@/components/score";
import { ErrorNotice, Seg, Tile } from "@/components/ui";
import { useApi, type Evaluations, type InfraCandidates } from "@/lib/api";
import { ago, num, pct, short, usd } from "@/lib/format";

type View = "eligible" | "ineligible" | "all";

export default function EligibilityPage() {
  const [view, setView] = useState<View>("eligible");
  const [reason, setReason] = useState<string | null>(null);
  const qs = new URLSearchParams({ view: reason ? "all" : view });
  if (reason) qs.set("reason", reason);
  const { data, error } = useApi<Evaluations>(`/api/evaluations?${qs}`);
  const infra = useApi<InfraCandidates>("/api/holders/infrastructure-candidates?min_tokens=5");
  if (error) return <ErrorNotice error={error} />;
  if (!data) return <div className="muted">Loading…</div>;
  const c = data.counts;

  return (
    <>
      <div className="page-head">
        <h1>Eligibility &amp; scores</h1>
        <span className="sub">Latest evaluation {ago(data.evaluated_at)} · every active token, with reason codes</span>
      </div>
      <div className="tiles">
        <Tile label="Evaluated" value={num(c.evaluated)} />
        <Tile label="Eligible" value={num(c.eligible)} hint="passed every hard rule" />
        <Tile label="Score ≥ entry" value={num(c.above_entry)} hint="before the two-evaluation rule" />
        <Tile label="Critical failures" value={num(c.critical)} hint="authorities, Token-2022, no sell route" />
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16, gridTemplateColumns: "minmax(0,1fr) minmax(0,2fr)" }}>
        <div className="card">
          <h2>Why tokens are ineligible</h2>
          <p className="muted small" style={{ marginTop: -6 }}>A token can fail several rules. Click a reason to list its tokens.</p>
          <table>
            <tbody>
              {data.reasons.map((r) => (
                <tr
                  key={r.reason}
                  className={`reason-row${reason === r.reason ? " selected" : ""}`}
                  onClick={() => setReason(reason === r.reason ? null : r.reason)}
                >
                  <td className="mono">{r.reason}</td>
                  <td className="num">{num(r.n)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ minWidth: 0 }}>
          <div className="chart-head">
            <h2>{reason ? `Tokens failing: ${reason}` : "Tokens"}</h2>
            {reason ? (
              <button className="btn" onClick={() => setReason(null)}>Clear filter</button>
            ) : (
              <Seg label="View" value={view} onChange={setView} options={[
                { value: "eligible", label: "Eligible" },
                { value: "ineligible", label: "Ineligible" },
                { value: "all", label: "All" },
              ]} />
            )}
          </div>
          <div className="table-wrap" style={{ border: 0, maxHeight: 620, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th className="num">Score</th>
                  {CATEGORIES.map((k) => <th key={k.key} className="num">{k.label}</th>)}
                  <th className="num">Cover</th>
                  <th>Group</th>
                  <th className="num">Mkt cap</th>
                  <th className="num">Liquidity</th>
                  <th>Reasons</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((t) => (
                  <tr key={t.mint}>
                    <td>
                      <Link href={`/tokens/${t.mint}`}><span className="sym">{t.symbol ?? short(t.mint)}</span></Link>
                      {t.member && <span className="tag" style={{ marginLeft: 6 }}>watchlist</span>}
                    </td>
                    <td className="num"><ScoreBar value={t.total} strong /></td>
                    {CATEGORIES.map((k) => <td key={k.key} className="num"><ScoreBar value={t[k.key]} /></td>)}
                    <td className="num">{t.coverage == null ? "—" : pct(t.coverage * 100, 0)}</td>
                    <td className="ink2">{t.group ?? "—"}</td>
                    <td className="num">{usd(t.market_cap)}</td>
                    <td className="num">{usd(t.liquidity_usd)}</td>
                    <td>{t.eligible ? <span className="muted">eligible</span> : <Reasons reasons={t.reasons} max={3} />}</td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr><td colSpan={12} className="empty">No tokens.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card explain">
        <h2>Possible exchange or custody wallets</h2>
        <p>
          Wallets among the largest holders of many unrelated tokens are usually exchanges or custodians. They count toward holder
          concentration until verified: check the label on Solscan, then add the address with its label and source to{" "}
          <code>backend/src/autolp/reference/infrastructure_wallets.json</code>. Program-controlled owners (pool vaults, multisigs,
          lockers) are already excluded automatically.
        </p>
        <table>
          <thead>
            <tr>
              <th>Owner</th>
              <th className="num">Tokens (of {infra.data?.tokens_analysed ?? "—"})</th>
              <th className="num">Avg %</th>
              <th className="num">Max %</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {(infra.data?.candidates ?? []).map((o) => (
              <tr key={o.owner}>
                <td className="mono"><a href={`https://solscan.io/account/${o.owner}`} target="_blank" rel="noreferrer">{o.owner}</a></td>
                <td className="num">{o.tokens}</td>
                <td className="num">{pct(o.avg_pct, 2)}</td>
                <td className="num">{pct(o.max_pct, 2)}</td>
                <td className="small ink2">{o.excluded ? "excluded" : "counted"}</td>
              </tr>
            ))}
            {infra.data?.candidates.length === 0 && (
              <tr><td colSpan={5} className="empty">No candidates yet (needs holder analyses on more tokens).</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
