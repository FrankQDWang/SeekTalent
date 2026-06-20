import { ClipboardCheck } from "lucide-react";
import { useState } from "react";
import type {
  AgentWorkbenchPendingActions,
  AgentWorkbenchRequirementDraft,
  AgentWorkbenchRequirementDraftItem,
} from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import { FieldTextarea } from "../primitives/FieldTextarea";
import "./RequirementReviewPanel.css";

type RequirementReviewPanelProps = {
  amending?: boolean;
  confirming?: boolean;
  onAddOther?: ((text: string) => Promise<void> | void) | undefined;
  onConfirm?: (() => void) | undefined;
  onToggleItem?:
    | ((item: AgentWorkbenchRequirementDraftItem, selected: boolean) => void)
    | undefined;
  pendingActions: AgentWorkbenchPendingActions;
  requirementDraft: AgentWorkbenchRequirementDraft | null | undefined;
  updatingItemIds?: readonly string[] | undefined;
};

export function RequirementReviewPanel({
  amending = false,
  confirming = false,
  onAddOther,
  onConfirm,
  onToggleItem,
  pendingActions,
  requirementDraft,
  updatingItemIds = [],
}: RequirementReviewPanelProps) {
  const [otherText, setOtherText] = useState("");
  const trimmedOtherText = otherText.trim();

  if (!requirementDraft && !pendingActions.primary) {
    return null;
  }

  const updatingItems = new Set(updatingItemIds);
  const canConfirm =
    Boolean(requirementDraft?.canConfirm) &&
    pendingActions.allowed.includes("confirm_requirements");
  const canAddOther =
    Boolean(onAddOther) && trimmedOtherText.length > 0 && !amending;

  return (
    <section className="requirement-review-panel" aria-label="需求确认">
      <div className="requirement-review-panel__header">
        <ClipboardCheck aria-hidden="true" size={18} />
        <span>
          <strong>{requirementDraft?.title ?? "待处理动作"}</strong>
          <em>{requirementDraft?.summary ?? pendingActions.primary}</em>
        </span>
        {requirementDraft ? (
          <span className="requirement-review-panel__status">
            {requirementStatusLabel(requirementDraft.status)}
          </span>
        ) : null}
      </div>

      {requirementDraft ? (
        <div className="requirement-review-panel__sections">
          {requirementDraft.sections.map((section) => {
            const items = section.items.filter(
              (item) => item.status !== "deleted",
            );
            if (items.length === 0) {
              return null;
            }
            return (
              <section
                className="requirement-review-section"
                key={section.sectionId}
              >
                <div className="requirement-review-section__header">
                  <h3>{section.displayName}</h3>
                  <span>{items.length} 项</span>
                </div>
                <div className="requirement-review-section__items">
                  {items.map((item) => {
                    const canToggle =
                      item.allowedActions.includes("set_selected");
                    const updating = updatingItems.has(item.itemId);
                    return (
                      <button
                        aria-pressed={item.selected}
                        className="requirement-review-item"
                        data-selected={item.selected ? "true" : "false"}
                        disabled={!canToggle || updating}
                        key={item.itemId}
                        onClick={() => onToggleItem?.(item, !item.selected)}
                        type="button"
                      >
                        <span>{item.text}</span>
                        <em>
                          {updating
                            ? "更新中"
                            : item.selected
                              ? "已选择"
                              : "未选择"}
                        </em>
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      ) : null}

      {requirementDraft ? (
        <form
          className="requirement-review-panel__other"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!onAddOther || trimmedOtherText.length === 0 || amending) {
              return;
            }
            try {
              await onAddOther(trimmedOtherText);
              setOtherText("");
            } catch {
              // The route-level mutation error keeps the text available for retry.
            }
          }}
        >
          <FieldTextarea
            disabled={amending}
            label={requirementDraft.otherInputPrompt}
            onChange={(event) => setOtherText(event.currentTarget.value)}
            placeholder="补充未被提取的候选人要求"
            rows={2}
            value={otherText}
          />
          <Button
            disabled={!canAddOther}
            loading={amending}
            tone="secondary"
            type="submit"
          >
            添加
          </Button>
        </form>
      ) : null}

      <div className="requirement-review-panel__actions">
        {canConfirm ? (
          <Button loading={confirming} onClick={onConfirm} tone="primary">
            确认需求
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function requirementStatusLabel(status: string): string {
  if (status === "needs_review") {
    return "待确认";
  }
  if (status === "confirmed" || status === "approved") {
    return "已确认";
  }
  return status;
}
