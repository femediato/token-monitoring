"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ErrorNotice, Seg, SecurityBadge, TrackingBadge } from "@/components/ui";
import { useApi, type TokenList, type TokenRow } from "@/lib/api";
import { ago, compactNum, days, pct, price, short, signClass, usd } from "@/lib/format";

type Col = {
  key: string;
  label: string;
  title?: string;
  num?: boolean;
  sort?: string;
  render: (t: TokenRow) => React.ReactNode;
};

const COLUMNS: Col[] = [
  {
    key: "token",
    label: "Token",
    sort: "symbol",
    render: (t) => (
      <Link href={`/tokens/${t.mint}`}>
        <span className="sym">{t.symbol ?? short(t.mint)}</span>
        <span className="name">{t.name}</span>
      </Link>
    ),
  },
  { key: "age", label: "Age", num: true, sort: "age_days", title: "Since first pool", render: (t) => days(t.age_days) },
  { key: "price", label: "Price", num: true, sort: "price_usd", render: (t) => price(t.price_usd) },
  { key: "mcap", label: "Mkt cap", num: true, sort: "market_cap", render: (t) => usd(t.market_cap) },
  { key: "liq", label: "Liquidity", num: true, sort: "liquidity_usd", render: (t) => usd(t.liquidity_usd) },
  { key: "vol", label: "Vol 24h", num: true, sort: "volume_24h", render: (t) => usd(t.volume_24h) },
  {
    key: "c1",
    label: "1h",
    num: true,
    sort: "price_change_1h",
    title: "Price change 1h",
    render: (t) => <span className={signClass(t.price_change_1h)}>{pct(t.price_change_1h, 1, true)}</span>,
  },
  {
    key: "c24",
    label: "24h",
    num: true,
    sort: "price_change_24h",
    title: "Price change 24h",
    render: (t) => <span className={signClass(t.price_change_24h)}>{pct(t.price_change_24h, 1, true)}</span>,
  },
  { key: "trades", label: "Trades 24h", num: true, sort: "trades_24h", render: (t) => compactNum(t.trades_24h) },
  { key: "traders", label: "Traders 24h", num: true, sort: "traders_24h", title: "Unique traders (Jupiter)", render: (t) => compactNum(t.traders_24h) },
  { key: "buy", label: "Buy % 24h", num: true, sort: "buy_share_24h", title: "Buy volume / total volume", render: (t) => pct(t.buy_share_24h, 0) },
  { key: "top10", label: "Top-10", num: true, sort: "top10_pct_adjusted", title: "Top-10 holder share, excluding pools and burns", render: (t) => pct(t.top10_pct_adjusted, 1) },
  {
    key: "slip",
    label: "Sell $1k",
    num: true,
    sort: "sell_slippage_pct",
    title: "Slippage selling $1,000 to USDC (Jupiter quote)",
    render: (t) => (t.quote_error ? <span className="muted" title={t.quote_error}>failed</span> : pct(t.sell_slippage_pct, 2)),
  },
  { key: "sec", label: "Security", render: (t) => <SecurityBadge status={t.security_status} flags={t.risk_flags} /> },
  { key: "class", label: "Class", render: (t) => <span className="small ink2" title={t.classification_reason ?? undefined}>{t.classification?.replace("_", " ")}</span> },
  { key: "upd", label: "Updated", num: true, render: (t) => <span className="muted">{ago(t.observed_at)}</span> },
];

const PAGE = 50;

