export type WorkbenchUser = {
  userId: string;
  email: string;
  displayName: string;
  role: 'admin' | 'member';
  workspaceId: string;
};

export type WorkbenchWorkspace = {
  id: string;
  name: string;
};

export type BootstrapResponse = {
  user: WorkbenchUser;
  workspace: WorkbenchWorkspace;
};

export type MeResponse = {
  user: WorkbenchUser;
};

export type SourceKind = 'cts' | 'liepin';
export type WorkbenchSourceStatus = 'queued' | 'blocked' | 'running' | 'completed' | 'failed';
export type WorkbenchAuthState = 'not_required' | 'login_required';
export type WorkbenchTriageStatus = 'draft' | 'approved';
export type WorkbenchJobStatus = 'queued' | 'running' | 'completed' | 'failed';
export type WorkbenchCandidateReviewStatus = 'new' | 'promising' | 'rejected';
export type WorkbenchCandidateEvidenceLevel = 'card' | 'detail' | 'final';
export type WorkbenchDetailOpenMode = 'human_confirm' | 'bypass_confirm';
export type WorkbenchDetailOpenRequestStatus = 'pending' | 'approved' | 'rejected' | 'bypassed' | 'blocked' | 'expired';
export type WorkbenchDetailOpenLedgerStatus =
  | 'planned'
  | 'leased'
  | 'opened'
  | 'skipped'
  | 'blocked'
  | 'failed'
  | 'maybe_used';
export type WorkbenchProviderActionBudgetImpact = 'none' | 'reserved';
export type WorkbenchSourceConnectionStatus =
  | 'login_required'
  | 'login_in_progress'
  | 'verification_required'
  | 'connected'
  | 'expired'
  | 'blocked'
  | 'disconnected';

export type WorkbenchRequirementTriage = {
  sessionId: string;
  status: WorkbenchTriageStatus;
  mustHaves: string[];
  niceToHaves: string[];
  synonyms: string[];
  seniorityFilters: string[];
  exclusions: string[];
  generatedQueryHints: string[];
  createdAt: string;
  updatedAt: string;
  approvedAt: string | null;
};

export type WorkbenchRequirementTriageInput = {
  mustHaves: string[];
  niceToHaves: string[];
  synonyms: string[];
  seniorityFilters: string[];
  exclusions: string[];
  generatedQueryHints: string[];
};

export type WorkbenchSourceRun = {
  sourceRunId: string;
  sourceKind: SourceKind;
  status: WorkbenchSourceStatus;
  authState: WorkbenchAuthState;
  cardsScannedCount: number;
  uniqueCandidatesCount: number;
  detailOpenUsedCount: number;
  detailOpenBlockedCount: number;
  warningCode: string | null;
  warningMessage: string | null;
};

export type WorkbenchSourceCard = WorkbenchSourceRun & {
  label: string;
  connectionId?: string | null;
  connectionStatus?: WorkbenchSourceConnectionStatus | null;
  connectionWarningCode?: string | null;
  connectionWarningMessage?: string | null;
};

export type WorkbenchSession = {
  sessionId: string;
  workspaceId: string;
  ownerUserId: string;
  jobTitle: string;
  jdText: string;
  notes: string;
  status: 'draft';
  requirementTriage: WorkbenchRequirementTriage;
  sourceRuns: WorkbenchSourceRun[];
  sourceCards: WorkbenchSourceCard[];
};

export type WorkbenchSessionListResponse = {
  sessions: WorkbenchSession[];
};

export type CreateWorkbenchSessionInput = {
  jobTitle: string;
  jdText: string;
  notes: string;
  sourceKinds?: SourceKind[];
};

export type WorkbenchSettingsSource = {
  sourceKind: SourceKind;
  label: string;
  enabled: boolean;
  authRequired: boolean;
};

export type WorkbenchSettingsResponse = {
  workspaceId: string;
  sources: WorkbenchSettingsSource[];
};

export type WorkbenchSourceConnection = {
  connectionId: string;
  sourceKind: SourceKind;
  label: string;
  status: WorkbenchSourceConnectionStatus;
  warningCode: string | null;
  warningMessage: string | null;
  createdAt: string;
  updatedAt: string;
  connectedAt: string | null;
};

export type WorkbenchSourceConnectionListResponse = {
  connections: WorkbenchSourceConnection[];
};

