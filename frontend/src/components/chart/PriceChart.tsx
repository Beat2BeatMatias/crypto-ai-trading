import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  TickMarkType,
  createChart,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { createSeriesMarkers, type ISeriesMarkersPluginApi } from "lightweight-charts";
import { type LinePoint } from "./indicators";

import { api } from "../../api/client";
import { useWebSocket, type WSEvent } from "../../hooks/useWebSocket";
import type { Candle, Timeframe, Trade, DecisionOutcome, TradingProduct } from "../../types";
import { tradeDirection } from "../../lib/pnl";
import ReasoningBlock from "../ReasoningBlock";
import { TIMEFRAMES, bucketStart, timeframeFromConfigMinutes, timeframeSeconds } from "./timeframe";
import { bollingerBands, ema } from "./indicators";

type DecisionAction = "BUY" | "SHORT" | "SELL" | "HOLD";

interface DecisionMarkerSource {
  id: string;
  ts: string;
  action: DecisionAction | undefined;
  executed: boolean;
  model: string;
  latency_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  output: Record<string, unknown>;
  rejected_reason: string | null;
}

const COLORS = {
  bullish: "#26a69a",
  bearish: "#ef5350",
  ema20: "#f0b90b",
  ema50: "#2196f3",
  ema200: "#9c27b0",
  bbBand: "rgba(100, 149, 237, 0.5)",
  bbFill: "rgba(100, 149, 237, 0.06)",
  bbMiddle: "rgba(100, 149, 237, 0.8)",
  entry: "#3b82f6",
  stopLoss: "#ef5350",
  takeProfit: "#26a69a",
  decisionBuy: "#26a69a",
  decisionShort: "#f59e0b",
  decisionSell: "#ef5350",
  decisionHold: "#71717a",
  liquidation: "#fb923c",
  missedOpportunity: "#f59e0b",
  blockedGood: "#f59e0b80",
};

