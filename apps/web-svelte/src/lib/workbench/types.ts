import type { components } from '../api/schema';

export type BootstrapAdminInput = components['schemas']['WorkbenchBootstrapRequest'];
export type BootstrapResponse = components['schemas']['WorkbenchBootstrapResponse'];
export type LoginInput = components['schemas']['WorkbenchLoginRequest'];
export type MeResponse = components['schemas']['WorkbenchMeResponse'];
export type WorkbenchUser = components['schemas']['WorkbenchUserResponse'];
export type WorkbenchWorkspace = components['schemas']['WorkbenchWorkspaceResponse'];
export type WorkbenchSession = components['schemas']['WorkbenchSessionResponse'];
export type WorkbenchSessionCreateInput = components['schemas']['WorkbenchSessionCreateRequest'];
export type WorkbenchSessionListResponse = components['schemas']['WorkbenchSessionListResponse'];
export type WorkbenchDevModeStatus = components['schemas']['WorkbenchDevModeStatusResponse'];
export type WorkbenchDevModeComponent = components['schemas']['WorkbenchDevModeComponentResponse'];
export type RequirementSheet = components['schemas']['RequirementSheet-Output'];
export type RequirementSheetInput = components['schemas']['RequirementSheet-Input'];
export type HardConstraintSlots = components['schemas']['HardConstraintSlots'];
export type PreferenceSlots = components['schemas']['PreferenceSlots'];
export type QueryTermCandidate = components['schemas']['QueryTermCandidate'];
export type WorkbenchRequirementReview =
	components['schemas']['WorkbenchRequirementReviewResponse'];
export type WorkbenchRequirementReviewInput =
	components['schemas']['WorkbenchRequirementReviewUpdateRequest'];
export type WorkbenchCandidateReviewItem =
	components['schemas']['WorkbenchCandidateReviewItemResponse'];
export type WorkbenchCandidateReviewItemUpdateInput =
	components['schemas']['WorkbenchCandidateReviewItemUpdateRequest'];
export type WorkbenchCandidateReviewQueueResponse =
	components['schemas']['WorkbenchCandidateReviewQueueResponse'];
export type WorkbenchFinalTopCandidate =
	components['schemas']['WorkbenchFinalTopCandidateResponse'];
export type WorkbenchFinalTopCandidateListResponse =
	components['schemas']['WorkbenchFinalTopCandidateListResponse'];
export type WorkbenchEvent = components['schemas']['WorkbenchEventResponse'];
export type WorkbenchEventListResponse = components['schemas']['WorkbenchEventListResponse'];
export type WorkbenchSourceRunPolicy = components['schemas']['WorkbenchSourceRunPolicyResponse'];
export type WorkbenchSourceRunPolicyUpdateInput =
	components['schemas']['WorkbenchSourceRunPolicyUpdateRequest'];
export type WorkbenchGraphCandidateListResponse =
	components['schemas']['WorkbenchGraphCandidateListResponse'];
export type WorkbenchGraphCandidateSummary =
	components['schemas']['WorkbenchGraphCandidateSummaryResponse'];
export type WorkbenchGraphCandidateResumeSnapshot =
	components['schemas']['WorkbenchGraphCandidateResumeSnapshotResponse'];
export type WorkbenchRuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];
export type WorkbenchDetailOpenRequest =
	components['schemas']['WorkbenchDetailOpenRequestResponse'];
export type WorkbenchDetailOpenRequestListResponse =
	components['schemas']['WorkbenchDetailOpenRequestListResponse'];
export type WorkbenchProviderAction = components['schemas']['WorkbenchProviderActionResponse'];
export type WorkbenchSourceConnection = components['schemas']['WorkbenchSourceConnectionResponse'];
export type WorkbenchSourceConnectionListResponse =
	components['schemas']['WorkbenchSourceConnectionListResponse'];
