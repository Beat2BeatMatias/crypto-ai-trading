import { fmtPnlValue, pnlColorClass } from "../lib/pnl";

interface PnlRowProps {
  label: string;
  pnlUsdt: number | null | undefined;
  pnlPct: number | null | undefined;
  labelClass?: string;
}

export function PnlRow({ label, pnlUsdt, pnlPct, labelClass = "text-zinc-500" }: PnlRowProps) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className={`text-xs ${labelClass}`}>{label}</span>
      <span className={`font-mono text-sm font-semibold ${pnlColorClass(pnlUsdt)}`}>
        {fmtPnlValue(pnlUsdt, pnlPct)}
      </span>
    </div>
  );
}