function Universe() {
  const router = useRouter();
  const sp = useSearchParams();
  const status = (sp.get("status") ?? "active") as "active" | "dormant" | "excluded" | "all";
  const sort = sp.get("sort") ?? "liquidity_usd";
  const order = (sp.get("order") ?? "desc") as "asc" | "desc";
  const security = sp.get("security") ?? "";
  const classification = sp.get("classification") ?? "";
  const minLiq = sp.get("min_liquidity") ?? "";
  const page = Number(sp.get("page") ?? 0);
  const [q, setQ] = useState(sp.get("q") ?? "");

  const set = (patch: Record<string, string | number | null>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    if (!("page" in patch)) next.delete("page");
    router.replace(`/?${next.toString()}`, { scroll: false });
  };

  useEffect(() => {
    const id = setTimeout(() => {
      if (q !== (sp.get("q") ?? "")) set({ q });
    }, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const params = new URLSearchParams({ status, sort, order, limit: String(PAGE), offset: String(page * PAGE) });
  if (security) params.set("security", security);
  if (classification) params.set("classification", classification);
  if (minLiq) params.set("min_liquidity", minLiq);
  if (sp.get("q")) params.set("q", sp.get("q")!);
  const { data, error, isLoading } = useApi<TokenList>(`/api/tokens?${params}`);

  const onSort = (key?: string) => {
    if (!key) return;
    set({ sort: key, order: sort === key && order === "desc" ? "asc" : "desc" });
  };

  return (
    <>
      <div className="page-head">
        <h1>Token universe</h1>
        <span className="sub">
          {data ? `${data.total} tokens` : isLoading ? "Loading…" : ""} · latest Jupiter snapshot per token · missing values shown as —
        </span>
      </div>
      <div className="toolbar">
        <Seg
          label="Tracking status"
          value={status}
          onChange={(v) => set({ status: v })}
          options={[
            { value: "active", label: "Active" },
            { value: "dormant", label: "Dormant" },
            { value: "excluded", label: "Excluded" },
            { value: "all", label: "All" },
          ]}
        />
        <input type="search" placeholder="Search symbol, name or mint" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        <select value={classification} onChange={(e) => set({ classification: e.target.value })} aria-label="Classification">
          <option value="">Any class</option>
          <option value="meme">Meme</option>
          <option value="meme_candidate">Meme candidate</option>
          <option value="excluded">Excluded</option>
        </select>
        <select value={security} onChange={(e) => set({ security: e.target.value })} aria-label="Security">
          <option value="">Any security</option>
          <option value="ok">OK</option>
          <option value="risky">Risky</option>
          <option value="unknown">Unknown</option>
          <option value="unchecked">Unchecked</option>
        </select>
        <select value={minLiq} onChange={(e) => set({ min_liquidity: e.target.value })} aria-label="Minimum liquidity">
          <option value="">Any liquidity</option>
          <option value="25000">≥ $25K</option>
          <option value="150000">≥ $150K</option>
          <option value="1000000">≥ $1M</option>
        </select>
      </div>
      <ErrorNotice error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  className={`${c.num ? "num " : ""}${c.sort ? "sortable " : ""}${c.sort === sort ? "sorted" : ""}`}
                  onClick={() => onSort(c.sort)}
                  aria-sort={c.sort === sort ? (order === "asc" ? "ascending" : "descending") : undefined}
                >
                  {c.label}
                  {c.sort === sort ? (order === "asc" ? " ↑" : " ↓") : ""}
                </th>
              ))}
              {status === "all" && <th>Status</th>}
            </tr>
          </thead>
          <tbody>
            {data?.items.map((t) => (
              <tr key={t.mint}>
                {COLUMNS.map((c) => (
                  <td key={c.key} className={c.num ? "num" : undefined}>
                    {c.render(t)}
                  </td>
                ))}
                {status === "all" && (
                  <td>
                    <TrackingBadge status={t.tracking_status} reason={t.tracking_reason} />
                  </td>
                )}
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="empty">
                  No tokens match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {data && data.total > PAGE && (
        <div className="pager">
          <span>
            {page * PAGE + 1}–{Math.min(data.total, (page + 1) * PAGE)} of {data.total}
          </span>
          <button className="btn" disabled={page === 0} onClick={() => set({ page: page - 1 })}>
            Previous
          </button>
          <button className="btn" disabled={(page + 1) * PAGE >= data.total} onClick={() => set({ page: page + 1 })}>
            Next
          </button>
        </div>
      )}
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Universe />
    </Suspense>
  );
}
