import type { ReactNode } from "react";
import "./Toast.css";

type ToastTone = "info" | "success" | "warning" | "danger";

type ToastProps = {
  children?: ReactNode;
  title: string;
  tone?: ToastTone;
};

export function Toast({ children, title, tone = "info" }: ToastProps) {
  return (
    <section
      aria-label={title}
      className="st-toast"
      data-tone={tone}
      role="status"
    >
      <strong>{title}</strong>
      {children ? <span>{children}</span> : null}
    </section>
  );
}
