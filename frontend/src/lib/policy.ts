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
  created_at: number;
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
  created_at: number;
  [key: string]: unknown;
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
    created_at: typeof raw.created_at === "number" ? raw.created_at : 0,
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
    created_at: typeof raw.created_at === "number" ? raw.created_at : 0,
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
