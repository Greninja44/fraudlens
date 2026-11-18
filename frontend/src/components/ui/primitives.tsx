import { forwardRef, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-lg border border-border bg-surface", className)} {...p} />;
}
export function CardHeader({ title, subtitle, actions, className }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 border-b border-border px-4 py-3", className)}>
      <div className="min-w-0">
        <h3 className="text-[13px] font-semibold text-fg">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
export function CardBody({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...p} />;
}

const field = "h-8 w-full rounded-md border border-border bg-bg px-2.5 text-[13px] text-fg placeholder:text-subtle focus:border-accent focus:outline-none disabled:opacity-60";
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn(field, className)} {...p} />
));
Input.displayName = "Input";
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...p }, ref) => (
  <select ref={ref} className={cn(field, "pr-7", className)} {...p} />
));
Select.displayName = "Select";
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn(field, "h-auto min-h-[72px] py-2 leading-relaxed", className)} {...p} />
));
Textarea.displayName = "Textarea";

export function Label({ children, hint, className }: { children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <label className={cn("mb-1 block text-xs font-medium text-muted", className)}>
      {children} {hint && <span className="font-normal text-subtle">· {hint}</span>}
    </label>
  );
}
export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <Label hint={hint}>{label}</Label>
      {children}
    </div>
  );
}

const tones = {
  neutral: "bg-raised text-muted border-border",
  accent: "bg-accent/10 text-accent border-accent/30",
  danger: "bg-danger/10 text-danger border-danger/35",
  warning: "bg-warning/10 text-warning border-warning/35",
  success: "bg-success/10 text-success border-success/35",
  orange: "bg-[#d95926]/10 text-[#ef7a4a] border-[#d95926]/35",
};
export type Tone = keyof typeof tones;
export function Badge({ tone = "neutral", className, children, title }: { tone?: Tone; className?: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-2xs font-medium", tones[tone], className)}>
      {children}
    </span>
  );
}

export function Table({ className, ...p }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-[12.5px]", className)} {...p} />
    </div>
  );
}
export function Th({ className, ...p }: HTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("sticky top-0 z-[1] whitespace-nowrap border-b border-border bg-surface px-3 py-2 text-left text-2xs font-semibold uppercase tracking-wide text-subtle", className)} {...p} />;
}
export function Td({ className, ...p }: HTMLAttributes<HTMLTableCellElement> & { colSpan?: number }) {
  return <td className={cn("border-b border-border/60 px-3 py-2 align-middle", className)} {...p} />;
}
export function Tr({ className, onClick, ...p }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr onClick={onClick} className={cn(onClick && "cursor-pointer hover:bg-raised", className)} {...p} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-raised", className)} />;
}

export function Tabs<T extends string>({ value, onChange, items, className }: { value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[]; className?: string }) {
  return (
    <div role="tablist" className={cn("flex gap-1 border-b border-border", className)}>
      {items.map((it) => (
        <button key={it.value} role="tab" aria-selected={value === it.value} onClick={() => onChange(it.value)}
          className={cn("-mb-px border-b-2 px-3 py-2 text-[13px] font-medium transition-colors",
            value === it.value ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function KV({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-[12.5px]", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-subtle">{k}</dt>
          <dd className="min-w-0 break-words text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
