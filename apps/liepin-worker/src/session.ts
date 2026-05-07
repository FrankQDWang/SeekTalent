export const LIEPIN_SESSION_STATUSES = [
  "logged_out",
  "ready",
  "needs_user_action",
  "risk_control_wait",
  "temporarily_rate_limited",
  "failed",
] as const;

export type ManagedLoginStatus = (typeof LIEPIN_SESSION_STATUSES)[number];

export type LoginHandoffRequest = {
  connectionId: string;
  handoffToken: string;
  expiresAt: Date;
};

export type LoginHandoff = {
  connection_id: string;
  handoff_token: string;
  browser_view_url: null;
  expires_at: string;
  status_event_stream: string;
};

export function createLoginHandoff(request: LoginHandoffRequest): LoginHandoff {
  return {
    connection_id: request.connectionId,
    handoff_token: request.handoffToken,
    browser_view_url: null,
    expires_at: formatUtcZ(request.expiresAt),
    status_event_stream: `/api/liepin/connections/${request.connectionId}/events`,
  };
}

function formatUtcZ(value: Date): string {
  return value.toISOString().replace(".000Z", "Z");
}