export type WorkbenchLiepinLoginHandoffResponse = {
  connectionId: string;
  sourceKind: 'liepin';
  status: WorkbenchSourceConnectionStatus;
  handoffMode: 'server_managed_browser';
  handoffState: 'login_in_progress' | 'relay_pending_worker' | 'safe_frame_available';
  safeFrameUrl: string | null;
  warningCode: string | null;
  warningMessage: string | null;
};

export type WorkbenchSourceRunJob = {
  jobId: string;
  sourceRunId: string;
  status: WorkbenchJobStatus;
  attemptCount: number;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
};

export type WorkbenchSourceRunStartResponse = {
  sessionId: string;
  sourceRunId: string;
  sourceKind: SourceKind;
  status: WorkbenchSourceStatus;
  job: WorkbenchSourceRunJob;
};

export type WorkbenchSessionStartBlockedSource = {
  sourceRunId: string;
  sourceKind: SourceKind;
  reason: string;
};

export type WorkbenchSessionStartResponse = {
  sessionId: string;
  sourceRuns: WorkbenchSourceRunStartResponse[];
  blockedSources: WorkbenchSessionStartBlockedSource[];
};

export type WorkbenchSourceRunPolicy = {
  sessionId: string;
  sourceKind: 'liepin';
  detailOpenMode: WorkbenchDetailOpenMode;
  updatedAt: string;
};

export type WorkbenchDetailOpenLedger = {
  ledgerId: string;
  status: WorkbenchDetailOpenLedgerStatus;
  budgetDay: string;
  leaseExpiresAt: string | null;
};

export type WorkbenchProviderAction = {
  actionKind: 'managed_browser';
  sourceKind: 'liepin';
  connectionId: string;
  reviewItemId: string;
  budgetImpact: WorkbenchProviderActionBudgetImpact;
  message: string;
};

export type WorkbenchDetailOpenCandidateSnapshot = {
  reviewItemId: string;
  displayName: string;
  title: string;
  company: string;
  location: string;
  summary: string;
  aggregateScore: number | null;
  evidenceLevel: WorkbenchCandidateEvidenceLevel;
  sourceBadges: string[];
  matchedMustHaves: string[];
  matchedPreferences: string[];
  missingRisks: string[];
};

export type WorkbenchDetailOpenRequest = {
  requestId: string;
  sessionId: string;
  reviewItemId: string;
  status: WorkbenchDetailOpenRequestStatus;
  detailOpenMode: WorkbenchDetailOpenMode;
  decisionNote: string | null;
  candidate: WorkbenchDetailOpenCandidateSnapshot | null;
  blockedReason: string | null;
  ledger: WorkbenchDetailOpenLedger | null;
  providerAction: WorkbenchProviderAction | null;
  createdAt: string;
  updatedAt: string;
};

export type WorkbenchDetailOpenRequestListResponse = {
  requests: WorkbenchDetailOpenRequest[];
};

export type WorkbenchEvent = {
  globalSeq: number;
  sessionSeq: number | null;
  sessionId: string | null;
  sourceRunId: string | null;
  sourceKind: SourceKind | null;
  eventName: string;
  schemaVersion?: string;
  idempotencyKey?: string | null;
  payload: Record<string, unknown>;
  occurredAt?: string;
  createdAt: string;
};

export type WorkbenchNotePayload = {
  text?: string;
  eventSeq?: number;
  event_seq?: number;
  globalSeq?: number;
  global_seq?: number;
  noteKind?: string;
  note_kind?: string;
  statusHint?: string | null;
  status_hint?: string | null;
};

export type WorkbenchEventListResponse = {
  events: WorkbenchEvent[];
};

export type WorkbenchCandidateEvidence = {
  evidenceId: string;
  sourceRunId: string;
  sourceKind: SourceKind;
  evidenceLevel: WorkbenchCandidateEvidenceLevel;
  score: number | null;
  fitBucket: string | null;
  matchedMustHaves: string[];
  matchedPreferences: string[];
  missingRisks: string[];
  strengths: string[];
  weaknesses: string[];
  createdAt: string;
};

