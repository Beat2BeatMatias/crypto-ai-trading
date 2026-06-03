export function computePnlUsdt(
  entry: number,
  quantity: number,
  exitPrice: number | null | undefined,
  side = "BUY",
): number | null {
  if (exitPrice == null || entry <= 0 || quantity <= 0) return null;
  if (side.toUpperCase() === "BUY") return Math.round((exitPrice - entry) * quantity * 10000) / 10000;
  return Math.round((entry - exitPrice) * quantity * 10000) / 10000;
}

export function computePnlPct(
  entry: number,
  exitPrice: number | null | undefined,
  side = "BUY",
): number | null {
  if (exitPrice == null || entry <= 0) return null;
  if (side.toUpperCase() === "BUY") return Math.round(((exitPrice - entry) / entry) * 100 * 10000) / 10000;
  return Math.round(((entry - exitPrice) / entry) * 100 * 10000) / 10000;
}

export function fmtPnlValue(
  pnlUsdt: number | null | undefined,
  pnlPct: number | null | undefined,
  decimals = 2,
): string {
  if (pnlUsdt == null) return "—";
  const sign = pnlUsdt >= 0 ? "+" : "";
  const amount = `${sign}$${pnlUsdt.toLocaleString("es-AR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
  if (pnlPct == null) return amount;
  const pctSign = pnlPct >= 0 ? "+" : "";
  return `${amount} (${pctSign}${pnlPct.toFixed(2)}%)`;
}

export function pnlColorClass(pnl: number | null | undefined): string {
  if (pnl == null) return "text-zinc-400";
  if (pnl > 0) return "text-emerald-400";
  if (pnl < 0) return "text-red-400";
  return "text-zinc-400";
}

export type PositionSide = "LONG" | "SHORT";

export function tradeDirection(trade: { position_side?: string; side?: string }): PositionSide {
  if (trade.position_side === "SHORT") return "SHORT";
  if (trade.side === "SELL" && trade.position_side !== "LONG") return "SHORT";
  return "LONG";
}

export function computePnlUsdtDirectional(
  entry: number,
  quantity: number,
  exitPrice: number | null | undefined,
  direction: PositionSide,
): number | null {
  return computePnlUsdt(entry, quantity, exitPrice, direction === "SHORT" ? "SELL" : "BUY");
}

export function computePnlPctDirectional(
  entry: number,
  exitPrice: number | null | undefined,
  direction: PositionSide,
): number | null {
  return computePnlPct(entry, exitPrice, direction === "SHORT" ? "SELL" : "BUY");
}

export function slDistancePct(
  entry: number,
  stopLoss: number,
  direction: PositionSide,
): number | null {
  if (entry <= 0) return null;
  if (direction === "SHORT") return ((stopLoss - entry) / entry) * 100;
  return ((entry - stopLoss) / entry) * 100;
}

export function tpDistancePct(
  entry: number,
  takeProfit: number,
  direction: PositionSide,
): number | null {
  if (entry <= 0) return null;
  if (direction === "SHORT") return ((entry - takeProfit) / entry) * 100;
  return ((takeProfit - entry) / entry) * 100;
}
