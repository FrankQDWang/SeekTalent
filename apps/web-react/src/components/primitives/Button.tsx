import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./Button.css";

type ButtonTone = "primary" | "secondary" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  loading?: boolean;
  tone?: ButtonTone;
};

export function Button({
  children,
  className,
  disabled,
  icon,
  loading = false,
  tone = "secondary",
  type = "button",
  ...props
}: ButtonProps) {
  const classes = ["st-button", className].filter(Boolean).join(" ");

  return (
    <button
      {...props}
      aria-busy={loading || undefined}
      className={classes}
      data-loading={loading ? "true" : "false"}
      data-tone={tone}
      disabled={disabled || loading}
      type={type}
    >
      {loading ? (
        <span className="st-button-spinner" aria-hidden="true" />
      ) : (
        icon
      )}
      <span>{loading ? "处理中" : children}</span>
    </button>
  );
}
