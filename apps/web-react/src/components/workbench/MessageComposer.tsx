import { Send } from "lucide-react";
import { useState } from "react";
import { Button } from "../primitives/Button";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./MessageComposer.css";

type MessageComposerProps = {
  disabled?: boolean;
  loading?: boolean;
  onSubmit?: ((message: string) => Promise<void> | void) | undefined;
  placeholder?: string;
};

export function MessageComposer({
  disabled = false,
  loading = false,
  onSubmit,
  placeholder = "输入下一步要求",
}: MessageComposerProps) {
  const [message, setMessage] = useState("");
  const trimmed = message.trim();
  const submitDisabled = disabled || loading;

  return (
    <form
      className="message-composer"
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
        }
      }}
    >
      <FieldTextarea
        disabled={submitDisabled}
        hideLabel
        label="下一步要求"
        onChange={(event) => setMessage(event.currentTarget.value)}
        placeholder={placeholder}
        rows={3}
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
