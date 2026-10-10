const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export const DASH = "—";

export function usd(v: number | null | undefined): string {
  if (v == null) return DASH;
  return "$" + compact.format(v);
}

/** Prices span many orders of magnitude for meme coins; keep 4 significant digits. */
export function price(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v >= 1) return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (v === 0) return "$0";
  const digits = Math.min(12, Math.max(4, -Math.floor(Math.log10(v)) + 3));
  return "$" + v.toFixed(digits);
}

export function num(v: number | null | undefined): string {
  return v == null ? DASH : int.format(v);
}

export function compactNum(v: number | null | undefined): string {
  return v == null ? DASH : compact.format(v);
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (v == null) return DASH;
  const s = v.toFixed(digits) + "%";
  return signed && v > 0 ? "+" + s : s;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
}

export function utc(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function days(v: number | null | undefined): string {
  if (v == null) return DASH;
  if (v < 1) return `${Math.round(v * 24)}h`;
  return `${v < 10 ? v.toFixed(1) : Math.round(v)}d`;
}

export function short(addr: string | null | undefined, n = 4): string {
  if (!addr) return DASH;
  return addr.length <= n * 2 + 1 ? addr : `${addr.slice(0, n)}…${addr.slice(-n)}`;
}

export function signClass(v: number | null | undefined): string {
  if (v == null || v === 0) return "";
  return v > 0 ? "up" : "down";
}
