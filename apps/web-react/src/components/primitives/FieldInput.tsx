import { useId, type InputHTMLAttributes, type ReactNode } from "react";
import "./FieldInput.css";

type FieldInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "id"> & {
  error?: ReactNode;
  helperText?: ReactNode;
  hideLabel?: boolean;
  id?: string;
  label: string;
};

export function FieldInput({
  className,
  error,
  helperText,
  hideLabel = false,
  id,
  label,
  ...props
}: FieldInputProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const descriptionId =
    helperText || error ? `${inputId}-description` : undefined;
  const classes = ["st-field", className].filter(Boolean).join(" ");

  return (
    <label className={classes}>
      <span
        className={hideLabel ? "st-field__label--hidden" : "st-field__label"}
      >
        {label}
      </span>
      <input
        {...props}
        aria-describedby={descriptionId}
        aria-invalid={Boolean(error) || undefined}
        className="st-field__control"
        id={inputId}
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
