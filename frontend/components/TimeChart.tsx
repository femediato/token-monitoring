"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

export type Point = { time: number; value: number };
export type Bar = { time: number; open: number; high: number; low: number; close: number };

export type SeriesSpec =
  | { kind: "line"; label: string; color: string; data: Point[]; format: Format }
  | { kind: "histogram"; label: string; color: string; data: Point[]; format: Format }
  | { kind: "candles"; label: string; data: Bar[]; format: Format };

/** price: adaptive decimals for tiny meme coin prices; usd/count: compact. */
export type Format = "price" | "usd" | "count";

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });

function formatter(format: Format, sample: number) {
  if (format === "price") {
    const digits = sample >= 1 ? 4 : Math.min(12, Math.max(4, -Math.floor(Math.log10(sample || 1)) + 3));
    return (v: number) => v.toFixed(digits);
  }
  if (format === "usd") return (v: number) => "$" + compact.format(v);
  return (v: number) => compact.format(v);
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function useColorScheme() {
  const [scheme, setScheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const set = () => setScheme(mq.matches ? "dark" : "light");
    set();
    mq.addEventListener("change", set);
    return () => mq.removeEventListener("change", set);
  }, []);
  return scheme;
}

export default function TimeChart({ series, tall }: { series: SeriesSpec[]; tall?: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const scheme = useColorScheme();

  useEffect(() => {
    if (!el.current) return;
    const first = series.find((s) => s.data.length);
    const sample =
      first?.kind === "candles" ? first.data[first.data.length - 1].close : (first?.data.at(-1) as Point)?.value ?? 1;
    const fmt = formatter(first?.format ?? "count", Math.abs(sample));
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: cssVar("--surface") },
        textColor: cssVar("--muted"),
        fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--grid") } },
      rightPriceScale: { borderColor: cssVar("--axis") },
      timeScale: { borderColor: cssVar("--axis"), timeVisible: true, secondsVisible: false },
      crosshair: { mode: CrosshairMode.Magnet },
      localization: { priceFormatter: fmt, timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ") + " UTC" },
    });
    for (const s of series) {
      if (s.kind === "candles") {
        const c = chart.addSeries(CandlestickSeries, {
          upColor: cssVar("--up"),
          downColor: cssVar("--down"),
          wickUpColor: cssVar("--up"),
          wickDownColor: cssVar("--down"),
          borderVisible: false,
          title: "",
        });
        c.setData(s.data.map((d) => ({ ...d, time: d.time as UTCTimestamp })));
      } else if (s.kind === "line") {
        const l = chart.addSeries(LineSeries, {
          color: cssVar(s.color),
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerRadius: 4,
        });
        l.setData(s.data.map((d) => ({ ...d, time: d.time as UTCTimestamp })));
      } else {
        const h = chart.addSeries(HistogramSeries, { color: cssVar(s.color), priceLineVisible: false });
        h.setData(s.data.map((d) => ({ ...d, time: d.time as UTCTimestamp })));
      }
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, scheme]);

  const hasData = series.some((s) => s.data.length);
  return (
    <>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.label}>
              <span className="key" style={{ background: s.kind === "candles" ? "var(--up)" : `var(${s.color})` }} />
              {s.label}
            </span>
          ))}
        </div>
      )}
      {hasData ? <div ref={el} className={`chart-box${tall ? " tall" : ""}`} /> : <div className="empty">No data in this range yet.</div>}
    </>
  );
}

export function toPoints<T>(rows: T[], time: (r: T) => string, value: (r: T) => number | null): Point[] {
  const out: Point[] = [];
  for (const r of rows) {
    const v = value(r);
    if (v != null) out.push({ time: Math.floor(new Date(time(r)).getTime() / 1000), value: v });
  }
  return out;
}
