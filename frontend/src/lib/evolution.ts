import { get, send, asList, pick } from "./http";

// Bounded evolution API client — real gateway surface only.
// Router: backend/app/gateway/routers/evolution.py (prefix /api/evolution).

/** One persisted ledger event as recorded by alpha.evolution.engine (event/candidate_id/at/reason...). */
export interface EvolutionLedgerEvent {
  event: string;
  candidate_id?: string;
  at?: number;
  reason?: string;
  [key: string]: unknown;
}

/** GET /api/evolution/ledger — {"events": [...]} (limit clamped to 500 by the router). */
export async function getEvolutionLedger(limit = 100): Promise<EvolutionLedgerEvent[]> {
  const bounded = Math.max(1, Math.min(Math.floor(limit), 500));
  const d = await get<unknown>(`/evolution/ledger?limit=${bounded}`);
  return asList(d, ["events", "data"]).map((raw) => ({
    ...raw,
    event: String(pick(raw, ["event", "type"], "event")),
  }));
}

/** Runtime identity record from alpha.evolution.identity.get_runtime_identity(). */
export interface EvolutionIdentity {
  agentId: string;
  alphaVersion: string;
  gitCommit: string;
  gitCommitSource: string;
  gitCommitNote: string;
  os: string;
  architecture: string;
  runtime: string;
  releaseChannel: string;
  updateState: string;
  capabilities: string[];
  repository?: Record<string, unknown>;
  identityVersion?: number | null;
  createdAt?: string | null;
  [key: string]: unknown;
}

/** GET /api/evolution/identity — canonical repository, version, commit, capabilities. */
export async function getEvolutionIdentity(): Promise<EvolutionIdentity> {
  const d = await get<Record<string, unknown>>("/evolution/identity");
  const raw = d ?? {};
  return {
    ...raw,
    agentId: String(pick(raw, ["agentId"], "unknown")),
    alphaVersion: String(pick(raw, ["alphaVersion"], "unknown")),
    gitCommit: String(pick(raw, ["gitCommit"], "unknown")),
    gitCommitSource: String(pick(raw, ["gitCommitSource"], "unknown")),
    gitCommitNote: String(pick(raw, ["gitCommitNote"], "")),
    os: String(pick(raw, ["os"], "unknown")),
    architecture: String(pick(raw, ["architecture"], "unknown")),
    runtime: String(pick(raw, ["runtime"], "unknown")),
    releaseChannel: String(pick(raw, ["releaseChannel"], "unknown")),
    updateState: String(pick(raw, ["updateState"], "unknown")),
    capabilities: asList(raw.capabilities, ["capabilities"]).map((c) => String(c)),
  };
}

/** Persisted update state from alpha.evolution.release_check.load_update_state(). */
export interface EvolutionUpdateState {
  /** IDLE | CHECKING | UPDATE_AVAILABLE | UP_TO_DATE | CHECK_FAILED | RECOVERY_REQUIRED (or "unknown"). */
  state: string;
  checkedAt: string | null;
  installedVersion: string | null;
  latestTag: string | null;
  error: string | null;
  [key: string]: unknown;
}

function toUpdateState(raw: Record<string, unknown>): EvolutionUpdateState {
  return {
    ...raw,
    state: String(pick(raw, ["state"], "unknown")),
    checkedAt: typeof raw.checkedAt === "string" ? raw.checkedAt : null,
    installedVersion: typeof raw.installedVersion === "string" ? raw.installedVersion : null,
    latestTag: typeof raw.latestTag === "string" ? raw.latestTag : null,
    error: typeof raw.error === "string" ? raw.error : null,
  };
}

/** GET /api/evolution/update-state — persisted state only, no network access. */
export async function getEvolutionUpdateState(): Promise<EvolutionUpdateState> {
  const d = await get<Record<string, unknown>>("/evolution/update-state");
  return toUpdateState(d ?? {});
}

/** POST /api/evolution/update-check — runs a GitHub latest-release check (failures answer in-body). */
export async function checkForEvolutionUpdate(): Promise<EvolutionUpdateState> {
  const d = await send<Record<string, unknown>>("/evolution/update-check", "POST", {});
  return toUpdateState(d ?? {});
}

/**
 * Queue the admin-only detached source transaction. The server ignores any
 * client-supplied URL/ref; it applies only its verified persisted candidate.
 * ``force`` confirms an attended manual handoff only and never bypasses the
 * server's safety verdict.
 */
export async function requestEvolutionUpdate(force = false): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/evolution/update-apply", "POST", { force });
}

/** POST /api/evolution/candidates — propose an evolvable-surface candidate (201). */
export async function proposeEvolutionCandidate(input: {
  surface: string;
  target: string;
  payload?: Record<string, unknown>;
  parent_id?: string;
}): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/evolution/candidates", "POST", {
    surface: input.surface,
    target: input.target,
    payload: input.payload ?? {},
    parent_id: input.parent_id ?? null,
  });
}

/** POST /api/evolution/candidates/{id}/benchmark — record a benchmark report on the candidate. */
export async function recordEvolutionBenchmark(
  candidateId: string,
  benchmark: Record<string, unknown>
): Promise<Record<string, unknown> | null> {
  const d = await send<Record<string, unknown> | null>(
    `/evolution/candidates/${encodeURIComponent(candidateId)}/benchmark`,
    "POST",
    { benchmark }
  );
  return d ?? null;
}

/** POST /api/evolution/candidates/{id}/gate — {promoted, reason}; omission never grants autonomy. */
export async function gateEvolutionCandidate(
  candidateId: string,
  input: { baseline?: Record<string, unknown>; human_approved?: boolean; autonomous_mode?: boolean } = {}
): Promise<{ promoted: boolean; reason: string }> {
  const d = await send<Record<string, unknown>>(
    `/evolution/candidates/${encodeURIComponent(candidateId)}/gate`,
    "POST",
    {
      baseline: input.baseline ?? {},
      human_approved: input.human_approved ?? false,
      autonomous_mode: input.autonomous_mode ?? false,
    }
  );
  return {
    promoted: Boolean(pick(d, ["promoted"], false)),
    reason: String(pick(d, ["reason"], "")),
  };
}

/** POST /api/evolution/candidates/{id}/rollback — {rolled_back}. */
export async function rollbackEvolutionCandidate(
  candidateId: string,
  reason = ""
): Promise<boolean> {
  const query = reason ? `?reason=${encodeURIComponent(reason)}` : "";
  const d = await send<Record<string, unknown>>(
    `/evolution/candidates/${encodeURIComponent(candidateId)}/rollback${query}`,
    "POST",
    {}
  );
  return Boolean(pick(d, ["rolled_back"], false));
}
