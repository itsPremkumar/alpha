import { get, send, asList, pick } from "./http";

// Policy engine API client — real gateway surface only.
// Router: backend/app/gateway/routers/policy.py (prefix /api/policy).

/** ApprovalPolicy.to_dict() — an operator-added policy row. */
export interface PolicyRule {
  policy_id: string;
  action_pattern: string;
  actor: string;
  project_id: string;
  /** "allow" | "deny" | "approval" (label comes from the API, never hardcoded here). */
  auto: string;
  note: string;
  /** ISO 8601, or `null` when the Gateway did not report one. Never an epoch number. */
  created_at: string | null;
  [key: string]: unknown;
}

/** PolicyDecision.to_dict() — verdict/reason/matched_rule as the engine decided. */
export interface PolicyDecision {
  verdict: string;
  reason: string;
  matched_rule: string | null;
  [key: string]: unknown;
}

/** ApprovalRequest.to_dict() — a pending/decided human approval row. */
export interface ApprovalRequest {
  request_id: string;
  action: string;
  actor: string;
  project_id: string;
  reason: string;
  /** "pending" | "approved" | "rejected". */
  status: string;
  decided_by: string | null;
  /** ISO 8601, or `null` when the Gateway did not report one. Never an epoch number. */
  created_at: string | null;
  [key: string]: unknown;
}

/**
 * Normalise a policy/approval `created_at` to an ISO string, or `null`.
 *
 * The Gateway used to put a raw float epoch on this wire and now sends ISO
 * 8601 (`alpha.utils.time.coerce_iso` is the repository-wide convention). Both
 * shapes are accepted so a mixed-version deployment degrades to a correct date
 * rather than to `1970` — but an absent or unrecognised value is `null`, never
 * `0`. Coercing "not a number" to `0` is the exact defect this function exists
 * to stop: `0` is a valid epoch, so it renders as 1 January 1970 and reads as a
 * measurement the server never made.
 */
function toIsoOrNull(raw: unknown): string | null {
  if (typeof raw === "string" && raw.length > 0) return raw;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const ms = raw > 1e11 ? raw : raw * 1000; // seconds vs milliseconds
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }
  return null;
}

function toPolicyRule(raw: Record<string, unknown>): PolicyRule {
  return {
    ...raw,
    policy_id: String(pick(raw, ["policy_id", "id"], "")),
    action_pattern: String(pick(raw, ["action_pattern", "pattern"], "")),
    actor: String(pick(raw, ["actor"], "*")),
    project_id: String(pick(raw, ["project_id"], "*")),
    auto: String(pick(raw, ["auto", "verdict"], "allow")),
    note: String(pick(raw, ["note"], "")),
    created_at: toIsoOrNull(raw.created_at),
  };
}

function toApproval(raw: Record<string, unknown>): ApprovalRequest {
  return {
    ...raw,
    request_id: String(pick(raw, ["request_id", "id"], "")),
    action: String(pick(raw, ["action"], "")),
    actor: String(pick(raw, ["actor"], "")),
    project_id: String(pick(raw, ["project_id"], "*")),
    reason: String(pick(raw, ["reason"], "")),
    status: String(pick(raw, ["status"], "pending")),
    decided_by: typeof raw.decided_by === "string" ? raw.decided_by : null,
    created_at: toIsoOrNull(raw.created_at),
  };
}

/** GET /api/policy/policies — operator policies (base rules are evaluated server-side). */
export async function listPolicies(): Promise<PolicyRule[]> {
  const d = await get<unknown>("/policy/policies");
  return asList(d, ["policies", "data"]).map(toPolicyRule);
}

/** POST /api/policy/evaluate — evaluate one action; labels come straight from the engine. */
export async function evaluateAction(
  action: string,
  actor = "*",
  projectId = "*"
): Promise<PolicyDecision> {
  const d = await send<Record<string, unknown>>("/policy/evaluate", "POST", {
    action,
    actor,
    project_id: projectId,
  });
  const raw = d ?? {};
  return {
    ...raw,
    verdict: String(pick(raw, ["verdict"], "approval")),
    reason: String(pick(raw, ["reason"], "")),
    matched_rule: typeof raw.matched_rule === "string" ? raw.matched_rule : null,
  };
}

/** POST /api/policy/policies — add an operator policy (auto: allow|deny|approval). */
export async function addPolicy(input: {
  action_pattern: string;
  actor?: string;
  project_id?: string;
  auto?: "allow" | "deny" | "approval";
  note?: string;
}): Promise<PolicyRule> {
  const d = await send<Record<string, unknown>>("/policy/policies", "POST", {
    action_pattern: input.action_pattern,
    actor: input.actor ?? "*",
    project_id: input.project_id ?? "*",
    auto: input.auto ?? "allow",
    note: input.note ?? "",
  });
  return toPolicyRule(d ?? {});
}

/** DELETE /api/policy/policies/{policy_id} — {removed: boolean}. */
export async function removePolicy(policyId: string): Promise<boolean> {
  const d = await send<Record<string, unknown>>(
    `/policy/policies/${encodeURIComponent(policyId)}`,
    "DELETE"
  );
  return Boolean(pick(d, ["removed"], false));
}

/** GET /api/policy/approvals — {approvals, count}; optional status filter. */
export async function listApprovals(status?: string): Promise<ApprovalRequest[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const d = await get<unknown>(`/policy/approvals${query}`);
  return asList(d, ["approvals", "data"]).map(toApproval);
}

/** POST /api/policy/approvals — create an approval request row. */
export async function createApproval(input: {
  action: string;
  actor?: string;
  project_id?: string;
  reason?: string;
}): Promise<ApprovalRequest> {
  const d = await send<Record<string, unknown>>("/policy/approvals", "POST", {
    action: input.action,
    actor: input.actor ?? "agent",
    project_id: input.project_id ?? "*",
    reason: input.reason ?? "",
  });
  return toApproval(d ?? {});
}

/** POST /api/policy/approvals/{request_id}/decide — approve or reject a pending request. */
export async function decideApproval(
  requestId: string,
  approved: boolean,
  decidedBy = "operator"
): Promise<ApprovalRequest> {
  const d = await send<Record<string, unknown>>(
    `/policy/approvals/${encodeURIComponent(requestId)}/decide`,
    "POST",
    { approved, decided_by: decidedBy }
  );
  return toApproval(d ?? {});
}
