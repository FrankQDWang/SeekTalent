import { Send } from "lucide-react";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { Button } from "../primitives/Button";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./MessageComposer.css";

type MessageComposerProps = {
  disabled?: boolean;
  loading?: boolean;
  onSubmit?: ((message: string) => Promise<void> | void) | undefined;
  placeholder?: string;
};

const FALLBACK_INITIAL_TEXTAREA_HEIGHT = 44;

export function MessageComposer({
  disabled = false,
  loading = false,
  onSubmit,
  placeholder = "输入下一步要求",
}: MessageComposerProps) {
  const formRef = useRef<HTMLFormElement | null>(null);
  const initialTextareaHeightRef = useRef<number | null>(null);
  const [message, setMessage] = useState("");
  const trimmed = message.trim();
  const submitDisabled = disabled || loading;
  const resizeTextarea = useCallback((textarea: HTMLTextAreaElement) => {
    if (initialTextareaHeightRef.current === null) {
      const computed = window.getComputedStyle(textarea);
      const minHeight = Number.parseFloat(computed.minHeight);
      initialTextareaHeightRef.current = Math.max(
        textarea.clientHeight,
        Number.isFinite(minHeight) ? minHeight : 0,
        FALLBACK_INITIAL_TEXTAREA_HEIGHT,
      );
    }
    const initialHeight = initialTextareaHeightRef.current;
    const maxHeight = initialHeight * 3;
    textarea.style.height = `${initialHeight}px`;
    const nextHeight = Math.min(
      Math.max(textarea.scrollHeight, initialHeight),
      maxHeight,
    );
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY =
      textarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }, []);

  useLayoutEffect(() => {
    const textarea = formRef.current?.querySelector("textarea");
    if (textarea instanceof HTMLTextAreaElement) {
      resizeTextarea(textarea);
    }
  }, [message, resizeTextarea]);

  useLayoutEffect(() => {
    const form = formRef.current;
    const parent = form?.parentElement;
    if (!form || !parent) {
      return undefined;
    }

    const updateComposerHeight = () => {
      parent.style.setProperty(
        "--message-composer-height",
        `${String(Math.ceil(form.getBoundingClientRect().height))}px`,
      );
    };

    updateComposerHeight();
    if (typeof ResizeObserver === "undefined") {
      return () => {
        parent.style.removeProperty("--message-composer-height");
      };
    }

    const observer = new ResizeObserver(updateComposerHeight);
    observer.observe(form);
    return () => {
      observer.disconnect();
      parent.style.removeProperty("--message-composer-height");
    };
  }, []);

  return (
    <form
      className="message-composer"
      ref={formRef}
      onSubmit={async (event) => {
        event.preventDefault();
        if (trimmed.length === 0 || submitDisabled) {
          return;
        }
        const submittedMessage = trimmed;
        setMessage("");
        try {
          await onSubmit?.(submittedMessage);
        } catch {
          // The route-level mutation error keeps the text available for retry.
          setMessage(submittedMessage);
        } finally {
          formRef.current?.querySelector("textarea")?.focus();
        }
      }}
    >
      <FieldTextarea
        disabled={submitDisabled}
        hideLabel
        label="下一步要求"
        onChange={(event) => {
          setMessage(event.currentTarget.value);
          resizeTextarea(event.currentTarget);
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
            return;
          }
          event.preventDefault();
          formRef.current?.requestSubmit();
        }}
        placeholder={placeholder}
        rows={1}
        value={message}
      />
      <Button
        disabled={submitDisabled || trimmed.length === 0}
        icon={<Send aria-hidden="true" size={16} />}
        loading={loading}
        tone="primary"
        type="submit"
      >
        发送
      </Button>
    </form>
  );
}
