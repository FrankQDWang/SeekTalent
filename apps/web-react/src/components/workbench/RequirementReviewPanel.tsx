import { ClipboardCheck } from "lucide-react";
import type {
  AgentWorkbenchPendingActions,
  AgentWorkbenchRequirementDraft,
} from "../../lib/api/agentWorkbenchTypes";
import { Button } from "../primitives/Button";
import "./RequirementReviewPanel.css";

type RequirementReviewPanelProps = {
  confirming?: boolean;
  onConfirm?: (() => void) | undefined;
  pendingActions: AgentWorkbenchPendingActions;
  requirementDraft: AgentWorkbenchRequirementDraft | null | undefined;
};

export function RequirementReviewPanel({
  confirming = false,
  onConfirm,
  pendingActions,
  requirementDraft,
}: RequirementReviewPanelProps) {
  if (!requirementDraft && !pendingActions.primary) {
    return null;
  }

  return (
    <section className="requirement-review-panel" aria-label="需求确认">
      <div>
        <ClipboardCheck aria-hidden="true" size={18} />
        <span>
          <strong>{requirementDraft?.title ?? "待处理动作"}</strong>
          <em>{requirementDraft?.summary ?? pendingActions.primary}</em>
        </span>
      </div>
      {pendingActions.allowed.includes("confirm_requirements") ? (
        <Button loading={confirming} onClick={onConfirm} tone="primary">
          确认需求
        </Button>
      ) : null}
    </section>
  );
}
