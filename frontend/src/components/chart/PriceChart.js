import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, } from "lightweight-charts";
import { createSeriesMarkers } from "lightweight-charts";
import { api } from "../../api/client";
import { useWebSocket } from "../../hooks/useWebSocket";
import { TIMEFRAMES, bucketStart, timeframeFromConfigMinutes, timeframeSeconds } from "./timeframe";
import { bollingerBands, ema } from "./indicators";
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
    decisionSell: "#ef5350",
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
        .filter((c) => (c.high >= c.low &&
        c.open >= c.low && c.open <= c.high &&
        c.close >= c.low && c.close <= c.high))
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
    const decisionsRef = useRef([]);
    const timeframeRef = useRef("5m");
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
    const [indicatorValues, setIndicatorValues] = useState({ ema20: null, ema50: null, ema200: null });
    const [selectedDecisions, setSelectedDecisions] = useState([]);
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const { last } = useWebSocket(`${wsProtocol}://${window.location.host}/ws`);
    useEffect(() => { timeframeRef.current = timeframe; }, [timeframe]);
    useEffect(() => { decisionsRef.current = decisions; }, [decisions]);
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
            timeScale: {
                borderColor: "#2a2d3a",
                timeVisible: true,
                secondsVisible: false,
                rightOffset: 8,
                barSpacing: 8,
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
            const clickedTime = params.time;
            const bucketSec = timeframeSeconds(timeframeRef.current);
            const nearby = decisionsRef.current.filter((d) => {
                const dTime = toUtc(d.ts);
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
            api.trades("closed").then((all) => setClosedTrades(all.slice(0, 50))).catch(() => { });
            api.decisions({ agent: "decisor" })
                .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
                id: d.id,
                ts: d.ts,
                action: (d.output?.action),
                executed: d.executed,
                model: d.model,
                latency_ms: d.latency_ms,
                tokens_in: d.tokens_in,
                tokens_out: d.tokens_out,
                output: d.output,
                rejected_reason: d.rejected_reason,
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
        // Guardar la vela live antes de que setData la pise
        const prevLive = lastCandleRef.current;
        candleSeriesRef.current?.setData(data);
        const lastFromApi = data.length ? { ...data[data.length - 1] } : null;
        lastCandleRef.current = lastFromApi;
        // Reactivar la vela live si estaba más adelante que la API
        if (prevLive && lastFromApi) {
            const prevTime = prevLive.time;
            const apiTime = lastFromApi.time;
            if (prevTime > apiTime) {
                // La vela live está en un bucket más nuevo que lo que devolvió la API
                candleSeriesRef.current?.update(prevLive);
                lastCandleRef.current = prevLive;
            }
            else if (prevTime === apiTime) {
                // Mismo bucket: fusionar — el high/low live puede superar al de la DB
                const merged = {
                    time: lastFromApi.time,
                    open: lastFromApi.open,
                    high: Math.max(lastFromApi.high, prevLive.high),
                    low: Math.min(lastFromApi.low, prevLive.low),
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
        const lastOf = (arr) => arr.length ? arr[arr.length - 1].value : null;
        if (showOverlays.emas) {
            const e20 = ema(sourcePoints, 20);
            const e50 = ema(sourcePoints, 50);
            const e200 = ema(sourcePoints, 200);
            ema20Ref.current?.setData(e20);
            ema50Ref.current?.setData(e50);
            ema200Ref.current?.setData(e200);
            setIndicatorValues({ ema20: lastOf(e20), ema50: lastOf(e50), ema200: lastOf(e200) });
        }
        else {
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
            api.trades("closed").then((all) => setClosedTrades(all.slice(0, 50))).catch(() => { });
        }
        if (ev.event === "decision") {
            api.decisions({ agent: "decisor" })
                .then((rows) => setDecisions(rows.slice(0, 80).map((d) => ({
                id: d.id,
                ts: d.ts,
                action: (d.output?.action),
                executed: d.executed,
                model: d.model,
                latency_ms: d.latency_ms,
                tokens_in: d.tokens_in,
                tokens_out: d.tokens_out,
                output: d.output,
                rejected_reason: d.rejected_reason,
            }))))
                .catch(() => { });
        }
    }, [last, timeframe]);
    const fmtPrice = (v) => v.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (_jsxs("div", { className: "rounded-xl overflow-hidden", style: { background: "#0b0e11" }, children: [_jsxs("div", { className: "flex flex-wrap items-center justify-between gap-2 px-4 pt-3 pb-2", children: [_jsxs("div", { className: "flex items-center gap-3 min-w-0", children: [_jsx("span", { className: "text-sm font-bold text-white tracking-wide", children: "BTC/USDT" }), livePrice != null && (_jsx("span", { className: "text-xl font-mono font-bold", style: { color: COLORS.bullish }, children: fmtPrice(livePrice) })), loading && _jsx("span", { className: "text-xs", style: { color: "#848e9c" }, children: "cargando\u2026" })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx("div", { className: "inline-flex rounded gap-0.5", style: { background: "#1e2026" }, children: TIMEFRAMES.map((tf) => (_jsx("button", { onClick: () => setTimeframe(tf), className: "px-2.5 py-1 text-xs font-medium rounded transition-colors", style: timeframe === tf
                                        ? { background: "#2b3139", color: "#f0b90b" }
                                        : { color: "#848e9c" }, children: tf }, tf))) }), _jsxs("div", { className: "inline-flex rounded gap-0.5 text-xs", style: { background: "#1e2026" }, children: [_jsx(Toggle, { label: "EMA", active: showOverlays.emas, onClick: () => setShowOverlays((s) => ({ ...s, emas: !s.emas })) }), _jsx(Toggle, { label: "BB", active: showOverlays.bb, onClick: () => setShowOverlays((s) => ({ ...s, bb: !s.bb })) }), _jsx(Toggle, { label: "Trades", active: showOverlays.closedTrades, onClick: () => setShowOverlays((s) => ({ ...s, closedTrades: !s.closedTrades })) }), _jsx(Toggle, { label: "Se\u00F1ales", active: showOverlays.decisions, onClick: () => setShowOverlays((s) => ({ ...s, decisions: !s.decisions })) })] })] })] }), showOverlays.emas && (indicatorValues.ema20 || indicatorValues.ema50 || indicatorValues.ema200) && (_jsxs("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-0.5 px-4 pb-1.5 text-xs font-mono", children: [indicatorValues.ema20 != null && (_jsxs("span", { style: { color: COLORS.ema20 }, children: ["MA(20) ", _jsx("span", { className: "font-semibold", children: fmtPrice(indicatorValues.ema20) })] })), indicatorValues.ema50 != null && (_jsxs("span", { style: { color: COLORS.ema50 }, children: ["MA(50) ", _jsx("span", { className: "font-semibold", children: fmtPrice(indicatorValues.ema50) })] })), indicatorValues.ema200 != null && (_jsxs("span", { style: { color: COLORS.ema200 }, children: ["MA(200) ", _jsx("span", { className: "font-semibold", children: fmtPrice(indicatorValues.ema200) })] }))] })), _jsx("div", { ref: containerRef, style: { height } }), selectedDecisions.length > 0 && (_jsx(DecisionPanel, { decisions: selectedDecisions, onClose: () => setSelectedDecisions([]) })), _jsx(Legend, { openTrades: openTrades.length })] }));
}
function Toggle({ label, active, onClick }) {
    return (_jsx("button", { onClick: onClick, className: "px-2.5 py-1 rounded transition-colors text-xs", style: active ? { background: "#2b3139", color: "#eaecef" } : { color: "#848e9c" }, children: label }));
}
function DecisionPanel({ decisions, onClose }) {
    const fmtTs = (iso) => new Date(iso).toLocaleString("es-AR", {
        day: "2-digit", month: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    const actionColor = (action) => {
        if (action === "BUY")
            return COLORS.decisionBuy;
        if (action === "SELL")
            return COLORS.decisionSell;
        return "#848e9c";
    };
    return (_jsxs("div", { className: "mx-0 mt-0 text-xs", style: { borderTop: "1px solid #2a2d3a", background: "#0f1217" }, children: [_jsxs("div", { className: "flex items-center justify-between px-4 py-2", style: { borderBottom: "1px solid #2a2d3a" }, children: [_jsxs("span", { className: "font-semibold", style: { color: "#eaecef" }, children: ["Se\u00F1ales en esta vela", _jsx("span", { className: "ml-2 px-1.5 py-0.5 rounded text-[10px]", style: { background: "#2b3139", color: "#848e9c" }, children: decisions.length })] }), _jsx("button", { onClick: onClose, className: "text-lg leading-none", style: { color: "#848e9c" }, title: "Cerrar", children: "\u00D7" })] }), _jsx("div", { className: "divide-y divide-[#2a2d3a]", style: { maxHeight: 280, overflowY: "auto" }, children: decisions.map((d) => {
                    const reasoning = d.output?.reasoning;
                    const confidence = d.output?.confidence;
                    const signal = d.output?.signal;
                    const regime = d.output?.market_regime;
                    const risk = d.output?.risk_assessment;
                    return (_jsxs("div", { className: "px-4 py-3 space-y-2", children: [_jsxs("div", { className: "flex items-center gap-3 flex-wrap", children: [_jsx("span", { style: { color: "#848e9c" }, children: fmtTs(d.ts) }), _jsx("span", { className: "px-2 py-0.5 rounded font-bold text-[11px]", style: { background: `${actionColor(d.action)}22`, color: actionColor(d.action) }, children: d.action ?? "?" }), d.executed ? (_jsx("span", { className: "px-1.5 py-0.5 rounded text-[10px]", style: { background: "#0d2d1a", color: COLORS.decisionBuy }, children: "Ejecutada" })) : (_jsx("span", { className: "px-1.5 py-0.5 rounded text-[10px]", style: { background: "#2d1a1a", color: "#f87171" }, children: "Bloqueada" })), confidence != null && (_jsxs("span", { style: { color: "#848e9c" }, children: ["Confianza: ", _jsx("span", { style: { color: "#eaecef" }, children: typeof confidence === "number" ? `${(confidence * 100).toFixed(0)}%` : confidence })] }))] }), !d.executed && d.rejected_reason && (_jsxs("div", { className: "flex gap-2", children: [_jsx("span", { style: { color: "#848e9c", flexShrink: 0 }, children: "Bloqueada por:" }), _jsx("span", { style: { color: "#fbbf24" }, children: d.rejected_reason })] })), _jsxs("div", { className: "flex flex-wrap gap-x-4 gap-y-1", children: [signal && (_jsxs("span", { style: { color: "#848e9c" }, children: ["Se\u00F1al: ", _jsx("span", { style: { color: "#eaecef" }, children: signal })] })), regime && (_jsxs("span", { style: { color: "#848e9c" }, children: ["R\u00E9gimen: ", _jsx("span", { style: { color: "#eaecef" }, children: regime })] })), risk && (_jsxs("span", { style: { color: "#848e9c" }, children: ["Riesgo: ", _jsx("span", { style: { color: "#eaecef" }, children: risk })] }))] }), reasoning && (_jsxs("p", { className: "leading-relaxed", style: { color: "#c9d1d9", fontStyle: "italic" }, children: ["\"", reasoning, "\""] })), _jsxs("div", { className: "flex flex-wrap gap-x-4 gap-y-0.5", style: { color: "#4b5563" }, children: [_jsx("span", { children: d.model }), d.latency_ms != null && _jsxs("span", { children: [(d.latency_ms / 1000).toFixed(1), "s"] }), d.tokens_in != null && _jsxs("span", { children: [d.tokens_in + (d.tokens_out ?? 0), " tokens"] })] })] }, d.id));
                }) })] }));
}
function Legend({ openTrades }) {
    const items = [
        ["Entry", COLORS.entry, "dashed"],
        ["SL", COLORS.stopLoss, "solid"],
        ["TP", COLORS.takeProfit, "solid"],
    ];
    return (_jsxs("div", { className: "flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-[11px]", style: { borderTop: "1px solid #2a2d3a", color: "#848e9c" }, children: [items.map(([label, color]) => (_jsxs("span", { className: "inline-flex items-center gap-1.5", children: [_jsx("span", { className: "inline-block w-4 h-0.5", style: { backgroundColor: color, opacity: 0.8 } }), label] }, label))), _jsx("span", { className: "ml-auto", style: { color: openTrades > 0 ? "#f0b90b" : "#848e9c" }, children: openTrades > 0
                    ? `${openTrades} posición${openTrades === 1 ? "" : "es"} abierta${openTrades === 1 ? "" : "s"}`
                    : "Sin posiciones abiertas" })] }));
}
