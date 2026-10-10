import type { Num } from "@/lib/api";

export const CATEGORIES = [
  { key: "activity", label: "Activity", weight: 25 },
  { key: "momentum", label: "Momentum", weight: 25 },
  { key: "liquidity", label: "Liquidity", weight: 20 },
  { key: "buying", label: "Buying", weight: 15 },
  { key: "risk", label: "Risk quality", weight: 15 },
] as const;

/** A 0-100 score as a number with a thin magnitude bar (single hue). */
export function ScoreBar({ value, strong }: { value: Num | undefined; strong?: boolean }) {
  if (value == null) return <span className="muted">—</span>;
  const v = Math.max(0, Math.min(100, value));
  return (
    <span className="scorebar" title={value.toFixed(1)}>
      <span className={strong ? "num strong" : "num"}>{value.toFixed(0)}</span>
      <span className="track" aria-hidden>
        <span className="fill" style={{ width: `${v}%` }} />
      </span>
    </span>
  );
}

export function Delta({ now, prev }: { now: Num; prev: Num }) {
  if (now == null || prev == null) return <span className="muted">—</span>;
  const d = now - prev;
  if (Math.abs(d) < 0.05) return <span className="muted">0.0</span>;
  return <span className={d > 0 ? "up" : "down"}>{(d > 0 ? "+" : "") + d.toFixed(1)}</span>;
}

/** Reason codes are machine-readable; show them as compact tags. */
export function Reasons({ reasons, max = 4 }: { reasons: string[] | null | undefined; max?: number }) {
  if (!reasons?.length) return <span className="muted">—</span>;
  return (
    <span>
      {reasons.slice(0, max).map((r) => (
        <span key={r} className="tag">{r}</span>
      ))}
      {reasons.length > max && <span className="muted small">+{reasons.length - max}</span>}
    </span>
  );
}
