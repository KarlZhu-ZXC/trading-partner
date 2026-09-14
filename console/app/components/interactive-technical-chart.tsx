"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Eraser } from "lucide-react";
import type {
  CandleType,
  Chart,
  DeepPartial,
  KLineData,
  Styles,
} from "klinecharts";
import { chartStructures, type ChartStructure } from "../lib/chart-research-context";
import { Button, Input, Select } from "./ui/controls";
import styles from "../styles/technical-chart.module.css";

export type TechnicalWireBar = {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};

type SmartMoneyZone = {
  kind: "order_block" | "fair_value_gap" | "premium" | "equilibrium" | "discount";
  scope: "internal" | "swing";
  direction: "bullish" | "bearish" | null;
  low: string;
  high: string;
  created_at: string;
  confirmed_at: string;
  status: string;
};

type SmartMoneyEvent = {
  scope: "internal" | "swing";
  event: "bos" | "choch";
  direction: "bullish" | "bearish";
  price: string;
  occurred_at: string;
  broken_swing_at: string;
};

type SmartMoneyLiquidity = {
  kind: "equal_high" | "equal_low";
  price: string;
  first_swing_at: string;
  second_swing_at: string;
  confirmed_at: string;
  status: string;
};

export type TechnicalChartTimeframe = {
  interval: "1d" | "1w";
  trend_state: string;
  smart_money: null | {
    swings?: Array<{ scope: string; label: string; price: string; occurred_at: string; confirmed_at: string }>;
    algorithm_version?: string;
    trend: string;
    atr_200_ready: boolean;
    limitations: string[];
    structure_events: SmartMoneyEvent[];
    order_blocks: SmartMoneyZone[];
    fair_value_gaps: SmartMoneyZone[];
    liquidity_levels: SmartMoneyLiquidity[];
    value_zones: SmartMoneyZone[];
  };
};

export type InteractiveTechnicalScene = {
  instrument_id: string;
  bars_interval: "1d" | "1w";
  bars: TechnicalWireBar[];
  timeframes: TechnicalChartTimeframe[];
  price_basis: string;
  as_of?: string;
  algorithm_version?: string;
};

type ChartColors = {
  ink: string;
  muted: string;
  line: string;
  panel: string;
  positive: string;
  negative: string;
  accent: string;
  amber: string;
};

type ZoneOverlayData = { color: string; fill: string; label: string };
type StructureOverlayData = { color: string; dashed: boolean; label: string };

const INDICATORS = ["EMA", "MA", "BOLL", "VOL", "MACD", "RSI", "KDJ"] as const;
const OVERLAY_INDICATORS = new Set(["EMA", "MA", "BOLL"]);
const DRAWING_TOOLS = [
  { name: "segment", label: "Trend Line" },
  { name: "horizontalStraightLine", label: "Horizontal Line" },
  { name: "priceChannelLine", label: "Price Channel" },
  { name: "parallelStraightLine", label: "Parallel Lines" },
  { name: "fibonacciLine", label: "Fibonacci" },
  { name: "brush", label: "Brush" },
  { name: "simpleAnnotation", label: "Annotation" },
] as const;

const CHART_TYPES: Array<{ value: CandleType; label: string }> = [
  { value: "candle_solid", label: "Solid Candles" },
  { value: "candle_stroke", label: "Hollow Candles" },
  { value: "candle_up_stroke", label: "Hollow Up" },
  { value: "candle_down_stroke", label: "Hollow Down" },
  { value: "ohlc", label: "OHLC Bars" },
  { value: "area", label: "Area" },
];

