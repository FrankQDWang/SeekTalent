import { ClipboardCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
  WorkbenchV2RequirementActionRequest,
  WorkbenchV2TranscriptEvent,
} from "../../lib/api/workbenchV2Types";
import { Button } from "../primitives/Button";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./RequirementFormEvent.css";

type RequirementFormEventProps = {
  actionPending?: boolean;
  event: WorkbenchV2TranscriptEvent;
  onAction?:
    | ((payload: WorkbenchV2RequirementActionRequest) => Promise<void> | void)
    | undefined;
};

type RequirementDraft = {
  canConfirm: boolean;
  otherInputPrompt: string;
  sections: RequirementSection[];
};

type RequirementSection = {
  displayName: string;
  items: RequirementItem[];
  sectionId: string;
};

type RequirementItem = {
  allowedActions: string[];
  itemId: string;
  selected: boolean;
  status: string;
  text: string;
};

export function RequirementFormEvent({
  actionPending = false,
  event,
  onAction,
}: RequirementFormEventProps) {
  const draft = useMemo(() => readRequirementDraft(event.payload), [event]);
  const readonly =
    readBoolean(event.payload, "readonly") === true ||
    event.type === "requirement_form_confirmed";
  const [otherText, setOtherText] = useState("");
  const [localSelectedByItemId, setLocalSelectedByItemId] = useState<
    Record<string, boolean>
  >({});
  const trimmedOtherText = otherText.trim();

  useEffect(() => {
    const next: Record<string, boolean> = {};
    for (const section of draft?.sections ?? []) {
      for (const item of section.items) {
        next[item.itemId] = item.selected;
      }
    }
    setLocalSelectedByItemId(next);
  }, [draft]);

  if (draft === null) {
    return null;
  }

  const disabled = readonly || actionPending || onAction === undefined;

  async function submitAction(payload: WorkbenchV2RequirementActionRequest) {
    await onAction?.(payload);
  }

  async function confirm() {
    if (disabled || !draft?.canConfirm) {
      return;
    }
    await submitAction(
      trimmedOtherText.length > 0
        ? { action: "confirm", text: trimmedOtherText }
        : { action: "confirm" },
    );
    setOtherText("");
  }

  if (readonly) {
    return <ConfirmedRequirementSummary draft={draft} />;
  }

  return (
    <section className="requirement-form-event" aria-label="需求确认">
      <div className="requirement-form-event__header">
        <ClipboardCheck aria-hidden="true" size={18} />
        <div>
          <h2>需求确认</h2>
          <p>请确认本轮检索需求</p>
        </div>
      </div>

      <div className="requirement-form-event__sections">
        {draft.sections.map((section) => {
          const items = section.items.filter(
            (item) => item.status !== "deleted",
          );
          if (items.length === 0) {
            return null;
          }
          return (
            <section
              className="requirement-form-event__section"
              key={section.sectionId}
            >
              <div className="requirement-form-event__section-header">
                <h3>{section.displayName}</h3>
              </div>
              <div className="requirement-form-event__items">
                {items.map((item) => {
                  const canToggle =
                    item.allowedActions.includes("set_selected") && !readonly;
                  const selected =
                    localSelectedByItemId[item.itemId] ?? item.selected;
                  return (
                    <label
                      className="requirement-form-event__item"
                      data-selected={selected ? "true" : "false"}
                      key={item.itemId}
                    >
                      <input
                        checked={selected}
                        disabled={disabled || !canToggle}
                        onChange={(changeEvent) => {
                          const nextSelected =
                            changeEvent.currentTarget.checked;
                          const previousSelected = selected;
                          setLocalSelectedByItemId((current) => ({
                            ...current,
                            [item.itemId]: nextSelected,
                          }));
                          void Promise.resolve(
                            submitAction({
                              action: "set_selected",
                              itemId: item.itemId,
                              selected: nextSelected,
                            }),
                          ).catch(() => {
                            setLocalSelectedByItemId((current) => ({
                              ...current,
                              [item.itemId]: previousSelected,
                            }));
                          });
                        }}
                        type="checkbox"
                      />
                      <span
                        aria-hidden="true"
                        className="requirement-form-event__box"
                      />
                      <span className="requirement-form-event__item-text">
                        {item.text}
                      </span>
                    </label>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      <div className="requirement-form-event__other">
        <FieldTextarea
          disabled={disabled}
          label={draft.otherInputPrompt}
          onChange={(inputEvent) =>
            setOtherText(inputEvent.currentTarget.value)
          }
          placeholder="输入补充要求"
          rows={2}
          value={otherText}
        />
      </div>

      <div className="requirement-form-event__actions">
        <Button
          disabled={disabled || !draft.canConfirm}
          onClick={() => void confirm()}
          tone="primary"
        >
          确认需求
        </Button>
      </div>
    </section>
  );
}

function ConfirmedRequirementSummary({ draft }: { draft: RequirementDraft }) {
  const selectedSections = draft.sections
    .map((section) => ({
      ...section,
      items: section.items.filter(
        (item) => item.status !== "deleted" && item.selected,
      ),
    }))
    .filter((section) => section.items.length > 0);

  return (
    <section
      aria-label="需求确认"
      className="requirement-form-event requirement-form-event--confirmed"
    >
      <div className="requirement-form-event__header">
        <ClipboardCheck aria-hidden="true" size={18} />
        <div>
          <h2>需求确认</h2>
          <p>已确认需求，后续运行会按这些条件执行</p>
        </div>
      </div>

      {selectedSections.length > 0 ? (
        <div className="requirement-form-event__sections">
          {selectedSections.map((section) => (
            <section
              className="requirement-form-event__section"
              key={section.sectionId}
            >
              <div className="requirement-form-event__section-header">
                <h3>{section.displayName}</h3>
              </div>
              <div className="requirement-form-event__items">
                {section.items.map((item) => (
                  <span
                    className="requirement-form-event__confirmed-item"
                    key={item.itemId}
                  >
                    {item.text}
                  </span>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <p className="requirement-form-event__empty-confirmed">
          没有保留的筛选条件。
        </p>
      )}

      <div className="requirement-form-event__confirmed-footer">需求已确认</div>
    </section>
  );
}

function readRequirementDraft(
  payload: Record<string, unknown>,
): RequirementDraft | null {
  const draft = readRecord(payload, "draft");
  if (draft === null) {
    return null;
  }

  const sections = readArray(draft, "sections").flatMap((section) => {
    const sectionRecord = asRecord(section);
    if (sectionRecord === null) {
      return [];
    }
    const sectionId =
      readString(sectionRecord, "section_id") ??
      readString(sectionRecord, "sectionId");
    const displayName =
      readString(sectionRecord, "display_name") ??
      readString(sectionRecord, "displayName") ??
      "需求";
    if (sectionId === null) {
      return [];
    }
    return [
      {
        displayName,
        items: readRequirementItems(sectionRecord),
        sectionId,
      },
    ];
  });

  return {
    canConfirm:
      readBoolean(draft, "can_confirm") ??
      readBoolean(draft, "canConfirm") ??
      true,
    otherInputPrompt:
      readString(draft, "other_input_prompt") ??
      readString(draft, "otherInputPrompt") ??
      "补充其他要求",
    sections,
  };
}

function readRequirementItems(
  section: Record<string, unknown>,
): RequirementItem[] {
  return readArray(section, "items").flatMap((item) => {
    const itemRecord = asRecord(item);
    if (itemRecord === null) {
      return [];
    }
    const itemId =
      readString(itemRecord, "item_id") ?? readString(itemRecord, "itemId");
    const text = readString(itemRecord, "text");
    if (itemId === null || text === null) {
      return [];
    }
    return [
      {
        allowedActions: normalizeAllowedActions(
          readStringArray(itemRecord, "allowed_actions", "allowedActions"),
        ),
        itemId,
        selected: readBoolean(itemRecord, "selected") ?? false,
        status: readString(itemRecord, "status") ?? "active",
        text,
      },
    ];
  });
}

function normalizeAllowedActions(actions: string[]): string[] {
  const normalized = new Set(actions);
  if (normalized.has("select")) {
    normalized.add("set_selected");
  }
  return [...normalized];
}

function readStringArray(
  record: Record<string, unknown>,
  snakeField: string,
  camelField: string,
): string[] {
  const value = record[snakeField] ?? record[camelField];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function readArray(record: Record<string, unknown>, field: string): unknown[] {
  const value = record[field];
  return Array.isArray(value) ? value : [];
}

function readRecord(
  record: Record<string, unknown>,
  field: string,
): Record<string, unknown> | null {
  return asRecord(record[field]);
}

function readString(
  record: Record<string, unknown>,
  field: string,
): string | null {
  const value = record[field];
  return typeof value === "string" ? value : null;
}

function readBoolean(
  record: Record<string, unknown>,
  field: string,
): boolean | null {
  const value = record[field];
  return typeof value === "boolean" ? value : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}
