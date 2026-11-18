import type { ReactNode } from "react";
import type { TooltipProps } from "recharts";

/** Chart colours come from CSS variables so light/dark each use their own validated steps. */
export const C = {
  s1: "var(--chart-1)", s2: "var(--chart-2)", s3: "var(--chart-3)", s4: "var(--chart-4)",
  s5: "var(--chart-5)", s6: "var(--chart-6)", s7: "var(--chart-7)", s8: "var(--chart-8)",
  grid: "var(--chart-grid)", axis: "var(--chart-axis)", danger: "rgb(var(--danger))",
};
export const SERIES = [C.s1, C.s2, C.s3, C.s4, C.s5, C.s6, C.s7, C.s8];
export const axisProps = { stroke: C.axis, tickLine: false, axisLine: false, fontSize: 11 } as const;

export function ChartTooltip({ active, payload, label, fmt, labelFmt }: TooltipProps<number, string> & { fmt?: (v: number, name: string) => ReactNode; labelFmt?: (l: unknown) => ReactNode }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border-strong bg-overlay px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-fg">{labelFmt ? labelFmt(label) : String(label)}</div>
      {payload.map((p) => (
        <div key={String(p.dataKey)} className="flex items-center gap-2 text-muted">
          <span className="h-2 w-2 rounded-sm" style={{ background: p.color }} />
          <span>{p.name}</span>
          <span className="num ml-auto pl-3 font-medium text-fg">{fmt ? fmt(Number(p.value), String(p.name)) : Number(p.value).toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