function toUtc(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

function isCompleteCandle(c: Candle): c is Candle & {
  open: number; high: number; low: number; close: number;
} {
  return c.open != null && c.high != null && c.low != null && c.close != null;
}

function toCandlestickData(candles: Candle[]): CandlestickData[] {
  return candles
    .filter(isCompleteCandle)
    .filter((c) => (
      c.high >= c.low &&
      c.open >= c.low && c.open <= c.high &&
      c.close >= c.low && c.close <= c.high
    ))
    .map((c) => ({
      time: toUtc(c.time),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
}

interface PriceChartProps {
  defaultTimeframe?: Timeframe;
  height?: number;
  tradingProduct?: TradingProduct | null;
  chartLabel?: string | null;
}

export function PriceChart({
  defaultTimeframe,
  height = 540,
  tradingProduct = "spot",
  chartLabel,
}: PriceChartProps) {
  const ohlcvMarket: "spot" | "futures" =
    tradingProduct === "futures" ? "futures" : "spot";
  const displayLabel =
    chartLabel ?? (ohlcvMarket === "futures" ? "BTC/USDT Perp" : "BTC/USDT");
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ema20Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema50Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ema200Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const bbUpperRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbMiddleRef = useRef<ISeriesApi<"Line"> | null>(null);
  const bbLowerRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lastCandleRef = useRef<CandlestickData | null>(null);
  const candlesRef = useRef<Candle[]>([]);
  const decisionsRef = useRef<DecisionMarkerSource[]>([]);
  const timeframeRef = useRef<Timeframe>("5m");

  const [timeframe, setTimeframe] = useState<Timeframe>(defaultTimeframe ?? "5m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [openTrades, setOpenTrades] = useState<Trade[]>([]);
  const [closedTrades, setClosedTrades] = useState<Trade[]>([]);
  const [decisions, setDecisions] = useState<DecisionMarkerSource[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [showOverlays, setShowOverlays] = useState({
    emas: true,
    bb: true,
    decisions: true,
    closedTrades: true,
    outcomes: true,
  });
  const [outcomes, setOutcomes] = useState<DecisionOutcome[]>([]);
  const [indicatorValues, setIndicatorValues] = useState<{
    ema20: number | null;
    ema50: number | null;
    ema200: number | null;
  }>({ ema20: null, ema50: null, ema200: null });
  const [selectedDecisions, setSelectedDecisions] = useState<DecisionMarkerSource[]>([]);

  const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
  const { last } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);

  useEffect(() => { timeframeRef.current = timeframe; }, [timeframe]);
  useEffect(() => { decisionsRef.current = decisions; }, [decisions]);

  useEffect(() => {
    if (defaultTimeframe) return;
    api.config()
      .then((cfg) => {
        const entry = cfg.find((c) => c.key === "decisor_interval_min");
        const minutes = entry ? parseInt(entry.value, 10) : NaN;
        setTimeframe(timeframeFromConfigMinutes(Number.isFinite(minutes) ? minutes : null));
      })
      .catch(() => {});
  }, [defaultTimeframe]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "#0b0e11" },
        textColor: "#848e9c",
        fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(42, 46, 57, 0.6)" },
        horzLines: { color: "rgba(42, 46, 57, 0.6)" },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: "#758696", style: 3, labelBackgroundColor: "#2a2d3a" },
        horzLine: { color: "#758696", style: 3, labelBackgroundColor: "#2a2d3a" },
      },
      rightPriceScale: {
        borderColor: "#2a2d3a",
        textColor: "#848e9c",
        entireTextOnly: true,
      },
      localization: {
        locale: "es-AR",
        timeFormatter: (ts: UTCTimestamp) =>
          new Date((ts as number) * 1000).toLocaleString("es-AR", { hour12: false }),
      },
      timeScale: {
        borderColor: "#2a2d3a",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        barSpacing: 8,
        tickMarkFormatter: (ts: Time, type: TickMarkType) => {
          const d = new Date((ts as number) * 1000);
          switch (type) {
            case TickMarkType.Year:
              return d.toLocaleDateString("es-AR", { year: "numeric" });
            case TickMarkType.Month:
              return d.toLocaleDateString("es-AR", { month: "short", year: "2-digit" });
            case TickMarkType.DayOfMonth:
              return d.toLocaleDateString("es-AR", { day: "2-digit", month: "2-digit" });
            case TickMarkType.Time:
              return d.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", hour12: false });
            case TickMarkType.TimeWithSeconds:
              return d.toLocaleTimeString("es-AR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
            default:
              return null;
          }
        },
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.bullish,
      downColor: COLORS.bearish,
      borderUpColor: COLORS.bullish,
      borderDownColor: COLORS.bearish,
      wickUpColor: COLORS.bullish,
      wickDownColor: COLORS.bearish,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "#26a69a",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.80, bottom: 0 },
    });

    chart.priceScale("right").applyOptions({
      scaleMargins: { top: 0.06, bottom: 0.20 },
    });

    const ema20 = chart.addSeries(LineSeries, {
      color: COLORS.ema20, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: false,
    });
    const ema50 = chart.addSeries(LineSeries, {
      color: COLORS.ema50, lineWidth: 1, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: false,
    });
    const ema200 = chart.addSeries(LineSeries, {
      color: COLORS.ema200, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerVisible: false,
    });
    const bbUpper = chart.addSeries(LineSeries, {
      color: COLORS.bbBand, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const bbMiddle = chart.addSeries(LineSeries, {
      color: COLORS.bbMiddle, lineWidth: 1, lineStyle: 2,
      priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    const bbLower = chart.addSeries(LineSeries, {
      color: COLORS.bbBand, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    const markers = createSeriesMarkers(candleSeries, []);

    chart.subscribeClick((params) => {
      if (!params.time) {
        setSelectedDecisions([]);
        return;
      }
      const clickedTime = params.time as number;
      const bucketSec = timeframeSeconds(timeframeRef.current);
      const nearby = decisionsRef.current.filter((d) => {
        const dTime = toUtc(d.ts) as number;
        return Math.abs(dTime - clickedTime) <= bucketSec;
      });
      setSelectedDecisions(nearby.length > 0 ? nearby : []);
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema20Ref.current = ema20;
    ema50Ref.current = ema50;
    ema200Ref.current = ema200;
    bbUpperRef.current = bbUpper;
    bbMiddleRef.current = bbMiddle;
    bbLowerRef.current = bbLower;
    markersPluginRef.current = markers;

    return () => {
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ema20Ref.current = null;
      ema50Ref.current = null;
      ema200Ref.current = null;
      bbUpperRef.current = null;
      bbMiddleRef.current = null;
      bbLowerRef.current = null;
      markersPluginRef.current = null;
      priceLinesRef.current = [];
      lastCandleRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setCandles([]);
    setLivePrice(null);
    lastCandleRef.current = null;
    api.ohlcv(timeframe, 400, ohlcvMarket)
      .then((data) => { if (!cancelled) setCandles(data); })
      .catch(() => { if (!cancelled) setCandles([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [timeframe, ohlcvMarket]);

  useEffect(() => {
    const refresh = () => {
      api.trades({ status: "open" }).then(setOpenTrades).catch(() => {});
      api.trades({ status: "closed" }).then((all) => setClosedTrades(all.slice(0, 50))).catch(() => {});
      api.decisions({ agent: "decisor" })
        .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
          id: d.id,
          ts: d.ts,
          action: (d.output?.action) as DecisionAction | undefined,
          executed: d.executed,
          model: d.model,
          latency_ms: d.latency_ms,
          tokens_in: d.tokens_in,
          tokens_out: d.tokens_out,
          output: d.output,
          rejected_reason: d.rejected_reason,
        }))))
        .catch(() => {});
    };
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const load = () => api.outcomes(24).then(setOutcomes).catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    candlesRef.current = candles;
    const data = toCandlestickData(candles);

    // Guardar la vela live antes de que setData la pise
    const prevLive = lastCandleRef.current;

    candleSeriesRef.current?.setData(data);
    const lastFromApi = data.length ? { ...data[data.length - 1] } : null;
    lastCandleRef.current = lastFromApi;

    // Reactivar la vela live si estaba más adelante que la API
    if (prevLive && lastFromApi) {
      const prevTime = prevLive.time as number;
      const apiTime = lastFromApi.time as number;
      if (prevTime > apiTime) {
        // La vela live está en un bucket más nuevo que lo que devolvió la API
        candleSeriesRef.current?.update(prevLive);
        lastCandleRef.current = prevLive;
      } else if (prevTime === apiTime) {
        // Mismo bucket: respetar los valores OHLCV reales de la API como base
        // y solo actualizar close con el último precio live.
        // No acumular high/low del prevLive porque puede haber quedado
        // distorsionado por tickers previos fuera de rango.
        const merged: CandlestickData = {
          time: lastFromApi.time,
          open: lastFromApi.open,
          high: lastFromApi.high,
          low: lastFromApi.low,
          close: prevLive.close,
        };
        candleSeriesRef.current?.update(merged);
        lastCandleRef.current = merged;
      }
    }

    const volumeData = candles
      .filter(isCompleteCandle)
      .map((c) => ({
        time: toUtc(c.time),
        value: c.volume ?? 0,
        color: c.close >= c.open ? "rgba(38, 166, 154, 0.55)" : "rgba(239, 83, 80, 0.55)",
      }));
    volumeSeriesRef.current?.setData(volumeData);

    const sourcePoints = data.map((d) => ({ time: d.time, close: d.close }));

    const lastOf = (arr: LinePoint[]) => arr.length ? arr[arr.length - 1].value : null;

    if (showOverlays.emas) {
      const e20 = ema(sourcePoints, 20);
      const e50 = ema(sourcePoints, 50);
      const e200 = ema(sourcePoints, 200);
      ema20Ref.current?.setData(e20);
      ema50Ref.current?.setData(e50);
      ema200Ref.current?.setData(e200);
      setIndicatorValues({ ema20: lastOf(e20), ema50: lastOf(e50), ema200: lastOf(e200) });
    } else {
      ema20Ref.current?.setData([]);
      ema50Ref.current?.setData([]);
      ema200Ref.current?.setData([]);
      setIndicatorValues({ ema20: null, ema50: null, ema200: null });
    }

    if (showOverlays.bb) {
      const bb = bollingerBands(sourcePoints, 20, 2);
      bbUpperRef.current?.setData(bb.upper);
      bbMiddleRef.current?.setData(bb.middle);
      bbLowerRef.current?.setData(bb.lower);
    } else {
      bbUpperRef.current?.setData([]);
      bbMiddleRef.current?.setData([]);
      bbLowerRef.current?.setData([]);
    }
  }, [candles, showOverlays.emas, showOverlays.bb]);

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;
    priceLinesRef.current.forEach((line) => series.removePriceLine(line));
    const newLines: IPriceLine[] = [];
    openTrades.forEach((t, idx) => {
      const dir = tradeDirection(t);
      const tag = openTrades.length > 1 ? ` #${idx + 1}` : "";
      const sideTag = dir === "SHORT" ? " SHORT" : "";
      newLines.push(series.createPriceLine({
        price: t.entry_price,
        color: dir === "SHORT" ? COLORS.decisionShort : COLORS.entry,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `Entry${sideTag}${tag} $${t.entry_price.toFixed(2)}`,
      }));
      if (t.stop_loss != null) {
        newLines.push(series.createPriceLine({
          price: t.stop_loss,
          color: COLORS.stopLoss,
          lineWidth: 1,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `SL${sideTag}${tag} $${t.stop_loss.toFixed(2)}`,
        }));
      }
      if (t.take_profit != null) {
        newLines.push(series.createPriceLine({
          price: t.take_profit,
          color: COLORS.takeProfit,
          lineWidth: 1,
          lineStyle: 0,
          axisLabelVisible: true,
          title: `TP${sideTag}${tag} $${t.take_profit.toFixed(2)}`,
        }));
      }
      if (t.liquidation_price != null) {
        newLines.push(series.createPriceLine({
          price: t.liquidation_price,
          color: COLORS.liquidation,
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: `Liq${sideTag}${tag} $${t.liquidation_price.toFixed(2)}`,
        }));
      }
    });
    priceLinesRef.current = newLines;
  }, [openTrades]);

  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    const out: SeriesMarker<Time>[] = [];
    const outcomeByDecisionId = new Map(
      outcomes.map((o) => [o.decision_id, o])
    );

    // Posiciones abiertas: flecha visible con precio (son pocas, siempre con label)
    openTrades.forEach((t) => {
      const dir = tradeDirection(t);
      const isShort = dir === "SHORT";
      out.push({
        time: toUtc(t.ts_open),
        position: isShort ? "aboveBar" : "belowBar",
        color: isShort ? COLORS.decisionShort : COLORS.entry,
        shape: isShort ? "arrowDown" : "arrowUp",
        size: 1.5,
        text: `${dir} $${t.entry_price.toFixed(0)}`,
      });
    });

    if (showOverlays.closedTrades) {
      // Círculo pequeño en entry (sin texto — la posición vertical lo identifica)
      // Flecha en cierre con PnL solo si es significativo (|pnl| >= $5)
      closedTrades.forEach((t) => {
        const pnl = t.pnl_usdt ?? 0;
        const winner = pnl >= 0;
        const color = winner ? COLORS.takeProfit : COLORS.stopLoss;

        out.push({
          time: toUtc(t.ts_open),
          position: "belowBar",
          color: `${color}99`,
          shape: "circle",
          size: 0.3,
          text: "",
        });

        if (t.ts_close && t.exit_price != null) {
          out.push({
            time: toUtc(t.ts_close),
            position: winner ? "belowBar" : "aboveBar",
            color,
            shape: winner ? "arrowUp" : "arrowDown",
            size: 0.7,
            text: `${winner ? "+" : ""}$${pnl.toFixed(0)}`,
          });
        }
      });
    }

    // Markers de outcomes (HOLDs missed + BLOCKED_GOOD_TRADE) — independiente del toggle Señales
    if (showOverlays.outcomes) {
      decisions.forEach((d) => {
        const outcome = outcomeByDecisionId.get(d.id);
        if (!outcome) return;

        if ((d.action === "HOLD" || !d.action) && outcome.classification === "MISSED_OPPORTUNITY") {
          const label = outcome.mfe_pct != null && outcome.mfe_pct >= 1
            ? `miss +${outcome.mfe_pct.toFixed(1)}%`
            : "";
          out.push({
            time: toUtc(d.ts),
            position: "aboveBar",
            color: COLORS.missedOpportunity,
            shape: "circle",
            size: 0.8,
            text: label,
          });
        }

        if (
          (d.action === "BUY" || d.action === "SHORT")
          && !d.executed
          && outcome.classification === "BLOCKED_GOOD_TRADE"
        ) {
          out.push({
            time: toUtc(d.ts),
            position: "belowBar",
            color: COLORS.blockedGood,
            shape: "circle",
            size: 0.7,
            text: "",
          });
        }
      });
    }

    // Markers de señales BUY/SELL — controlados por toggle Señales
    if (showOverlays.decisions) {
      decisions.forEach((d) => {
        if (d.action === "HOLD" || !d.action) return;

        const outcome = outcomeByDecisionId.get(d.id);
        const baseColor =
          d.action === "BUY" ? COLORS.decisionBuy
          : d.action === "SHORT" ? COLORS.decisionShort
          : COLORS.decisionSell;
        const openBelow = d.action === "BUY";
        const openShape = d.action === "BUY" ? "arrowUp" : d.action === "SHORT" ? "arrowDown" : "arrowDown";

        if (d.executed) {
          let color = baseColor;
          if (showOverlays.outcomes && outcome) {
            if (outcome.classification === "GOOD_BUY" || outcome.classification === "GOOD_SHORT") {
              color = COLORS.decisionBuy;
            } else if (outcome.classification === "BAD_BUY" || outcome.classification === "BAD_SHORT") {
              color = COLORS.decisionSell;
            }
          }
          out.push({
            time: toUtc(d.ts),
            position: openBelow ? "belowBar" : "aboveBar",
            color,
            shape: openShape as "arrowUp" | "arrowDown",
            size: 1,
            text: d.action,
          });
        } else if (!(showOverlays.outcomes && outcome?.classification === "BLOCKED_GOOD_TRADE")) {
          const markerBelow = d.action === "BUY";
          out.push({
            time: toUtc(d.ts),
            position: markerBelow ? "belowBar" : "aboveBar",
            color: `${baseColor}55`,
            shape: "circle",
            size: 0.3,
            text: "",
          });
        }
      });
    }

    return out.sort((a, b) => (a.time as number) - (b.time as number));
  }, [openTrades, closedTrades, decisions, outcomes, showOverlays.closedTrades, showOverlays.decisions, showOverlays.outcomes]);

  useEffect(() => {
    markersPluginRef.current?.setMarkers(markers);
  }, [markers]);

  useEffect(() => {
    if (!last) return;
    const ev = last as WSEvent;

    if (ev.event === "ticker") {
      const data = ev.data as { price: number | null };
      if (data.price == null) return;
      setLivePrice(data.price);
      const series = candleSeriesRef.current;
      const current = lastCandleRef.current;
      if (!series || !current) return;
      const nowSec = Math.floor(Date.now() / 1000);
      const currentBucket = bucketStart(nowSec, timeframe);
      if (currentBucket > (current.time as number)) {
        const fresh: CandlestickData = {
          time: currentBucket as UTCTimestamp,
          open: data.price,
          high: data.price,
          low: data.price,
          close: data.price,
        };
        series.update(fresh);
        lastCandleRef.current = fresh;
        const sinceLastFetch = nowSec - (current.time as number);
        if (sinceLastFetch >= timeframeSeconds(timeframe)) {
          api.ohlcv(timeframe, 400, ohlcvMarket).then(setCandles).catch(() => {});
        }
      } else {
        // Limitar extensión de bigotes: solo mover high/low si el precio
        // está dentro de un 5% del rango actual. Precios fuera de ese rango
        // indican un ticker espurio (testnet, retraso de red, etc.).
        const WICK_TOLERANCE = 0.05;
        const withinRange =
          data.price >= current.low * (1 - WICK_TOLERANCE) &&
          data.price <= current.high * (1 + WICK_TOLERANCE);
        const updated: CandlestickData = {
          time: current.time,
          open: current.open,
          high: withinRange ? Math.max(current.high, data.price) : current.high,
          low: withinRange ? Math.min(current.low, data.price) : current.low,
          close: data.price,
        };
        series.update(updated);
        lastCandleRef.current = updated;
      }
    }

    if (ev.event === "trade_opened" || ev.event === "trade_closed") {
      api.trades({ status: "open" }).then(setOpenTrades).catch(() => {});
      api.trades({ status: "closed" }).then((all) => setClosedTrades(all.slice(0, 50))).catch(() => {});
    }
    if (ev.event === "decision") {
      api.decisions({ agent: "decisor" })
        .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
          id: d.id,
          ts: d.ts,
          action: (d.output?.action) as DecisionAction | undefined,
          executed: d.executed,
          model: d.model,
          latency_ms: d.latency_ms,
          tokens_in: d.tokens_in,
          tokens_out: d.tokens_out,
          output: d.output,
          rejected_reason: d.rejected_reason,
        }))))
        .catch(() => {});
    }
  }, [last, timeframe, ohlcvMarket]);

  const fmtPrice = (v: number) =>
    v.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div className="rounded-xl overflow-hidden" style={{ background: "#0b0e11" }}>
      {/* Header: símbolo, precio live, timeframes */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3 pb-2">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-sm font-bold text-white tracking-wide">{displayLabel}</span>
          {ohlcvMarket === "futures" && (
            <span className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-violet-900/50 text-violet-200">
              Futuros USDT-M
            </span>
          )}
          {livePrice != null && (
            <span className="text-xl font-mono font-bold" style={{ color: COLORS.bullish }}>
              {fmtPrice(livePrice)}
            </span>
          )}
          {loading && <span className="text-xs" style={{ color: "#848e9c" }}>cargando…</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded gap-0.5" style={{ background: "#1e2026" }}>
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className="px-2.5 py-1 text-xs font-medium rounded transition-colors"
                style={
                  timeframe === tf
                    ? { background: "#2b3139", color: "#f0b90b" }
                    : { color: "#848e9c" }
                }
              >
                {tf}
              </button>
            ))}
          </div>

          <div className="inline-flex rounded gap-0.5 text-xs" style={{ background: "#1e2026" }}>
            <Toggle
              label="EMA"
              active={showOverlays.emas}
              onClick={() => setShowOverlays((s) => ({ ...s, emas: !s.emas }))}
            />
            <Toggle
              label="BB"
              active={showOverlays.bb}
              onClick={() => setShowOverlays((s) => ({ ...s, bb: !s.bb }))}
            />
            <Toggle
              label="Trades"
              active={showOverlays.closedTrades}
              onClick={() => setShowOverlays((s) => ({ ...s, closedTrades: !s.closedTrades }))}
            />
            <Toggle
              label="Señales"
              active={showOverlays.decisions}
              onClick={() => setShowOverlays((s) => ({ ...s, decisions: !s.decisions }))}
            />
            <Toggle
              label="Outcomes"
              active={showOverlays.outcomes}
              onClick={() => setShowOverlays((s) => ({ ...s, outcomes: !s.outcomes }))}
            />
          </div>
        </div>
      </div>

      {/* Leyenda de indicadores estilo Binance */}
      {showOverlays.emas && (indicatorValues.ema20 || indicatorValues.ema50 || indicatorValues.ema200) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 px-4 pb-1.5 text-xs font-mono">
          {indicatorValues.ema20 != null && (
            <span style={{ color: COLORS.ema20 }}>
              MA(20) <span className="font-semibold">{fmtPrice(indicatorValues.ema20)}</span>
            </span>
          )}
          {indicatorValues.ema50 != null && (
            <span style={{ color: COLORS.ema50 }}>
              MA(50) <span className="font-semibold">{fmtPrice(indicatorValues.ema50)}</span>
            </span>
          )}
          {indicatorValues.ema200 != null && (
            <span style={{ color: COLORS.ema200 }}>
              MA(200) <span className="font-semibold">{fmtPrice(indicatorValues.ema200)}</span>
            </span>
          )}
        </div>
      )}

      <div ref={containerRef} style={{ height }} />

      {selectedDecisions.length > 0 && (
        <DecisionPanel
          decisions={selectedDecisions}
          onClose={() => setSelectedDecisions([])}
        />
      )}

      <Legend openTrades={openTrades.length} />
    </div>
  );
}

