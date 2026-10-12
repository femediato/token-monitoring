"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { cssVar, formatter, useColorScheme } from "@/components/TimeChart";
import type { ChartData, SignalRow } from "@/lib/api";
import { pct } from "@/lib/format";

/** Strategy identity: fixed categorical order (validated palette slots 1-3) plus a letter,
 * so identity never rests on colour alone. */
export const STRATEGY_STYLE: Record<string, { letter: string; color: string; name: string }> = {
  momentum: { letter: "A", color: "--series-1", name: "A · Momentum" },
  pullback: { letter: "B", color: "--series-2", name: "B · Pullback" },
  range: { letter: "C", color: "--series-3", name: "C · Range" },
};

const BAR = 300;
const floor5 = (iso: string) => (Math.floor(new Date(iso).getTime() / 1000 / BAR) * BAR) as UTCTimestamp;

/** The span a signal's levels are drawn over: from the entry (or the signal) to the exit,
 * the ideal exit, or the end of its holding period. */
export function signalSpan(s: SignalRow, chartEnd: string): [string, string] {
  const start = s.entry_at ?? s.outcome.entry_at ?? s.at;
  const holdEnd = new Date(new Date(start).getTime() + s.max_hold_hours * 3600_000).toISOString();
  let end = s.exit_at ?? s.outcome.exit_at ?? holdEnd;
  if (s.status === "open" || s.outcome.status === "open" || s.outcome.status === "waiting") end = holdEnd < chartEnd ? holdEnd : chartEnd;
  return [start, end];
}

type Readout = { time: string; o: number; h: number; l: number; c: number } | null;

export default function TradeChart({ data, highlight }: { data: ChartData; highlight?: number | null }) {
  const el = useRef<HTMLDivElement>(null);
  const scheme = useColorScheme();
  const [readout, setReadout] = useState<Readout>(null);

  useEffect(() => {
    if (!el.current || data.candles.length === 0) return;
    const last = data.candles[data.candles.length - 1].close;
    const fmt = formatter("price", Math.abs(last));
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
      crosshair: { mode: CrosshairMode.Normal },
      localization: {
        priceFormatter: fmt,
        timeFormatter: (t: number) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ") + " UTC",
      },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: cssVar("--up"),
      downColor: cssVar("--down"),
      wickUpColor: cssVar("--up"),
      wickDownColor: cssVar("--down"),
      borderVisible: false,
      priceLineVisible: false,
    });
    const bars = data.candles.map((c) => ({ time: floor5(c.t), open: c.open, high: c.high, low: c.low, close: c.close }));
    candles.setData(bars);
    const known = new Set(bars.map((b) => b.time as number));
    const first = bars[0].time as number;
    const lastT = bars[bars.length - 1].time as number;
    // Snap a time onto an existing candle so levels never add slots. Line ends are clamped
    // to the window; a marker outside it is not drawn (null).
    const snap = (iso: string, clamp = false): UTCTimestamp | null => {
      let t = floor5(iso) as number;
      if (t < first || t > lastT) {
        if (!clamp) return null;
        t = Math.min(Math.max(t, first), lastT);
      }
      while (!known.has(t) && t > first) t -= BAR;
      return known.has(t) ? (t as UTCTimestamp) : null;
    };

    const markers: SeriesMarker<Time>[] = [];
    // Draw the highlighted signal last, on top.
    const ordered = [...data.signals].sort((a, b) => Number(a.id === highlight) - Number(b.id === highlight));
    for (const s of ordered) {
      const st = STRATEGY_STYLE[s.strategy] ?? { letter: "?", color: "--ink-2", name: s.strategy };
      const strong = highlight == null || s.id === highlight;
      const [from, to] = signalSpan(s, data.end);
      const visible = floor5(to) >= first && floor5(from) <= lastT;
      const a = visible ? snap(from, true) : null;
      const b = visible ? snap(to, true) : null;
      if (a != null && b != null && b > a) {
        const levels: [number, string][] = [
          [s.target, "--up-text"],
          [s.entry_ref, "--ink-2"],
          [s.stop, "--down-text"],
        ];
        for (const [value, color] of levels) {
          const line = chart.addSeries(LineSeries, {
            color: cssVar(color),
            lineWidth: strong ? 2 : 1,
            lineStyle: color === "--ink-2" ? LineStyle.Dotted : LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          line.setData([
            { time: a, value },
            { time: b, value },
          ]);
        }
      }
      // Marker labels are text: they wear ink tokens, never the strategy colour.
      const ink = (token: string) => cssVar(strong ? token : "--muted");
      const signalAt = snap(s.signal_bar);
      if (s.decision !== "taken") {
        if (signalAt != null)
          markers.push({
            time: signalAt,
            position: "belowBar",
            shape: "circle",
            color: ink("--ink-2"),
            text: `${st.letter} ${s.decision === "rejected" ? "rejected" : "cancelled"}`,
            size: 1,
          });
        continue;
      }
      const entry = s.entry_at ? snap(s.entry_at) : null;
      if (entry != null) markers.push({ time: entry, position: "belowBar", shape: "arrowUp", color: ink("--ink"), text: `${st.letter} buy`, size: 1 });
      if (s.exit_at) {
        const exit = snap(s.exit_at);
        if (exit != null)
          markers.push({
            time: exit,
            position: "aboveBar",
            shape: "arrowDown",
            color: ink((s.pnl_usd ?? 0) > 0 ? "--up-text" : "--down-text"),
            text: `${st.letter} ${(s.exit_reason ?? "exit").replace("_retry", "")} ${pct(s.paper_return_pct, 1, true)}`,
            size: 1,
          });
      }
    }
    markers.sort((x, y) => (x.time as number) - (y.time as number));
    createSeriesMarkers(candles, markers);

    chart.subscribeCrosshairMove((param) => {
      const d = param.time != null ? (param.seriesData.get(candles) as { open: number; high: number; low: number; close: number } | undefined) : undefined;
      setReadout(
        d && param.time != null
          ? { time: new Date((param.time as number) * 1000).toISOString().slice(5, 16).replace("T", " ") + " UTC", o: d.open, h: d.high, l: d.low, c: d.close }
          : null,
      );
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, highlight, scheme]);

  if (data.candles.length === 0) return <div className="empty">No candles for this token in this window.</div>;
  const fmt = formatter("price", Math.abs(data.candles[data.candles.length - 1].close));
  const strategies = [...new Set(data.signals.map((s) => s.strategy))].sort();
  return (
    <>
      <div className="legend">
        <span>{strategies.map((k) => STRATEGY_STYLE[k]?.name ?? k).join(" · ")}</span>
        <span><span className="key line dashed" style={{ borderColor: "var(--up-text)" }} />Take-profit</span>
        <span><span className="key line dotted" style={{ borderColor: "var(--ink-2)" }} />Entry reference</span>
        <span><span className="key line dashed" style={{ borderColor: "var(--down-text)" }} />Stop-loss</span>
        <span className="muted">markers: strategy letter · ▲ buy · ▼ exit · ● signal not taken</span>
      </div>
      <div className="small ink2 mono readout" aria-live="polite">
        {readout ? `${readout.time}  O ${fmt(readout.o)}  H ${fmt(readout.h)}  L ${fmt(readout.l)}  C ${fmt(readout.c)}` : "Hover the chart for prices"}
      </div>
      <div ref={el} className="chart-box tall" />
    </>
  );
}
