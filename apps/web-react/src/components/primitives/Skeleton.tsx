import type { HTMLAttributes } from "react";
import "./Skeleton.css";

type SkeletonProps = HTMLAttributes<HTMLDivElement> & {
  lines?: number;
};

export function Skeleton({ className, lines = 1, ...props }: SkeletonProps) {
  const classes = ["st-skeleton", className].filter(Boolean).join(" ");
  const safeLines = Math.max(1, Math.min(lines, 8));

  return (
    <div {...props} aria-busy="true" className={classes} role="status">
      {Array.from({ length: safeLines }, (_, index) => (
        <span
          aria-hidden="true"
          className="st-skeleton__line"
          data-last={index === safeLines - 1 ? "true" : "false"}
          key={index}
        />
      ))}
    </div>
  );
}
