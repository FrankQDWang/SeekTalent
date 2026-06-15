import { Send } from "lucide-react";
import { useState } from "react";
import { Button } from "../primitives/Button";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./MessageComposer.css";

type MessageComposerProps = {
  disabled?: boolean;
  onSubmit?: ((message: string) => void) | undefined;
  placeholder?: string;
};

export function MessageComposer({
  disabled = false,
  onSubmit,
  placeholder = "输入下一步要求",
}: MessageComposerProps) {
  const [message, setMessage] = useState("");
  const trimmed = message.trim();

  return (
    <form
      className="message-composer"
      onSubmit={(event) => {
        event.preventDefault();
        if (trimmed.length === 0 || disabled) {
          return;
        }
        onSubmit?.(trimmed);
        setMessage("");
      }}
    >
      <FieldTextarea
        disabled={disabled}
        hideLabel
        label="下一步要求"
        onChange={(event) => setMessage(event.currentTarget.value)}
        placeholder={placeholder}
        rows={3}
        value={message}
      />
      <Button
        disabled={disabled || trimmed.length === 0}
        icon={<Send aria-hidden="true" size={16} />}
        tone="primary"
        type="submit"
      >
        发送
      </Button>
    </form>
  );
}
