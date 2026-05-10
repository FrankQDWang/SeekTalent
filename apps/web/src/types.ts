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

export type StartWorkbenchSourceRunInput = {
  sourceKind: SourceKind;
  idempotencyKey?: string;
};

export type WorkbenchSourceRunStartResponse = {
  sessionId: string;
  sourceRunId: string;
  sourceKind: SourceKind;
  status: WorkbenchSourceStatus;
  job: WorkbenchSourceRunJob;
};

export type WorkbenchEvent = {
  globalSeq: number;
  sessionSeq: number | null;
  sessionId: string | null;
  sourceRunId: string | null;
  sourceKind: SourceKind | null;
  eventName: string;
  payload: Record<string, unknown>;
  createdAt: string;
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
