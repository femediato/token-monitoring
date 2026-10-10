"use client";

import Link from "next/link";
import { useState } from "react";
import { ErrorNotice, Seg, TrackingBadge } from "@/components/ui";
import { useApi, type TrackingEvent } from "@/lib/api";
import { ago, short, utc } from "@/lib/format";

type To = "all" | "active" | "dormant" | "excluded";

export default function DiscoveryPage() {
  const [hours, setHours] = useState("24");
  const [to, setTo] = useState<To>("all");
  const qs = new URLSearchParams({ hours, limit: "500" });
  if (to !== "all") qs.set("to_status", to);
  const { data, error } = useApi<TrackingEvent[]>(`/api/events?${qs}`);
  const isNew = (e: TrackingEvent) => e.from_status === null;

  return (
    <>
      <div className="page-head">
        <h1>Discovery feed</h1>
        <span className="sub">Every change of tracking status, with the recorded reason</span>
      </div>
      <div className="toolbar">
        <Seg
          label="Time range"
          value={hours}
          onChange={setHours}
          options={[
            { value: "1", label: "1h" },
            { value: "6", label: "6h" },
            { value: "24", label: "24h" },
            { value: "168", label: "7d" },
          ]}
        />
        <Seg
          label="New status"
          value={to}
          onChange={setTo}
          options={[
            { value: "all", label: "All changes" },
            { value: "active", label: "→ Active" },
            { value: "dormant", label: "→ Dormant" },
            { value: "excluded", label: "→ Excluded" },
          ]}
        />
        {data && (
          <span className="muted">
            {data.length} events · {data.filter(isNew).length} new tokens
          </span>
        )}
      </div>
      <ErrorNotice error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Token</th>
              <th>Change</th>
              <th>Reason</th>
              <th>Class</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((e, i) => (
              <tr key={`${e.token_mint}-${e.at}-${i}`}>
                <td title={utc(e.at)}>{ago(e.at)}</td>
                <td>
                  <Link href={`/tokens/${e.token_mint}`}>
                    <span className="sym">{e.symbol ?? short(e.token_mint)}</span>
                    <span className="name">{e.name}</span>
                  </Link>
                </td>
                <td>
                  <span className="muted">{e.from_status ?? "new"} →</span> <TrackingBadge status={e.to_status} />
                </td>
                <td className="small ink2">{e.reason}</td>
                <td className="small ink2">{e.classification?.replace("_", " ")}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr>
                <td colSpan={5} className="empty">No changes in this range.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
