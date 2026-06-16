import { useId, type ReactNode, type TextareaHTMLAttributes } from "react";
import "./FieldInput.css";

type FieldTextareaProps = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  "id"
> & {
  error?: ReactNode;
  helperText?: ReactNode;
  hideLabel?: boolean;
  id?: string;
  label: string;
};

export function FieldTextarea({
  className,
  error,
  helperText,
  hideLabel = false,
  id,
  label,
  ...props
}: FieldTextareaProps) {
  const generatedId = useId();
  const textareaId = id ?? generatedId;
  const descriptionId =
    helperText || error ? `${textareaId}-description` : undefined;
  const classes = ["st-field", className].filter(Boolean).join(" ");

  return (
    <label className={classes}>
      <span
        className={hideLabel ? "st-field__label--hidden" : "st-field__label"}
      >
        {label}
      </span>
      <textarea
        {...props}
        aria-describedby={descriptionId}
        aria-invalid={Boolean(error) || undefined}
        className="st-field__control st-field__control--textarea"
        id={textareaId}
      />
      {helperText || error ? (
        <span
          className="st-field__description"
          data-tone={error ? "error" : "muted"}
          id={descriptionId}
        >
          {error ?? helperText}
        </span>
      ) : null}
    </label>
  );
}
