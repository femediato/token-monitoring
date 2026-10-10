import type { ReactNode } from "react";

type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      <span className="dot" aria-hidden />
      {children}
    </span>
  );
}

const SECURITY: Record<string, [Tone, string]> = {
  ok: ["good", "OK"],
  risky: ["critical", "Risky"],
  unknown: ["warning", "Unknown"],
};

export function SecurityBadge({ status, flags }: { status: string | null; flags?: string[] | null }) {
  if (!status) return <Badge tone="neutral" title="Not checked yet">Unchecked</Badge>;
  const [tone, label] = SECURITY[status] ?? ["neutral", status];
  return (
    <Badge tone={tone} title={flags?.length ? flags.join(", ") : undefined}>
      {label}
    </Badge>
  );
}

const TRACKING: Record<string, Tone> = { active: "good", dormant: "warning", excluded: "neutral" };

export function TrackingBadge({ status, reason }: { status: string; reason?: string | null }) {
  return (
    <Badge tone={TRACKING[status] ?? "neutral"} title={reason ?? undefined}>
      {status}
    </Badge>
  );
}

const JOB: Record<string, [Tone, string]> = {
  ok: ["good", "OK"],
  degraded: ["warning", "Degraded"],
  stale: ["critical", "Stale"],
  never_ran: ["serious", "Never ran"],
};

export function JobBadge({ status }: { status: string }) {
  const [tone, label] = JOB[status] ?? ["neutral", status];
  return <Badge tone={tone}>{label}</Badge>;
}

export function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint != null && <div className="hint">{hint}</div>}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="notice error-box">Could not load data: {String((error as Error).message ?? error)}</div>;
}

export function Seg<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