export type WorkbenchCandidateReviewItem = {
  reviewItemId: string;
  sessionId: string;
  status: WorkbenchCandidateReviewStatus;
  note: string;
  displayName: string;
  title: string;
  company: string;
  location: string;
  summary: string;
  aggregateScore: number | null;
  fitBucket: string | null;
  sourceBadges: string[];
  evidenceLevel: WorkbenchCandidateEvidenceLevel;
  matchedMustHaves: string[];
  matchedPreferences: string[];
  missingRisks: string[];
  strengths: string[];
  weaknesses: string[];
  evidence: WorkbenchCandidateEvidence[];
  createdAt: string;
  updatedAt: string;
};

export type WorkbenchCandidateReviewQueueResponse = {
  items: WorkbenchCandidateReviewItem[];
};

export type WorkbenchCandidateReviewItemUpdateInput = {
  status?: WorkbenchCandidateReviewStatus;
  note?: string;
};

export type WorkbenchGraphNodeKind = 'recall' | 'scoring' | 'final' | 'liepin_card' | 'detail_approval';
export type WorkbenchGraphRelationshipKind =
  | 'recalled'
  | 'new'
  | 'scored'
  | 'fit'
  | 'not_fit'
  | 'final'
  | 'detail_requested';
export type WorkbenchGraphCandidateRecoveryState = 'ready' | 'recoverable_empty';
export type WorkbenchResumeSnapshotStatus =
  | 'ready'
  | 'snapshot_forbidden'
  | 'snapshot_not_found';

export type WorkbenchGraphCandidateNodeScope = {
  sessionId: string;
  source: SourceKind | 'all';
  roundId: string | null;
  nodeKind: WorkbenchGraphNodeKind;
};

export type WorkbenchGraphCandidateCoverage = {
  sourceResultIdsSeen: string[];
  missingSafeIdentityCount: number;
  missingSnapshotCount: number;
  forbiddenSnapshotCount: number;
  droppedRows: number;
};

export type WorkbenchGraphCandidateSummary = {
  graphCandidateId: string;
  sourceKind: SourceKind;
  sourceRunId: string;
  nodeKind: WorkbenchGraphNodeKind;
  roundNo: number | null;
  laneType: string | null;
  queryRole: string | null;
  relationshipKind: WorkbenchGraphRelationshipKind;
  displayName: string;
  title: string;
  company: string;
  location: string;
  sourceBadges: string[];
  score: number | null;
  fitBucket: string | null;
  summary: string;
  matchedMustHaves: string[];
  strengths: string[];
  missingRisks: string[];
  reviewItemId: string | null;
  evidenceLevel: WorkbenchCandidateEvidenceLevel | null;
  detailOpenRequestId: string | null;
  canExpandResume: boolean;
  canMarkPromising: boolean;
  canReject: boolean;
  canSaveNote: boolean;
  canRequestDetail: boolean;
  canOpenProvider: boolean;
};

export type WorkbenchGraphCandidateListResponse = {
  nodeId: string;
  nodeScope: WorkbenchGraphCandidateNodeScope;
  items: WorkbenchGraphCandidateSummary[];
  nextCursor: string | null;
  totalSourceResults: number;
  totalGraphCandidates: number;
  totalEstimate: number | null;
  coverage: WorkbenchGraphCandidateCoverage;
  truncated: boolean;
  generatedAt: string;
  recoveryState: WorkbenchGraphCandidateRecoveryState;
  recoveryReason: string | null;
};

export type WorkbenchResumeSnapshotProfile = {
  displayName: string;
  headline: string;
  company: string;
  location: string;
  summary: string;
};

export type WorkbenchResumeSnapshotWorkExperience = {
  company: string;
  title: string;
  duration: string | null;
  summary: string | null;
};

export type WorkbenchResumeSnapshotEducation = {
  school: string;
  degree: string | null;
  major: string | null;
};

export type WorkbenchResumeSnapshotProject = {
  name: string;
  summary: string | null;
};

export type WorkbenchResumeSnapshotSourceEvidence = {
  label: string;
  text: string;
};

export type WorkbenchGraphCandidateResumeSnapshot = {
  graphCandidateId: string;
  status: WorkbenchResumeSnapshotStatus;
  reason: string | null;
  profile: WorkbenchResumeSnapshotProfile | null;
  workExperience: WorkbenchResumeSnapshotWorkExperience[];
  education: WorkbenchResumeSnapshotEducation[];
  projects: WorkbenchResumeSnapshotProject[];
  skills: string[];
  sourceEvidence: WorkbenchResumeSnapshotSourceEvidence[];
};