function cssColor(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function chartColors(): ChartColors {
  return {
    ink: cssColor("--ink", "#e5edf7"),
    muted: cssColor("--muted", "#9babbe"),
    line: cssColor("--line", "#223047"),
    panel: cssColor("--panel", "#101a2b"),
    positive: cssColor("--positive", "#62dbaf"),
    negative: cssColor("--red", "#fb9292"),
    accent: cssColor("--accent", "#70cff5"),
    amber: cssColor("--amber", "#f0c476"),
  };
}

function chartStyles(type: CandleType): DeepPartial<Styles> {
  const colors = chartColors();
  return {
    grid: {
      horizontal: { color: colors.line, style: "dashed" },
      vertical: { color: colors.line, style: "dashed" },
    },
    candle: {
      type,
      bar: {
        upColor: colors.positive,
        downColor: colors.negative,
        noChangeColor: colors.muted,
        upBorderColor: colors.positive,
        downBorderColor: colors.negative,
        noChangeBorderColor: colors.muted,
        upWickColor: colors.positive,
        downWickColor: colors.negative,
        noChangeWickColor: colors.muted,
      },
      area: {
        lineColor: colors.accent,
        backgroundColor: [
          { offset: 0, color: `${colors.accent}08` },
          { offset: 1, color: `${colors.accent}32` },
        ],
      },
    },
    xAxis: {
      axisLine: { color: colors.line },
      tickLine: { color: colors.line },
      tickText: { color: colors.muted },
    },
    yAxis: {
      axisLine: { color: colors.line },
      tickLine: { color: colors.line },
      tickText: { color: colors.muted },
    },
    separator: { color: colors.line, activeBackgroundColor: colors.accent },
    crosshair: {
      horizontal: { line: { color: colors.muted }, text: { backgroundColor: colors.muted } },
      vertical: { line: { color: colors.muted }, text: { backgroundColor: colors.muted } },
    },
  };
}

function timestamp(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function number(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function chartData(bars: TechnicalWireBar[]): KLineData[] {
  return bars.flatMap((bar) => {
    const time = timestamp(bar.timestamp);
    const open = number(bar.open);
    const high = number(bar.high);
    const low = number(bar.low);
    const close = number(bar.close);
    const volume = number(bar.volume);
    return time === null || open === null || high === null || low === null || close === null
      ? []
      : [{ timestamp: time, open, high, low, close, volume: volume ?? undefined }];
  });
}

function pricePrecision(bars: TechnicalWireBar[]): number {
  return Math.min(8, Math.max(0, ...bars.slice(-40).map((bar) => bar.close.split(".")[1]?.length ?? 0)));
}

function registerTradingPartnerOverlays(module: typeof import("klinecharts")) {
  if (!module.getSupportedOverlays().includes("tpZone")) {
    module.registerOverlay<ZoneOverlayData>({
      name: "tpZone",
      totalStep: 3,
      needDefaultPointFigure: false,
      needDefaultXAxisFigure: false,
      needDefaultYAxisFigure: false,
      createPointFigures: ({ coordinates, overlay }) => {
        if (coordinates.length < 2) return [];
        const [first, second] = coordinates;
        const x = Math.min(first.x, second.x);
        const y = Math.min(first.y, second.y);
        const width = Math.max(1, Math.abs(second.x - first.x));
        const height = Math.max(1, Math.abs(second.y - first.y));
        return [
          {
            type: "rect",
            attrs: { x, y, width, height },
            styles: {
              style: "stroke_fill",
              color: overlay.extendData.fill,
              borderColor: overlay.extendData.color,
              borderSize: 1,
            },
            ignoreEvent: false,
          },
          {
            type: "text",
            attrs: { x: x + 5, y: y + 4, text: overlay.extendData.label, baseline: "top" },
            styles: { color: overlay.extendData.color, size: 10, backgroundColor: "transparent" },
            ignoreEvent: false,
          },
        ];
      },
    });
  }
  if (!module.getSupportedOverlays().includes("tpStructure")) {
    module.registerOverlay<StructureOverlayData>({
      name: "tpStructure",
      totalStep: 3,
      needDefaultPointFigure: false,
      needDefaultXAxisFigure: false,
      needDefaultYAxisFigure: false,
      createPointFigures: ({ coordinates, overlay }) => {
        if (coordinates.length < 2) return [];
        const [first, second] = coordinates;
        return [
          {
            type: "line",
            attrs: { coordinates: [first, second] },
            styles: {
              color: overlay.extendData.color,
              size: 1,
              style: overlay.extendData.dashed ? "dashed" : "solid",
              dashedValue: [4, 3],
            },
            ignoreEvent: false,
          },
          {
            type: "text",
            attrs: {
              x: (first.x + second.x) / 2,
              y: first.y - 5,
              text: overlay.extendData.label,
              align: "center",
              baseline: "bottom",
            },
            styles: { color: overlay.extendData.color, size: 10, backgroundColor: "transparent" },
            ignoreEvent: false,
          },
        ];
      },
    });
  }
}

function addDerivedOverlays(chart: Chart, timeframe: TechnicalChartTimeframe, finalTime: number, cutoff: string, onSelect: (id: string) => void) {
  chart.removeOverlay({ groupId: "derived:smc" });
  const smartMoney = timeframe.smart_money;
  if (!smartMoney) return;
  const colors = chartColors();
  const visible = new Set(chartStructures(timeframe, cutoff || undefined).map((r) => r.id));
  for (const [index, event] of smartMoney.structure_events.entries()) {
    if (!visible.has(`event-${index}`)) continue;
    const from = timestamp(event.broken_swing_at);
    const to = timestamp(event.occurred_at);
    const price = number(event.price);
    if (from === null || to === null || price === null) continue;
    const color = event.direction === "bullish" ? colors.positive : colors.negative;
    chart.createOverlay({
      name: "tpStructure",
      groupId: "derived:smc",
      lock: true,
      points: [{ timestamp: from, value: price }, { timestamp: to, value: price }],
      onClick: () => { onSelect(`event-${index}`); return true; },
      extendData: { color, dashed: event.scope === "internal", label: `${event.scope === "internal" ? "I" : "S"} ${event.event.toUpperCase()}` },
    });
  }
  for (const [index, level] of smartMoney.liquidity_levels.entries()) {
    if (!visible.has(`liquidity-${index}`)) continue;
    const from = timestamp(level.first_swing_at);
    const to = timestamp(level.second_swing_at);
    const price = number(level.price);
    if (from === null || to === null || price === null) continue;
    chart.createOverlay({
      name: "tpStructure",
      groupId: "derived:smc",
      lock: true,
      points: [{ timestamp: from, value: price }, { timestamp: to, value: price }],
      onClick: () => { onSelect(`liquidity-${index}`); return true; },
      extendData: { color: colors.amber, dashed: true, label: level.kind === "equal_high" ? "EQH" : "EQL" },
    });
  }
  const zones = [...smartMoney.value_zones, ...smartMoney.order_blocks, ...smartMoney.fair_value_gaps];
  for (const [index, zone] of zones.entries()) {
    if (!visible.has(`zone-${index}`)) continue;
    if (!cutoff && zone.status !== "active" && zone.status !== "current") continue;
    const from = timestamp(zone.created_at);
    const low = number(zone.low);
    const high = number(zone.high);
    if (from === null || low === null || high === null) continue;
    const directionColor = zone.direction === "bullish" ? colors.positive : zone.direction === "bearish" ? colors.negative : zone.kind === "equilibrium" ? colors.muted : zone.kind === "premium" ? colors.negative : colors.positive;
    const label = zone.kind === "order_block" ? "OB" : zone.kind === "fair_value_gap" ? "FVG" : zone.kind.toUpperCase();
    chart.createOverlay({
      name: "tpZone",
      groupId: "derived:smc",
      lock: true,
      points: [{ timestamp: from, value: high }, { timestamp: finalTime, value: low }],
      onClick: () => { onSelect(`zone-${index}`); return true; },
      extendData: { color: directionColor, fill: `${directionColor}1f`, label },
    });
  }
}

function syncIndicators(chart: Chart, selected: Set<string>) {
  for (const name of INDICATORS) chart.removeIndicator({ name });
  for (const name of INDICATORS) {
    if (!selected.has(name)) continue;
    const value = name === "EMA"
      ? { name, paneId: "candle_pane", calcParams: [10, 20] }
      : name === "MA"
        ? { name, paneId: "candle_pane", calcParams: [20, 50, 200] }
        : name === "BOLL"
          ? { name, paneId: "candle_pane", calcParams: [20, 2] }
          : { name };
    chart.createIndicator(value, OVERLAY_INDICATORS.has(name));
  }
}

export function InteractiveTechnicalChart({ scene, onSelectStructure, onContextReset }: { onContextReset?: () => void; scene: InteractiveTechnicalScene; onSelectStructure?: (structure: ChartStructure, cutoff: string | null) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const [chartType, setChartType] = useState<CandleType>("candle_solid");
  const [indicators, setIndicators] = useState<Set<string>>(() => new Set(["EMA", "VOL", "RSI"]));
  const [drawingTool, setDrawingTool] = useState("segment");
  const [drawingCount, setDrawingCount] = useState(0);
  const [showSmc, setShowSmc] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cutoff, setCutoff] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const data = useMemo(() => chartData(scene.bars).filter((bar) => !cutoff || bar.timestamp <= Date.parse(cutoff)), [scene.bars, cutoff]);
  const timeframe = scene.timeframes.find((item) => item.interval === scene.bars_interval);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || data.length === 0 || !timeframe) return;
    let disposed = false;
    let animationFrame = 0;
    let chartModule: typeof import("klinecharts") | null = null;
    let resizeObserver: ResizeObserver | null = null;
    let themeObserver: MutationObserver | null = null;
    void import("klinecharts").then((module) => {
      if (disposed) return;
      chartModule = module;
      registerTradingPartnerOverlays(module);
      const chart = module.init(container, {
        locale: "en-US",
        timezone: "UTC",
        styles: chartStyles(chartType),
        hotkey: { enabled: true },
      });
      if (!chart) {
        setError("Unable to initialize the interactive chart");
        return;
      }
      chartRef.current = chart;
      setDrawingCount(0);
      chart.setDataLoader({
        getBars: ({ type, callback }) => {
          callback(type === "init" ? data : [], { backward: false, forward: false });
        },
      });
      chart.setSymbol({
        ticker: scene.instrument_id,
        pricePrecision: pricePrecision(scene.bars),
        volumePrecision: 0,
      });
      chart.setPeriod({ type: scene.bars_interval === "1w" ? "week" : "day", span: 1 });
      syncIndicators(chart, indicators);
      animationFrame = requestAnimationFrame(() => {
        if (showSmc) addDerivedOverlays(chart, timeframe, data[data.length - 1].timestamp, cutoff, setSelectedId);
        chart.scrollToRealTime();
      });
      resizeObserver = new ResizeObserver(() => chart.resize());
      resizeObserver.observe(container);
      themeObserver = new MutationObserver(() => chart.setStyles(chartStyles(chartType)));
      themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      setError(null);
    }).catch(() => setError("Unable to load KLineChart"));
    return () => {
      disposed = true;
      cancelAnimationFrame(animationFrame);
      resizeObserver?.disconnect();
      themeObserver?.disconnect();
      chartRef.current = null;
      chartModule?.dispose(container);
    };
  }, [data, scene.bars, scene.bars_interval, scene.instrument_id, scene.price_basis, scene.algorithm_version, timeframe, cutoff]);

  useEffect(() => {
    chartRef.current?.setStyles(chartStyles(chartType));
  }, [chartType]);

  useEffect(() => {
    const chart = chartRef.current;
    if (chart) syncIndicators(chart, indicators);
  }, [indicators]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !timeframe || data.length === 0) return;
    chart.removeOverlay({ groupId: "derived:smc" });
    if (showSmc) addDerivedOverlays(chart, timeframe, data[data.length - 1].timestamp, cutoff, setSelectedId);
  }, [data, showSmc, timeframe, cutoff]);

  const toggleIndicator = useCallback((name: string) => {
    setIndicators((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  function startDrawing() {
    const chart = chartRef.current;
    if (!chart) return;
    const updateDrawingCount = () => setDrawingCount(chart.getOverlays({ groupId: "user:drawing" }).length);
    chart.createOverlay({
      name: drawingTool,
      groupId: "user:drawing",
      mode: "strong_magnet",
      onDrawEnd: updateDrawingCount,
      onRemoved: updateDrawingCount,
    });
  }

  function clearDrawings() {
    chartRef.current?.removeOverlay({ groupId: "user:drawing" });
    setDrawingCount(0);
  }

  function exportImage() {
    const chart = chartRef.current;
    if (!chart) return;
    const anchor = document.createElement("a");
    anchor.href = chart.getConvertPictureUrl(true, "png", chartColors().panel);
    anchor.download = `${scene.instrument_id.replaceAll(":", "-")}-${scene.bars_interval}.png`;
    anchor.click();
  }

  const smartMoney = timeframe?.smart_money;
  const structures = timeframe ? chartStructures(timeframe, cutoff || undefined) : [];
  const selected = structures.find((row) => row.id === selectedId);
  return (
    <section className={styles.workspace} aria-label="Interactive Technical Chart">
      <div className={styles.toolbar}>
        <label><span>Historical Cutoff (UTC)</span><Input type="datetime-local" value={cutoff.replace(/Z$/, "")} onChange={(event) => { setCutoff(event.target.value ? `${event.target.value}Z` : ""); setSelectedId(null); onContextReset?.(); }} /></label>
        <label><span>Chart Style</span><Select value={chartType} onChange={(event) => setChartType(event.target.value as CandleType)}>{CHART_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</Select></label>
        <div className={styles.indicators} aria-label="Chart Indicators">
          <span>Indicators</span>
          <div>{INDICATORS.map((name) => <Button size="sm" key={name} aria-pressed={indicators.has(name)} onClick={() => toggleIndicator(name)}>{name}</Button>)}</div>
        </div>
        <label><span>Drawing Tool</span><Select value={drawingTool} onChange={(event) => setDrawingTool(event.target.value)}>{DRAWING_TOOLS.map((item) => <option key={item.name} value={item.name}>{item.label}</option>)}</Select></label>
        <Button onClick={startDrawing}>Draw</Button>
        <Button aria-pressed={showSmc} onClick={() => setShowSmc((value) => !value)}>SMC</Button>
        <Button onClick={clearDrawings}><Eraser aria-hidden="true" />Clear Drawings</Button>
        <Button onClick={exportImage}><Download aria-hidden="true" />Export</Button>
      </div>
      <div
        className={styles.chart}
        ref={containerRef}
        role="img"
        aria-label={`${scene.instrument_id} ${scene.bars_interval} interactive candlestick chart with ${data.length} bars`}
      />
      <div className={styles.status} aria-live="polite">
        <span>{data.length} bars</span>
        <span>{scene.bars_interval.toUpperCase()}</span>
        <span>{scene.price_basis.replaceAll("_", " ")}</span>
        <span>Snapshot Trend {timeframe?.trend_state ?? "unknown"}</span>
        <span>Snapshot SMC {smartMoney?.trend ?? "unavailable"}</span>
        <span>{drawingCount} {drawingCount === 1 ? "drawing" : "drawings"}</span>
        {smartMoney && !smartMoney.atr_200_ready && <span>{smartMoney.limitations.join(", ")}</span>}
      </div>
      <p className={styles.help}>Algorithm {scene.algorithm_version ?? "unavailable"} · SMC {smartMoney?.algorithm_version ?? "unavailable"} · Source interval {scene.bars_interval} · Snapshot {scene.as_of ?? "unavailable"}. Derived SMC is locked; user drawings are editable and session-only.</p>
      {cutoff && <p className={styles.help}>Cutoff filters confirmation times and bars only. Zone/liquidity status belongs to the current snapshot; historical invalidation, mitigation and sweep status cannot be reconstructed. This is not a historical backtest.</p>}
      <div className={styles.inspector}>
        <label><span>Inspect Structure</span><Select value={selectedId ?? ""} onChange={(event) => setSelectedId(event.target.value || null)}><option value="">Select an overlay or structure</option>{structures.map((row) => <option key={row.id} value={row.id}>{row.kind} · {row.occurred_at}</option>)}</Select></label>
        {selected && <div><p>{selected.kind} · {selected.values}</p><p>Occurred {selected.occurred_at} · Confirmed {selected.confirmed_at}</p><p>Status at snapshot: {selected.status}</p>{onSelectStructure && <Button onClick={() => onSelectStructure(selected, cutoff || null)}>Use as Research Context</Button>}</div>}
        {structures.length === 0 && <p>No confirmed structures at this cutoff.</p>}
      </div>
      {error && <p className={styles.error}>{error}</p>}
      <p className={styles.help}>Right-click a user drawing to remove it. Drawings remain in the current chart session; changing Instrument, period, basis, algorithm version or cutoff starts a fresh canvas. Chart indicators are visual aids; the receipt below remains the sourced technical record.</p>
    </section>
  );
}
