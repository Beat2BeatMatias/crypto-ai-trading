export function ema(candles, period) {
    if (candles.length === 0 || period <= 0)
        return [];
    const k = 2 / (period + 1);
    const out = [];
    let prev = null;
    let warmupSum = 0;
    for (let i = 0; i < candles.length; i++) {
        const c = candles[i];
        if (i < period) {
            warmupSum += c.close;
            if (i === period - 1) {
                prev = warmupSum / period;
                out.push({ time: c.time, value: prev });
            }
            continue;
        }
        prev = c.close * k + prev * (1 - k);
        out.push({ time: c.time, value: prev });
    }
    return out;
}
export function bollingerBands(candles, period = 20, stdDev = 2) {
    const upper = [];
    const middle = [];
    const lower = [];
    if (candles.length < period)
        return { upper, middle, lower };
    for (let i = period - 1; i < candles.length; i++) {
        let sum = 0;
        for (let j = i - period + 1; j <= i; j++)
            sum += candles[j].close;
        const mean = sum / period;
        let varSum = 0;
        for (let j = i - period + 1; j <= i; j++) {
            const diff = candles[j].close - mean;
            varSum += diff * diff;
        }
        const sd = Math.sqrt(varSum / (period - 1));
        const t = candles[i].time;
        upper.push({ time: t, value: mean + stdDev * sd });
        middle.push({ time: t, value: mean });
        lower.push({ time: t, value: mean - stdDev * sd });
    }
    return { upper, middle, lower };
}
