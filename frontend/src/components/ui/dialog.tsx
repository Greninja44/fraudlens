import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: {
  open: boolean; onOpenChange: (o: boolean) => void; title: ReactNode; description?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <D.Content className={cn("fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-border bg-surface shadow-2xl",
          wide ? "max-w-3xl" : "max-w-lg")}>
          <div className="flex items-start justify-between border-b border-border px-5 py-3.5">
            <div>
              <D.Title className="text-sm font-semibold">{title}</D.Title>
              {description ? <D.Description className="mt-0.5 text-xs text-muted">{description}</D.Description> : <D.Description className="sr-only">{String(title)}</D.Description>}
            </div>
            <D.Close className="rounded p-1 text-muted hover:bg-raised hover:text-fg" aria-label="Close"><X className="h-4 w-4" /></D.Close>
          </div>
          <div className="px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

export function Drawer({ open, onOpenChange, title, children, actions }: { open: boolean; onOpenChange: (o: boolean) => void; title: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/50" />
        <D.Content className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[640px] flex-col border-l border-border bg-surface shadow-2xl">
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
            <D.Title className="min-w-0 truncate text-sm font-semibold">{title}</D.Title>
            <D.Description className="sr-only">Details</D.Description>
            <div className="flex items-center gap-2">
              {actions}
              <D.Close className="rounded p-1 text-muted hover:bg-raised hover:text-fg" aria-label="Close"><X className="h-4 w-4" /></D.Close>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-5">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
