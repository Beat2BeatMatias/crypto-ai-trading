import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, } from "lightweight-charts";
import { createSeriesMarkers } from "lightweight-charts";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { TIMEFRAMES, bucketStart, timeframeFromConfigMinutes, timeframeSeconds } from "./timeframe";
import { bollingerBands, ema } from "./indicators";
const COLORS = {
    bullish: "#22c55e",
    bearish: "#ef4444",
    ema20: "#fbbf24",
    ema50: "#06b6d4",
    ema200: "#a855f7",
    bbBand: "rgba(148, 163, 184, 0.55)",
    bbMiddle: "rgba(148, 163, 184, 0.85)",
    entry: "#3b82f6",
    stopLoss: "#ef4444",
    takeProfit: "#22c55e",
    decisionBuy: "#22c55e",
    decisionSell: "#ef4444",
    decisionHold: "#71717a",
};
function toUtc(iso) {
    return Math.floor(new Date(iso).getTime() / 1000);
}
function isCompleteCandle(c) {
    return c.open != null && c.high != null && c.low != null && c.close != null;
}
function toCandlestickData(candles) {
    return candles
        .filter(isCompleteCandle)
        .map((c) => ({
        time: toUtc(c.time),
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
    }));
}
export function PriceChart({ defaultTimeframe, height = 540 }) {
    const containerRef = useRef(null);
    const chartRef = useRef(null);
    const candleSeriesRef = useRef(null);
    const volumeSeriesRef = useRef(null);
    const ema20Ref = useRef(null);
    const ema50Ref = useRef(null);
    const ema200Ref = useRef(null);
    const bbUpperRef = useRef(null);
    const bbMiddleRef = useRef(null);
    const bbLowerRef = useRef(null);
    const priceLinesRef = useRef([]);
    const markersPluginRef = useRef(null);
    const lastCandleRef = useRef(null);
    const candlesRef = useRef([]);
    const [timeframe, setTimeframe] = useState(defaultTimeframe ?? "5m");
    const [candles, setCandles] = useState([]);
    const [openTrades, setOpenTrades] = useState([]);
    const [closedTrades, setClosedTrades] = useState([]);
    const [decisions, setDecisions] = useState([]);
    const [livePrice, setLivePrice] = useState(null);
    const [loading, setLoading] = useState(false);
    const [showOverlays, setShowOverlays] = useState({
        emas: true,
        bb: true,
        decisions: true,
        closedTrades: true,
    });
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const { last } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);
    useEffect(() => {
        if (defaultTimeframe)
            return;
        api.config()
            .then((cfg) => {
            const entry = cfg.find((c) => c.key === "decisor_interval_min");
            const minutes = entry ? parseInt(entry.value, 10) : NaN;
            setTimeframe(timeframeFromConfigMinutes(Number.isFinite(minutes) ? minutes : null));
        })
            .catch(() => { });
    }, [defaultTimeframe]);
    useEffect(() => {
        if (!containerRef.current)
            return;
        const chart = createChart(containerRef.current, {
            autoSize: true,
            layout: {
                background: { color: "#09090b" },
                textColor: "#a1a1aa",
                fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif",
            },
            grid: {
                vertLines: { color: "rgba(63, 63, 70, 0.35)" },
                horzLines: { color: "rgba(63, 63, 70, 0.35)" },
            },
            crosshair: { mode: 1 },
            rightPriceScale: { borderColor: "#3f3f46" },
            timeScale: {
                borderColor: "#3f3f46",
                timeVisible: true,
                secondsVisible: false,
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
            color: "#52525b",
        });
        volumeSeries.priceScale().applyOptions({
            scaleMargins: { top: 0.82, bottom: 0 },
        });
        const ema20 = chart.addSeries(LineSeries, {
            color: COLORS.ema20, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        });
        const ema50 = chart.addSeries(LineSeries, {
            color: COLORS.ema50, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        });
        const ema200 = chart.addSeries(LineSeries, {
            color: COLORS.ema200, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        });
        const bbUpper = chart.addSeries(LineSeries, {
            color: COLORS.bbBand, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        });
        const bbMiddle = chart.addSeries(LineSeries, {
            color: COLORS.bbMiddle, lineWidth: 1, lineStyle: 2,
            priceLineVisible: false, lastValueVisible: false,
        });
        const bbLower = chart.addSeries(LineSeries, {
            color: COLORS.bbBand, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        });
        const markers = createSeriesMarkers(candleSeries, []);
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
        api.ohlcv(timeframe, 400)
            .then((data) => { if (!cancelled)
            setCandles(data); })
            .catch(() => { if (!cancelled)
            setCandles([]); })
            .finally(() => { if (!cancelled)
            setLoading(false); });
        return () => { cancelled = true; };
    }, [timeframe]);
    useEffect(() => {
        const refresh = () => {
            api.trades("open").then(setOpenTrades).catch(() => { });
            api.trades("closed").then((all) => setClosedTrades(all.slice(0, 20))).catch(() => { });
            api.decisions({ agent: "decisor" })
                .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
                id: d.id,
                ts: d.ts,
                action: (d.output?.action),
                executed: d.executed,
            }))))
                .catch(() => { });
        };
        refresh();
        const id = setInterval(refresh, 30_000);
        return () => clearInterval(id);
    }, []);
    useEffect(() => {
        candlesRef.current = candles;
        const data = toCandlestickData(candles);
        candleSeriesRef.current?.setData(data);
        lastCandleRef.current = data.length ? { ...data[data.length - 1] } : null;
        const volumeData = candles
            .filter(isCompleteCandle)
            .map((c) => ({
            time: toUtc(c.time),
            value: c.volume ?? 0,
            color: c.close >= c.open ? "rgba(34, 197, 94, 0.45)" : "rgba(239, 68, 68, 0.45)",
        }));
        volumeSeriesRef.current?.setData(volumeData);
        const sourcePoints = data.map((d) => ({ time: d.time, close: d.close }));
        if (showOverlays.emas) {
            ema20Ref.current?.setData(ema(sourcePoints, 20));
            ema50Ref.current?.setData(ema(sourcePoints, 50));
            ema200Ref.current?.setData(ema(sourcePoints, 200));
        }
        else {
            ema20Ref.current?.setData([]);
            ema50Ref.current?.setData([]);
            ema200Ref.current?.setData([]);
        }
        if (showOverlays.bb) {
            const bb = bollingerBands(sourcePoints, 20, 2);
            bbUpperRef.current?.setData(bb.upper);
            bbMiddleRef.current?.setData(bb.middle);
            bbLowerRef.current?.setData(bb.lower);
        }
        else {
            bbUpperRef.current?.setData([]);
            bbMiddleRef.current?.setData([]);
            bbLowerRef.current?.setData([]);
        }
    }, [candles, showOverlays.emas, showOverlays.bb]);
    useEffect(() => {
        const series = candleSeriesRef.current;
        if (!series)
            return;
        priceLinesRef.current.forEach((line) => series.removePriceLine(line));
        const newLines = [];
        openTrades.forEach((t, idx) => {
            const tag = openTrades.length > 1 ? ` #${idx + 1}` : "";
            newLines.push(series.createPriceLine({
                price: t.entry_price,
                color: COLORS.entry,
                lineWidth: 1,
                lineStyle: 2,
                axisLabelVisible: true,
                title: `Entry${tag} $${t.entry_price.toFixed(2)}`,
            }));
            if (t.stop_loss != null) {
                newLines.push(series.createPriceLine({
                    price: t.stop_loss,
                    color: COLORS.stopLoss,
                    lineWidth: 1,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: `SL${tag} $${t.stop_loss.toFixed(2)}`,
                }));
            }
            if (t.take_profit != null) {
                newLines.push(series.createPriceLine({
                    price: t.take_profit,
                    color: COLORS.takeProfit,
                    lineWidth: 1,
                    lineStyle: 0,
                    axisLabelVisible: true,
                    title: `TP${tag} $${t.take_profit.toFixed(2)}`,
                }));
            }
        });
        priceLinesRef.current = newLines;
    }, [openTrades]);
    const markers = useMemo(() => {
        const out = [];
        // Posiciones abiertas: flecha visible con precio (son pocas, siempre con label)
        openTrades.forEach((t) => {
            out.push({
                time: toUtc(t.ts_open),
                position: "belowBar",
                color: COLORS.entry,
                shape: "arrowUp",
                size: 1.5,
                text: `BUY $${t.entry_price.toFixed(0)}`,
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
                    color,
                    shape: "circle",
                    size: 0.4,
                    text: "",
                });
                if (t.ts_close && t.exit_price != null) {
                    const showPnl = Math.abs(pnl) >= 5;
                    out.push({
                        time: toUtc(t.ts_close),
                        position: "aboveBar",
                        color,
                        shape: "arrowDown",
                        size: 0.8,
                        text: showPnl ? `${winner ? "+" : ""}$${pnl.toFixed(0)}` : "",
                    });
                }
            });
        }
        if (showOverlays.decisions) {
            decisions.forEach((d) => {
                if (!d.action || d.action === "HOLD")
                    return;
                const color = d.action === "BUY" ? COLORS.decisionBuy : COLORS.decisionSell;
                if (d.executed) {
                    // Decisiones ejecutadas: flecha mediana con label de acción
                    out.push({
                        time: toUtc(d.ts),
                        position: d.action === "BUY" ? "belowBar" : "aboveBar",
                        color,
                        shape: d.action === "BUY" ? "arrowUp" : "arrowDown",
                        size: 1,
                        text: d.action,
                    });
                }
                else {
                    // Decisiones bloqueadas: punto muy pequeño sin texto
                    out.push({
                        time: toUtc(d.ts),
                        position: d.action === "BUY" ? "belowBar" : "aboveBar",
                        color: `${color}55`,
                        shape: "circle",
                        size: 0.3,
                        text: "",
                    });
                }
            });
        }
        return out.sort((a, b) => a.time - b.time);
    }, [openTrades, closedTrades, decisions, showOverlays.closedTrades, showOverlays.decisions]);
    useEffect(() => {
        markersPluginRef.current?.setMarkers(markers);
    }, [markers]);
    useEffect(() => {
        if (!last)
            return;
        const ev = last;
        if (ev.event === "ticker") {
            const data = ev.data;
            if (data.price == null)
                return;
            setLivePrice(data.price);
            const series = candleSeriesRef.current;
            const current = lastCandleRef.current;
            if (!series || !current)
                return;
            const nowSec = Math.floor(Date.now() / 1000);
            const currentBucket = bucketStart(nowSec, timeframe);
            if (currentBucket > current.time) {
                const fresh = {
                    time: currentBucket,
                    open: data.price,
                    high: data.price,
                    low: data.price,
                    close: data.price,
                };
                series.update(fresh);
                lastCandleRef.current = fresh;
                const sinceLastFetch = nowSec - current.time;
                if (sinceLastFetch >= timeframeSeconds(timeframe)) {
                    api.ohlcv(timeframe, 400).then(setCandles).catch(() => { });
                }
            }
            else {
                const updated = {
                    time: current.time,
                    open: current.open,
                    high: Math.max(current.high, data.price),
                    low: Math.min(current.low, data.price),
                    close: data.price,
                };
                series.update(updated);
                lastCandleRef.current = updated;
            }
        }
        if (ev.event === "trade_opened" || ev.event === "trade_closed") {
            api.trades("open").then(setOpenTrades).catch(() => { });
            api.trades("closed").then((all) => setClosedTrades(all.slice(0, 20))).catch(() => { });
        }
        if (ev.event === "decision") {
            api.decisions({ agent: "decisor" })
                .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
                id: d.id,
                ts: d.ts,
                action: (d.output?.action),
                executed: d.executed,
            }))))
                .catch(() => { });
        }
    }, [last, timeframe]);
    return (_jsxs("div", { className: "rounded-xl bg-zinc-900 p-4", children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-3 mb-3", children: [_jsxs("div", { className: "flex items-baseline gap-3", children: [_jsx("h3", { className: "text-sm font-semibold text-zinc-300 uppercase tracking-wide", children: "BTC/USDT" }), livePrice != null && (_jsxs("span", { className: "text-lg font-mono font-bold text-white", children: ["$", livePrice.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })] })), loading && _jsx("span", { className: "text-xs text-zinc-500", children: "cargando\u2026" })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("div", { className: "inline-flex rounded-lg bg-zinc-800 p-0.5", children: TIMEFRAMES.map((tf) => (_jsx("button", { onClick: () => setTimeframe(tf), className: `px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${timeframe === tf
                                        ? "bg-emerald-600 text-white"
                                        : "text-zinc-400 hover:text-zinc-200"}`, children: tf }, tf))) }), _jsxs("div", { className: "inline-flex rounded-lg bg-zinc-800 p-0.5 text-xs", children: [_jsx(Toggle, { label: "EMAs", active: showOverlays.emas, onClick: () => setShowOverlays((s) => ({ ...s, emas: !s.emas })) }), _jsx(Toggle, { label: "BB", active: showOverlays.bb, onClick: () => setShowOverlays((s) => ({ ...s, bb: !s.bb })) }), _jsx(Toggle, { label: "Trades cerrados", active: showOverlays.closedTrades, onClick: () => setShowOverlays((s) => ({ ...s, closedTrades: !s.closedTrades })) }), _jsx(Toggle, { label: "Decisiones", active: showOverlays.decisions, onClick: () => setShowOverlays((s) => ({ ...s, decisions: !s.decisions })) })] })] })] }), _jsx("div", { ref: containerRef, style: { height } }), _jsx(Legend, { openTrades: openTrades.length })] }));
}
function Toggle({ label, active, onClick }) {
    return (_jsx("button", { onClick: onClick, className: `px-2 py-1 rounded-md transition-colors ${active ? "bg-zinc-700 text-zinc-100" : "text-zinc-500 hover:text-zinc-300"}`, children: label }));
}
function Legend({ openTrades }) {
    const items = [
        ["EMA 20", COLORS.ema20],
        ["EMA 50", COLORS.ema50],
        ["EMA 200", COLORS.ema200],
        ["Bollinger", COLORS.bbMiddle],
        ["Entry", COLORS.entry],
        ["SL", COLORS.stopLoss],
        ["TP", COLORS.takeProfit],
    ];
    return (_jsxs("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-[11px] text-zinc-500", children: [items.map(([label, color]) => (_jsxs("span", { className: "inline-flex items-center gap-1.5", children: [_jsx("span", { className: "inline-block w-3 h-0.5 rounded-full", style: { backgroundColor: color } }), label] }, label))), _jsx("span", { className: "ml-auto text-zinc-400", children: openTrades > 0
                    ? `${openTrades} posición${openTrades === 1 ? "" : "es"} abierta${openTrades === 1 ? "" : "s"}`
                    : "Sin posiciones abiertas" })] }));
}
