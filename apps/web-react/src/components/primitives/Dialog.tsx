import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "./Button";
import "./Dialog.css";

type DialogProps = {
  children: ReactNode;
  onClose: () => void;
  open: boolean;
  title: string;
};

export function Dialog({ children, onClose, open, title }: DialogProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="st-dialog-backdrop">
      <section
        aria-label={title}
        aria-modal="true"
        className="st-dialog"
        role="dialog"
      >
        <header className="st-dialog__header">
          <h2>{title}</h2>
          <Button
            aria-label="关闭"
            icon={<X aria-hidden="true" size={16} />}
            onClick={onClose}
          />
        </header>
        <div className="st-dialog__body">{children}</div>
      </section>
    </div>
  );
}
