import type { Timeframe } from "../../types";

export const TIMEFRAMES: ReadonlyArray<Timeframe> = ["1m", "5m", "15m", "1h", "4h"];

const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 5 * 60,
  "15m": 15 * 60,
  "1h": 60 * 60,
  "4h": 4 * 60 * 60,
};

export function timeframeSeconds(tf: Timeframe): number {
  return TIMEFRAME_SECONDS[tf];
}

export function bucketStart(tsSeconds: number, tf: Timeframe): number {
  const size = TIMEFRAME_SECONDS[tf];
  return Math.floor(tsSeconds / size) * size;
}

export function timeframeFromConfigMinutes(minutes: number | null | undefined): Timeframe {
  if (minutes == null) return "5m";
  if (minutes <= 1) return "1m";
  if (minutes <= 5) return "5m";
  if (minutes <= 15) return "15m";
  if (minutes <= 60) return "1h";
  return "4h";
}