function Toggle({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="px-2.5 py-1 rounded transition-colors text-xs"
      style={active ? { background: "#2b3139", color: "#eaecef" } : { color: "#848e9c" }}
    >
      {label}
    </button>
  );
}

interface DecisionPanelProps {
  decisions: DecisionMarkerSource[];
  onClose: () => void;
}

function DecisionPanel({ decisions, onClose }: DecisionPanelProps) {
  const fmtTs = (iso: string) =>
    new Date(iso).toLocaleString("es-AR", {
      day: "2-digit", month: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });

  const actionColor = (action: DecisionAction | undefined) => {
    if (action === "BUY") return COLORS.decisionBuy;
    if (action === "SHORT") return COLORS.decisionShort;
    if (action === "SELL") return COLORS.decisionSell;
    return "#848e9c";
  };

  return (
    <div
      className="mx-0 mt-0 text-xs"
      style={{ borderTop: "1px solid #2a2d3a", background: "#0f1217" }}
    >
      <div className="flex items-center justify-between px-4 py-2" style={{ borderBottom: "1px solid #2a2d3a" }}>
        <span className="font-semibold" style={{ color: "#eaecef" }}>
          Señales en esta vela
          <span className="ml-2 px-1.5 py-0.5 rounded text-[10px]" style={{ background: "#2b3139", color: "#848e9c" }}>
            {decisions.length}
          </span>
        </span>
        <button
          onClick={onClose}
          className="text-lg leading-none"
          style={{ color: "#848e9c" }}
          title="Cerrar"
        >
          ×
        </button>
      </div>

      <div className="divide-y divide-[#2a2d3a]" style={{ maxHeight: 280, overflowY: "auto" }}>
        {decisions.map((d) => {
          const reasoning = d.output?.reasoning as string | undefined;
          const confidence = d.output?.confidence as number | string | undefined;
          const signal = d.output?.signal as string | undefined;
          const regime = d.output?.market_regime as string | undefined;
          const risk = d.output?.risk_assessment as string | undefined;

          return (
            <div key={d.id} className="px-4 py-3 space-y-2">
              {/* Header: timestamp + acción + estado */}
              <div className="flex items-center gap-3 flex-wrap">
                <span style={{ color: "#848e9c" }}>{fmtTs(d.ts)}</span>
                <span
                  className="px-2 py-0.5 rounded font-bold text-[11px]"
                  style={{ background: `${actionColor(d.action)}22`, color: actionColor(d.action) }}
                >
                  {d.action ?? "?"}
                </span>
                {d.executed ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: "#0d2d1a", color: COLORS.decisionBuy }}>
                    Ejecutada
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ background: "#2d1a1a", color: "#f87171" }}>
                    Bloqueada
                  </span>
                )}
                {confidence != null && (
                  <span style={{ color: "#848e9c" }}>
                    Confianza: <span style={{ color: "#eaecef" }}>{typeof confidence === "number" ? `${(confidence * 100).toFixed(0)}%` : confidence}</span>
                  </span>
                )}
              </div>

              {/* Razón de bloqueo */}
              {!d.executed && d.rejected_reason && (
                <div className="flex gap-2">
                  <span style={{ color: "#848e9c", flexShrink: 0 }}>Bloqueada por:</span>
                  <span style={{ color: "#fbbf24" }}>{d.rejected_reason}</span>
                </div>
              )}

              {/* Señal técnica y régimen */}
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {signal && (
                  <span style={{ color: "#848e9c" }}>
                    Señal: <span style={{ color: "#eaecef" }}>{signal}</span>
                  </span>
                )}
                {regime && (
                  <span style={{ color: "#848e9c" }}>
                    Régimen: <span style={{ color: "#eaecef" }}>{regime}</span>
                  </span>
                )}
                {risk && (
                  <span style={{ color: "#848e9c" }}>
                    Riesgo: <span style={{ color: "#eaecef" }}>{risk}</span>
                  </span>
                )}
              </div>

              {/* Reasoning */}
              {reasoning && (
                <ReasoningBlock reasoning={reasoning} compact />
              )}

              {/* Metadata del modelo */}
              <div className="flex flex-wrap gap-x-4 gap-y-0.5" style={{ color: "#4b5563" }}>
                <span>{d.model}</span>
                {d.latency_ms != null && <span>{(d.latency_ms / 1000).toFixed(1)}s</span>}
                {d.tokens_in != null && <span>{d.tokens_in + (d.tokens_out ?? 0)} tokens</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Legend({ openTrades }: { openTrades: number }) {
  const items: Array<[string, string]> = [
    ["Entry LONG", COLORS.entry],
    ["SHORT", COLORS.decisionShort],
    ["SL", COLORS.stopLoss],
    ["TP", COLORS.takeProfit],
    ["Liquidación", COLORS.liquidation],
  ];
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-[11px]"
      style={{ borderTop: "1px solid #2a2d3a", color: "#848e9c" }}
    >
      {items.map(([label, color]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <span className="inline-block w-4 h-0.5" style={{ backgroundColor: color, opacity: 0.8 }} />
          {label}
        </span>
      ))}
      <span className="ml-auto" style={{ color: openTrades > 0 ? "#f0b90b" : "#848e9c" }}>
        {openTrades > 0
          ? `${openTrades} posición${openTrades === 1 ? "" : "es"} abierta${openTrades === 1 ? "" : "s"}`
          : "Sin posiciones abiertas"}
      </span>
    </div>
  );
}
