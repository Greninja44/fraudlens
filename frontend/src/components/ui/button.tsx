import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const variants = {
  primary: "bg-accent text-accent-fg hover:bg-accent/90 border border-accent",
  secondary: "bg-raised text-fg border border-border hover:border-border-strong hover:bg-overlay",
  ghost: "text-muted hover:text-fg hover:bg-raised border border-transparent",
  danger: "bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25",
  success: "bg-success/15 text-success border border-success/40 hover:bg-success/25",
};
const sizes = { sm: "h-7 px-2.5 text-xs gap-1.5", md: "h-8 px-3 text-[13px] gap-2", icon: "h-8 w-8 justify-center" };

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  loading?: boolean;
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", loading, disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn("inline-flex items-center rounded-md font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap",
        variants[variant], sizes[size], className)}
      {...props}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = "Button";
