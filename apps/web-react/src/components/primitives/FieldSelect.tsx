import { useId, type ReactNode, type SelectHTMLAttributes } from "react";
import "./FieldInput.css";

export type FieldSelectOption = {
  disabled?: boolean;
  label: string;
  value: string;
};

type FieldSelectProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> & {
  error?: ReactNode;
  helperText?: ReactNode;
  hideLabel?: boolean;
  id?: string;
  label: string;
  options: readonly FieldSelectOption[];
};

export function FieldSelect({
  className,
  error,
  helperText,
  hideLabel = false,
  id,
  label,
  options,
  ...props
}: FieldSelectProps) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const descriptionId =
    helperText || error ? `${selectId}-description` : undefined;
  const classes = ["st-field", className].filter(Boolean).join(" ");

  return (
    <label className={classes}>
      <span
        className={hideLabel ? "st-field__label--hidden" : "st-field__label"}
      >
        {label}
      </span>
      <select
        {...props}
        aria-describedby={descriptionId}
        aria-invalid={Boolean(error) || undefined}
        className="st-field__control st-field__control--select"
        id={selectId}
      >
        {options.map((option) => (
          <option
            disabled={option.disabled}
            key={option.value}
            value={option.value}
          >
            {option.label}
          </option>
        ))}
      </select>
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
