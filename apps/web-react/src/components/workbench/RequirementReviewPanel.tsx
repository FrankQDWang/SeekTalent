import { ClipboardCheck } from "lucide-react";
import { useEffect, useState } from "react";
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
    | ((
        item: AgentWorkbenchRequirementDraftItem,
        selected: boolean,
      ) => Promise<void> | void)
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
  const [
    confirmingSupplementalRequirement,
    setConfirmingSupplementalRequirement,
  ] = useState(false);
  const [localSelectedByItemId, setLocalSelectedByItemId] = useState<
    Record<string, boolean>
  >({});
  const trimmedOtherText = otherText.trim();

  useEffect(() => {
    const next: Record<string, boolean> = {};
    for (const section of requirementDraft?.sections ?? []) {
      for (const item of section.items) {
        next[item.itemId] = item.selected;
      }
    }
    setLocalSelectedByItemId(next);
  }, [requirementDraft]);

  if (!requirementDraft && !pendingActions.primary) {
    return null;
  }

  const updatingItems = new Set(updatingItemIds);
  const canConfirm =
    Boolean(requirementDraft?.canConfirm) &&
    pendingActions.allowed.includes("confirm_requirements");
  const isConfirming =
    confirming || amending || confirmingSupplementalRequirement;

  async function handleConfirm() {
    if (!onConfirm || isConfirming) {
      return;
    }
    if (onAddOther && trimmedOtherText.length > 0) {
      setConfirmingSupplementalRequirement(true);
      try {
        await onAddOther(trimmedOtherText);
        setOtherText("");
      } catch {
        return;
      } finally {
        setConfirmingSupplementalRequirement(false);
      }
    }
    onConfirm();
  }

  return (
    <section className="requirement-review-panel" aria-label="需求确认">
      <div className="requirement-review-panel__header">
        <ClipboardCheck aria-hidden="true" size={26} />
        <h2>需求确认</h2>
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
                    const selected =
                      localSelectedByItemId[item.itemId] ?? item.selected;
                    const updating = updatingItems.has(item.itemId);
                    return (
                      <label
                        className="requirement-review-item"
                        data-selected={selected ? "true" : "false"}
                        data-updating={updating ? "true" : "false"}
                        key={item.itemId}
                      >
                        <input
                          checked={selected}
                          disabled={!canToggle}
                          onChange={(event) => {
                            const nextSelected = event.currentTarget.checked;
                            const previousSelected = selected;
                            setLocalSelectedByItemId((current) => ({
                              ...current,
                              [item.itemId]: nextSelected,
                            }));
                            void Promise.resolve(
                              onToggleItem?.(item, nextSelected),
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
                          className="requirement-review-item__box"
                        />
                        <span>{item.text}</span>
                        <em>
                          {updating ? "更新中" : selected ? "已选择" : "未选择"}
                        </em>
                      </label>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      ) : null}

      {requirementDraft ? (
        <div className="requirement-review-panel__other">
          <FieldTextarea
            disabled={isConfirming}
            label={requirementDraft.otherInputPrompt}
            onChange={(event) => setOtherText(event.currentTarget.value)}
            placeholder="请输入"
            rows={2}
            value={otherText}
          />
        </div>
      ) : null}

      <div className="requirement-review-panel__actions">
        {canConfirm ? (
          <Button
            loading={isConfirming}
            onClick={() => void handleConfirm()}
            tone="primary"
          >
            确认需求
          </Button>
        ) : null}
      </div>
    </section>
  );
}
