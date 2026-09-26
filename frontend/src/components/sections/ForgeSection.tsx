"use client";

import React, { useEffect, useState } from "react";
import {
  listWorkshopProposals, distillSkillDraft, publishSkillDraft, proposeSkillEvolution,
  evaluateSkillEvolution, promoteSkillEvolution, rollbackSkillEvolution,
} from "@/lib/workshop";
import type { SkillEvolutionProposal } from "@/lib/workshop";
import {
  getEvolutionLedger, getEvolutionIdentity, getEvolutionUpdateState, checkForEvolutionUpdate,
  proposeEvolutionCandidate, recordEvolutionBenchmark, gateEvolutionCandidate, rollbackEvolutionCandidate,
} from "@/lib/evolution";
import type { EvolutionLedgerEvent, EvolutionIdentity, EvolutionUpdateState } from "@/lib/evolution";
import {
  listPolicies, evaluateAction, addPolicy, removePolicy,
  listApprovals, createApproval, decideApproval,
} from "@/lib/policy";
import type { PolicyRule, PolicyDecision, ApprovalRequest } from "@/lib/policy";
import { listBenchmarkSuites, listBenchmarkResults, runBenchmarkSuite } from "@/lib/benchmarks";
import type { BenchmarkSuite, BenchmarkCaseResult, BenchmarkRunReport } from "@/lib/benchmarks";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Play, Plus, Trash2, Check, X, RotateCcw, FlaskConical, ShieldCheck, GitBranch, Hammer } from "lucide-react";

type Tone = "green" | "amber" | "gray" | "blue" | "red" | "purple" | "cyan" | "indigo";

const STATUS_TONES: Record<string, Tone> = {
  // Skill evolution proposal statuses (backend skills_workshop router/engine)
  proposed: "gray", evaluated: "blue", moderated: "blue", validated: "green",
  promoted: "green", rejected: "red", rolled_back: "amber",
  // Policy verdicts / approval statuses (alpha/policy/engine.py)
  allow: "green", deny: "red", approval: "amber",
  pending: "amber", approved: "green",
  // Update states (alpha/evolution/release_check.py)
  CHECKING: "blue", UPDATE_AVAILABLE: "green", UP_TO_DATE: "blue", CHECK_FAILED: "red", IDLE: "gray",
};

function statusTone(status: string): Tone {
  return STATUS_TONES[status] ?? "gray";
}

function fmtEpoch(sec: number | null | undefined): string {
  if (!sec || !Number.isFinite(sec) || sec <= 0) return "—";
  return new Date(sec * 1000).toLocaleString();
}

/** Parse user-entered JSON honestly; throws with the real parse error. */
function parseJson(text: string, what: string): unknown {
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(`${what} must be valid JSON — ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** Compact, verbatim-ish rendering of an API answer for a flash notice. */
function summarize(value: unknown): string {
  let text: string;
  try {
    text = typeof value === "string" ? value : (JSON.stringify(value) ?? String(value));
  } catch {
    text = String(value);
  }
  return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

function strField(record: unknown, key: string): string | null {
  if (record && typeof record === "object") {
    const v = (record as Record<string, unknown>)[key];
    if (typeof v === "string" && v !== "") return v;
  }
  return null;
}

/**
 * Renders the API's own epistemic disclosures — `basis` and `evidence_kind` —
 * whenever the payload carries them. Nothing is invented here: if the record
 * has no such field, no chip is shown (and we never claim one either way).
 */
function BasisChips(props: { record?: unknown }) {
  const basis = strField(props.record, "basis");
  const evidenceKind = strField(props.record, "evidence_kind");
  if (!basis && !evidenceKind) return null;
  const kindTone: Tone =
    evidenceKind === "measured" || evidenceKind === "real" ? "green" : evidenceKind === "failed" ? "red" : "amber";
  return (
    <span className="inline-flex items-center gap-1 flex-wrap">
      {basis && <Badge tone="amber">basis: {basis}</Badge>}
      {evidenceKind && <Badge tone={kindTone}>evidence_kind: {evidenceKind}</Badge>}
    </span>
  );
}

function SubTabs(props: { value: string; onChange: (v: string) => void; tabs: Array<{ id: string; label: string }> }) {
  return (
    <div className="flex gap-1 rounded-xl bg-muted/60 p-1 w-fit flex-wrap">
      {props.tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => props.onChange(t.id)}
          className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${props.value === t.id ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

type PanelId = "workshop" | "evolution" | "policy" | "benchmarks";

export function ForgeSection() {
  const [panel, setPanel] = useState<PanelId>("workshop");
  const [notice, setNotice] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 8000);
  };

  return (
    <Section
      title="Skill Forge"
      hint="Skill synthesis workshop, bounded evolution, approval policy and the benchmark plane — every value below is read live from the gateway APIs. Empty and unverified states are shown as such; API-provided `basis` / `evidence_kind` disclosures are surfaced when present."
      actions={
        <Btn variant="ghost" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      <SubTabs
        value={panel}
        onChange={(v) => setPanel(v as PanelId)}
        tabs={[
          { id: "workshop", label: "Skill Workshop" },
          { id: "evolution", label: "Evolution" },
          { id: "policy", label: "Policy" },
          { id: "benchmarks", label: "Benchmarks" },
        ]}
      />
      {notice && <Notice message={notice} />}
      {panel === "workshop" && <WorkshopPanel refreshKey={refreshKey} onNotice={flash} />}
      {panel === "evolution" && <EvolutionPanel refreshKey={refreshKey} onNotice={flash} />}
      {panel === "policy" && <PolicyPanel refreshKey={refreshKey} onNotice={flash} />}
      {panel === "benchmarks" && <BenchmarksPanel refreshKey={refreshKey} onNotice={flash} />}
    </Section>
  );
}

/* ───────────────────────────── Skill Workshop ───────────────────────────── */

function WorkshopPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [rows, setRows] = useState<SkillEvolutionProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [dName, setDName] = useState("");
  const [dDesc, setDDesc] = useState("");
  const [dSteps, setDSteps] = useState("[]");
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [pMarkdown, setPMarkdown] = useState("");
  const [propSkill, setPropSkill] = useState("");
  const [propCandidate, setPropCandidate] = useState("");
  const [propEvidence, setPropEvidence] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listWorkshopProposals());
    } catch (e) {
      // A failed fetch must not render as an empty workshop.
      setRows([]);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const runAction = async (id: string, label: string, fn: () => Promise<unknown>) => {
    setBusyId(id);
    setActionError(null);
    try {
      const out = await fn();
      props.onNotice(`${label} answered: ${summarize(out)}`);
      await load();
    } catch (e) {
      setActionError(`${label} failed — ${errMsg(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const onEvaluate = (p: SkillEvolutionProposal) => runAction(p.id, "Evaluate", () => evaluateSkillEvolution(p.id));

  const onPromote = (p: SkillEvolutionProposal) => {
    if (
      !window.confirm(
        `Promote proposal ${p.id} for skill "${p.skill_name}"?\n\nThe active skill is only replaced if the gateway accepts the explicit approval (approve:true); auto-promote is off.`
      )
    ) {
      return;
    }
    return runAction(p.id, "Promote", () => promoteSkillEvolution(p.id, { approve: true, reason: "promoted from Forge UI" }));
  };

  const onRollback = (p: SkillEvolutionProposal) => {
    if (!window.confirm(`Roll back proposal ${p.id}? The retained pre-promotion body is restored.`)) return;
    return runAction(p.id, "Rollback", () => rollbackSkillEvolution(p.id, "rolled back from Forge UI"));
  };

  const onDistill = async () => {
    setFormError(null);
    setDraft(null);
    setBusy(true);
    try {
      if (!dName.trim()) throw new Error("Skill name is required.");
      const steps = parseJson(dSteps, "Steps");
      if (!Array.isArray(steps)) throw new Error("Steps must be a JSON array of step objects.");
      const out = await distillSkillDraft({
        name: dName.trim(),
        description: dDesc,
        steps: steps as Array<Record<string, unknown>>,
      });
      setDraft(out);
      setPMarkdown(typeof out.markdown_content === "string" ? out.markdown_content : "");
      props.onNotice("Distill finished — review the draft below before publishing.");
    } catch (e) {
      setFormError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const onPublish = async () => {
    setFormError(null);
    setBusy(true);
    try {
      if (!dName.trim()) throw new Error("Skill name is required.");
      const out = await publishSkillDraft({
        name: dName.trim(),
        description: dDesc,
        markdown_content: pMarkdown,
      });
      props.onNotice(`Publish answered: ${summarize(out)}`);
      setDraft(null);
    } catch (e) {
      setFormError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const onPropose = async () => {
    setFormError(null);
    setBusy(true);
    try {
      if (!propSkill.trim()) throw new Error("Skill name is required.");
      if (!propCandidate.trim()) throw new Error("Candidate markdown is required.");
      const evidence = propEvidence
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const created = await proposeSkillEvolution({
        skill_name: propSkill.trim(),
        candidate_markdown: propCandidate,
        evidence,
      });
      props.onNotice(
        `Proposal ${created.id || "(id not returned)"} created with status ${created.status} — the active skill was not modified.`
      );
      setPropCandidate("");
      setPropEvidence("");
      await load();
    } catch (e) {
      setFormError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const draftFindings = draft && Array.isArray(draft.findings) ? (draft.findings as unknown[]) : [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="space-y-2">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <Hammer className="size-3.5 text-primary" /> Evolution proposals
          </p>
          {loading ? (
            <SkeletonList rows={4} />
          ) : error ? (
            <ErrorBox
              message={`Couldn't load workshop proposals — they are unavailable, not empty. (${error})`}
              onRetry={load}
            />
          ) : rows.length === 0 ? (
            <EmptyState
              title="No proposals yet"
              hint="No skill evolution has been proposed through the workshop. Proposals only appear after POST /api/skills/workshop/evolution."
            />
          ) : (
            <div className="space-y-2">
              {actionError && <ErrorBox message={actionError} />}
              {rows.map((p) => (
                <div key={p.id} className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone={statusTone(p.status)}>{p.status}</Badge>
                    <span className="text-xs font-semibold">{p.skill_name || "(unnamed skill)"}</span>
                    <span className="text-[10px] font-mono text-muted-foreground">
                      candidate v{p.candidate_version || "?"}
                      {p.base_version ? ` (base v${p.base_version})` : ""}
                    </span>
                    <BasisChips record={p} />
                  </div>

                  <div className="flex items-center gap-1 flex-wrap">
                    {p.evidence.length === 0 ? (
                      <span className="text-[11px] text-muted-foreground">No evidence references recorded.</span>
                    ) : (
                      p.evidence.map((e, i) => (
                        <Badge key={`${e.ref}-${i}`} tone="cyan">
                          {e.ref || "(evidence without ref)"}
                        </Badge>
                      ))
                    )}
                  </div>

                  {p.evaluation ? (
                    <div className="rounded-lg bg-muted/40 p-2.5 space-y-1.5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[11px] font-semibold">Offline evaluation</span>
                        {typeof p.evaluation.validation_kind === "string" && (
                          <Badge tone={p.evaluation.validation_kind === "structural-only" ? "amber" : "blue"}>
                            {p.evaluation.validation_kind}
                          </Badge>
                        )}
                        {typeof p.evaluation.checks_passed === "number" && (
                          <span className="text-[10px] text-muted-foreground">
                            {p.evaluation.checks_passed}/{p.evaluation.checks_total} checks passed
                          </span>
                        )}
                        {typeof p.evaluation.score === "number" && (
                          <span className="text-[10px] text-muted-foreground">score {p.evaluation.score}</span>
                        )}
                      </div>
                      {p.evaluation.validation_kind === "structural-only" && (
                        <p className="text-[10px] text-amber-600 dark:text-amber-400">
                          Structural checks only — runtime behavior was not executed.
                        </p>
                      )}
                      {p.evaluation.error && (
                        <p className="text-[10px] text-destructive">Evaluation error: {p.evaluation.error}</p>
                      )}
                      {(p.evaluation.findings ?? []).map((f, i) => (
                        <p key={i} className="text-[10px] text-muted-foreground">
                          {f}
                        </p>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      No offline evaluation recorded yet — this candidate is unverified.
                    </p>
                  )}

                  {p.moderation ? (
                    <p className="text-[10px] text-muted-foreground">
                      moderation: <Badge tone="gray">{p.moderation.status ?? "unknown"}</Badge>{" "}
                      {p.moderation.model ? `model ${p.moderation.model}` : "no model configured"}
                      {p.moderation.reason ? ` — ${p.moderation.reason}` : ""}
                    </p>
                  ) : (
                    <p className="text-[10px] text-muted-foreground">No moderation result recorded.</p>
                  )}

                  {(p.notes ?? []).length > 0 && (
                    <ul className="space-y-0.5">
                      {(p.notes ?? []).map((n, i) => (
                        <li key={i} className="text-[10px] text-muted-foreground">
                          • {n}
                        </li>
                      ))}
                    </ul>
                  )}

                  {p.reject_reason && (
                    <p className="text-[10px] text-destructive">reject reason: {p.reject_reason}</p>
                  )}

                  <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
                    <Btn
                      variant="ghost"
                      disabled={busyId === p.id}
                      onClick={() => onEvaluate(p)}
                      title="Run the offline validation + moderation pass"
                    >
                      Evaluate
                    </Btn>
                    <Btn
                      disabled={busyId === p.id}
                      onClick={() => onPromote(p)}
                      title="Requires explicit approval; auto-promote is off"
                    >
                      <Check className="size-3.5" /> Promote
                    </Btn>
                    <Btn
                      variant="danger"
                      disabled={busyId === p.id}
                      onClick={() => onRollback(p)}
                      title="Restore the retained pre-promotion body"
                    >
                      <RotateCcw className="size-3.5" /> Rollback
                    </Btn>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <FlaskConical className="size-3.5 text-primary" /> Distill &amp; publish a draft
          </p>
          <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
            <Field label="Skill name">
              <input className={inputCls} value={dName} onChange={(e) => setDName(e.target.value)} placeholder="research" />
            </Field>
            <Field label="Description">
              <input className={inputCls} value={dDesc} onChange={(e) => setDDesc(e.target.value)} placeholder="What this skill does" />
            </Field>
            <Field label="Steps (JSON array)" hint='e.g. [{"op": "read"}, {"op": "summarize"}]'>
              <textarea className={`${inputCls} font-mono h-20`} value={dSteps} onChange={(e) => setDSteps(e.target.value)} />
            </Field>
            <div className="flex gap-1.5 flex-wrap">
              <Btn disabled={busy} onClick={onDistill}>
                Distill draft
              </Btn>
              <Btn variant="ghost" disabled={busy || !pMarkdown} onClick={onPublish} title="Publishes exactly the markdown shown below">
                <Plus className="size-3.5" /> Publish
              </Btn>
            </div>
            {formError && <ErrorBox message={formError} />}

            {draft && (
              <div className="rounded-lg bg-muted/40 p-2.5 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[11px] font-semibold">Draft: {String(draft.name ?? dName)}</span>
                  {typeof draft.is_valid === "boolean" && (
                    <Badge tone={draft.is_valid ? "green" : "amber"}>
                      is_valid: {String(draft.is_valid)}
                    </Badge>
                  )}
                  <BasisChips record={draft} />
                </div>
                {draftFindings.map((f, i) => (
                  <p key={i} className="text-[10px] text-muted-foreground">
                    {String(f)}
                  </p>
                ))}
                <Field label="Markdown to publish (exactly what is written)" hint="Publishing writes a real file via the gateway; 409/422 answers surface above.">
                  <textarea
                    className={`${inputCls} font-mono h-32`}
                    value={pMarkdown}
                    onChange={(e) => setPMarkdown(e.target.value)}
                  />
                </Field>
              </div>
            )}
          </div>
          <p className="text-[10px] text-muted-foreground">
            Distill is a draft builder only — nothing is written until Publish. Publish answers with{" "}
            <span className="font-mono">{"{status, name, path}"}</span> or a 409/422 rejection.
          </p>

          <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
            <p className="text-[11px] font-semibold">Propose an evolution (201 — active skill untouched)</p>
            <Field label="Existing skill name">
              <input className={inputCls} value={propSkill} onChange={(e) => setPropSkill(e.target.value)} placeholder="research" />
            </Field>
            <Field label="Candidate markdown" hint="The full replacement body for the skill.">
              <textarea
                className={`${inputCls} font-mono h-24`}
                value={propCandidate}
                onChange={(e) => setPropCandidate(e.target.value)}
                placeholder={"# research\n..."}
              />
            </Field>
            <Field label="Evidence references (one per line)" hint='e.g. run:abc123, benchmark:workforce-smoke@v1'>
              <textarea
                className={`${inputCls} font-mono h-16`}
                value={propEvidence}
                onChange={(e) => setPropEvidence(e.target.value)}
              />
            </Field>
            <Btn disabled={busy} onClick={onPropose}>
              Propose
            </Btn>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────── Evolution ──────────────────────────────── */

function EvolutionPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [identity, setIdentity] = useState<EvolutionIdentity | null>(null);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [updateState, setUpdateState] = useState<EvolutionUpdateState | null>(null);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const [events, setEvents] = useState<EvolutionLedgerEvent[]>([]);
  const [ledgerError, setLedgerError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);

  const [surface, setSurface] = useState("");
  const [target, setTarget] = useState("");
  const [payload, setPayload] = useState("{}");
  const [candidateId, setCandidateId] = useState("");
  const [benchmarkJson, setBenchmarkJson] = useState("{}");
  const [baseline, setBaseline] = useState("{}");
  const [humanApproved, setHumanApproved] = useState(false);
  const [autonomous, setAutonomous] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [gateOutcome, setGateOutcome] = useState<{ promoted: boolean; reason: string } | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    setIdentityError(null);
    setUpdateError(null);
    setLedgerError(null);
    await Promise.all([
      (async () => {
        try {
          setIdentity(await getEvolutionIdentity());
        } catch (e) {
          setIdentity(null);
          setIdentityError(errMsg(e));
        }
      })(),
      (async () => {
        try {
          setUpdateState(await getEvolutionUpdateState());
        } catch (e) {
          setUpdateState(null);
          setUpdateError(errMsg(e));
        }
      })(),
      (async () => {
        try {
          setEvents(await getEvolutionLedger());
        } catch (e) {
          setEvents([]);
          setLedgerError(errMsg(e));
        }
      })(),
    ]);
    setLoading(false);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const onCheck = async () => {
    setChecking(true);
    setFormError(null);
    try {
      const state = await checkForEvolutionUpdate();
      setUpdateState(state);
      props.onNotice(`Update check answered: state ${state.state}${state.error ? ` — ${state.error}` : ""}`);
    } catch (e) {
      setFormError(`Update check failed — ${errMsg(e)}`);
    } finally {
      setChecking(false);
    }
  };

  const withForm = async (label: string, fn: () => Promise<unknown>) => {
    setFormError(null);
    setBusy(true);
    try {
      const out = await fn();
      props.onNotice(`${label}: ${summarize(out)}`);
      return out;
    } catch (e) {
      setFormError(`${label} failed — ${errMsg(e)}`);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const onPropose = () =>
    withForm("Propose candidate", async () => {
      if (!surface.trim() || !target.trim()) throw new Error("Surface and target are required.");
      const parsed = parseJson(payload, "Payload") as Record<string, unknown>;
      const out = await proposeEvolutionCandidate({ surface: surface.trim(), target: target.trim(), payload: parsed });
      const cid = strField(out, "candidate_id");
      if (cid) setCandidateId(cid);
      return out;
    });

  const onRecordBenchmark = () =>
    withForm("Record benchmark", async () => {
      if (!candidateId.trim()) throw new Error("Candidate ID is required.");
      const parsed = parseJson(benchmarkJson, "Benchmark") as Record<string, unknown>;
      return recordEvolutionBenchmark(candidateId.trim(), parsed);
    });

  const onGate = async () => {
    setGateOutcome(null);
    const out = await withForm("Gate", async () => {
      if (!candidateId.trim()) throw new Error("Candidate ID is required.");
      const parsed = parseJson(baseline, "Baseline") as Record<string, unknown>;
      return gateEvolutionCandidate(candidateId.trim(), {
        baseline: parsed,
        human_approved: humanApproved,
        autonomous_mode: autonomous,
      });
    });
    if (out && typeof out === "object" && "promoted" in out) {
      setGateOutcome(out as { promoted: boolean; reason: string });
    }
  };

  const onRollbackCandidate = () => {
    if (!window.confirm(`Roll back candidate ${candidateId.trim()}?`)) return;
    return withForm("Rollback", async () => {
      if (!candidateId.trim()) throw new Error("Candidate ID is required.");
      return rollbackEvolutionCandidate(candidateId.trim(), rollbackReason);
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <GitBranch className="size-3.5 text-primary" /> Runtime identity
          </p>
          {loading && !identity && !identityError ? (
            <SkeletonList rows={3} />
          ) : identityError ? (
            <ErrorBox message={`Identity is unavailable, not unknown-by-default. (${identityError})`} onRetry={load} />
          ) : identity ? (
            <div className="space-y-1.5 text-[11px]">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono">{identity.agentId}</span>
                <Badge tone="blue">v{identity.alphaVersion}</Badge>
                <BasisChips record={identity} />
              </div>
              <p>
                commit: <span className="font-mono">{identity.gitCommit}</span>{" "}
                <span className="text-muted-foreground">(source: {identity.gitCommitSource})</span>
              </p>
              {identity.gitCommitNote && (
                <p className="text-muted-foreground">{identity.gitCommitNote}</p>
              )}
              <p className="text-muted-foreground">
                {identity.os} • {identity.architecture} • {identity.runtime} • channel {identity.releaseChannel}
              </p>
              <div className="flex gap-1 flex-wrap">
                {identity.capabilities.map((c) => (
                  <Badge key={c} tone="cyan">
                    {c}
                  </Badge>
                ))}
              </div>
            </div>
          ) : (
            <EmptyState title="No identity record" hint="The gateway returned no identity payload." />
          )}

          <div className="pt-2 border-t border-border/50 space-y-1.5">
            <p className="text-[11px] font-semibold">Update state</p>
            {updateError ? (
              <ErrorBox message={`Update state is unavailable, not idle. (${updateError})`} onRetry={load} />
            ) : updateState ? (
              <div className="space-y-1 text-[11px]">
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge tone={statusTone(updateState.state)}>{updateState.state}</Badge>
                  <span className="text-muted-foreground">checked {fmtEpochSafe(updateState.checkedAt)}</span>
                </div>
                <p className="text-muted-foreground">
                  installed {updateState.installedVersion ?? "—"} • latest{" "}
                  {updateState.latestTag ?? "no tag recorded"}
                </p>
                {updateState.error && <p className="text-destructive">{updateState.error}</p>}
                <Btn variant="ghost" disabled={checking} onClick={onCheck} title="Runs a GitHub latest-release check from the gateway">
                  {checking ? "Checking…" : "Check for updates"}
                </Btn>
              </div>
            ) : (
              <SkeletonList rows={1} />
            )}
          </div>
          {formError && <ErrorBox message={formError} />}
        </div>

        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold">Evolution ledger</p>
          {loading && !events.length && !ledgerError ? (
            <SkeletonList rows={3} />
          ) : ledgerError ? (
            <ErrorBox message={`Ledger is unavailable, not empty. (${ledgerError})`} onRetry={load} />
          ) : events.length === 0 ? (
            <EmptyState
              title="No ledger events"
              hint="Nothing has been proposed, gated or rolled back in this gateway process — the ledger is genuinely empty, not filtered."
            />
          ) : (
            <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
              {events.map((ev, i) => (
                <div key={i} className="rounded-lg bg-muted/40 px-2 py-1.5 text-[10px] flex items-start gap-2 flex-wrap">
                  <Badge tone={statusTone(ev.event)}>{ev.event}</Badge>
                  {ev.candidate_id && <span className="font-mono text-muted-foreground">{ev.candidate_id}</span>}
                  <span className="text-muted-foreground ml-auto">{fmtEpoch(ev.at)}</span>
                  {strField(ev, "reason") && <span className="w-full text-muted-foreground">{strField(ev, "reason")}</span>}
                  <BasisChips record={ev} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
        <p className="text-[11px] font-semibold">Candidate tools (propose → benchmark → gate → rollback)</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <Field label="Surface">
              <input className={inputCls} value={surface} onChange={(e) => setSurface(e.target.value)} placeholder="prompt" />
            </Field>
            <Field label="Target">
              <input className={inputCls} value={target} onChange={(e) => setTarget(e.target.value)} placeholder="summarizer" />
            </Field>
            <Field label="Payload (JSON object)">
              <textarea className={`${inputCls} font-mono h-16`} value={payload} onChange={(e) => setPayload(e.target.value)} />
            </Field>
            <Btn disabled={busy} onClick={onPropose}>
              Propose candidate
            </Btn>
          </div>
          <div className="space-y-2">
            <Field label="Candidate ID" hint="Prefilled from the propose answer when the API returns one.">
              <input className={inputCls} value={candidateId} onChange={(e) => setCandidateId(e.target.value)} />
            </Field>
            <Field label="Benchmark report (JSON)">
              <textarea className={`${inputCls} font-mono h-16`} value={benchmarkJson} onChange={(e) => setBenchmarkJson(e.target.value)} />
            </Field>
            <Btn variant="ghost" disabled={busy} onClick={onRecordBenchmark}>
              Record benchmark
            </Btn>
            <Field label="Gate baseline (JSON)">
              <textarea className={`${inputCls} font-mono h-16`} value={baseline} onChange={(e) => setBaseline(e.target.value)} />
            </Field>
            <label className="flex items-center gap-2 text-[11px]">
              <input type="checkbox" checked={humanApproved} onChange={(e) => setHumanApproved(e.target.checked)} />
              human_approved
            </label>
            <label className="flex items-center gap-2 text-[11px] text-amber-600 dark:text-amber-400">
              <input type="checkbox" checked={autonomous} onChange={(e) => setAutonomous(e.target.checked)} />
              autonomous_mode (off unless a human explicitly grants it)
            </label>
            <Btn disabled={busy} onClick={onGate} title="Omission never grants autonomy — both flags default to false">
              <ShieldCheck className="size-3.5" /> Gate
            </Btn>
            {gateOutcome && (
              <div className="rounded-lg bg-muted/40 p-2 space-y-1 text-[11px]">
                <Badge tone={gateOutcome.promoted ? "green" : "amber"}>
                  {gateOutcome.promoted ? "promoted" : "not promoted"}
                </Badge>
                <p className="text-muted-foreground">{gateOutcome.reason || "(no reason returned)"}</p>
              </div>
            )}
            <Field label="Rollback reason">
              <input className={inputCls} value={rollbackReason} onChange={(e) => setRollbackReason(e.target.value)} />
            </Field>
            <Btn variant="danger" disabled={busy || !candidateId.trim()} onClick={onRollbackCandidate}>
              <RotateCcw className="size-3.5" /> Rollback candidate
            </Btn>
          </div>
        </div>
        {formError && <ErrorBox message={formError} />}
      </div>
    </div>
  );
}

/** ISO timestamp → locale string, honestly falling back to the raw text. */
function fmtEpochSafe(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/* ───────────────────────────────── Policy ───────────────────────────────── */

function PolicyPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [policies, setPolicies] = useState<PolicyRule[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [action, setAction] = useState("");
  const [actor, setActor] = useState("*");
  const [projectId, setProjectId] = useState("*");
  const [decision, setDecision] = useState<PolicyDecision | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalError, setEvalError] = useState<string | null>(null);

  const [pattern, setPattern] = useState("");
  const [auto, setAuto] = useState<"allow" | "deny" | "approval">("allow");
  const [note, setNote] = useState("");
  const [newAction, setNewAction] = useState("");
  const [newReason, setNewReason] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, a] = await Promise.all([listPolicies(), listApprovals()]);
      setPolicies(p);
      setApprovals(a);
    } catch (e) {
      setPolicies([]);
      setApprovals([]);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const onEvaluate = async () => {
    setEvalBusy(true);
    setEvalError(null);
    setDecision(null);
    try {
      if (!action.trim()) throw new Error("Action is required.");
      setDecision(await evaluateAction(action.trim(), actor.trim() || "*", projectId.trim() || "*"));
    } catch (e) {
      setEvalError(errMsg(e));
    } finally {
      setEvalBusy(false);
    }
  };

  const withForm = async (label: string, fn: () => Promise<unknown>) => {
    setFormError(null);
    setBusy(true);
    try {
      const out = await fn();
      props.onNotice(`${label}: ${summarize(out)}`);
      await load();
      return out;
    } catch (e) {
      setFormError(`${label} failed — ${errMsg(e)}`);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const onAdd = () =>
    withForm("Add policy", async () => {
      if (!pattern.trim()) throw new Error("Action pattern is required.");
      return addPolicy({ action_pattern: pattern.trim(), actor: actor.trim() || "*", project_id: projectId.trim() || "*", auto, note });
    });

  const onRemove = (p: PolicyRule) => {
    if (!window.confirm(`Remove policy ${p.policy_id} (${p.action_pattern})?`)) return;
    return withForm("Remove policy", () => removePolicy(p.policy_id));
  };

  const onCreateApproval = () =>
    withForm("Create approval", async () => {
      if (!newAction.trim()) throw new Error("Action is required.");
      return createApproval({ action: newAction.trim(), reason: newReason });
    });

  const onDecide = (r: ApprovalRequest, approved: boolean) =>
    withForm(approved ? "Approve" : "Reject", () => decideApproval(r.request_id, approved));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <ShieldCheck className="size-3.5 text-primary" /> Evaluate an action
          </p>
          <Field label="Action" hint="e.g. shell.exec, files.delete, chat.send">
            <input className={inputCls} value={action} onChange={(e) => setAction(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Actor">
              <input className={inputCls} value={actor} onChange={(e) => setActor(e.target.value)} />
            </Field>
            <Field label="Project">
              <input className={inputCls} value={projectId} onChange={(e) => setProjectId(e.target.value)} />
            </Field>
          </div>
          <Btn disabled={evalBusy} onClick={onEvaluate}>
            {evalBusy ? "Evaluating…" : "Evaluate"}
          </Btn>
          {evalError && <ErrorBox message={`Evaluation failed — ${evalError}`} />}
          {decision && (
            <div className="rounded-lg bg-muted/40 p-2.5 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge tone={statusTone(decision.verdict)}>{decision.verdict}</Badge>
                <BasisChips record={decision} />
              </div>
              <p className="text-[11px] text-muted-foreground">{decision.reason}</p>
              <p className="text-[11px]">
                matched rule:{" "}
                {decision.matched_rule ? (
                  <span className="font-mono">{decision.matched_rule}</span>
                ) : (
                  <span className="text-muted-foreground">none — the engine failed closed to approval</span>
                )}
              </p>
            </div>
          )}
        </div>

        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold">Operator policies</p>
          <p className="text-[10px] text-muted-foreground">
            These are the operator-added rows only — base rules live server-side and are not editable from here.
          </p>
          {loading ? (
            <SkeletonList rows={3} />
          ) : error ? (
            <ErrorBox message={`Policies are unavailable, not empty. (${error})`} onRetry={load} />
          ) : policies.length === 0 ? (
            <EmptyState title="No operator policies" hint="Only base rules are in effect; nothing has been added through POST /api/policy/policies." />
          ) : (
            <div className="space-y-1.5">
              {policies.map((p) => (
                <div key={p.policy_id} className="rounded-lg bg-muted/40 px-2.5 py-2 text-[11px] space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone={statusTone(p.auto)}>{p.auto}</Badge>
                    <span className="font-mono">{p.action_pattern}</span>
                    <span className="text-muted-foreground">
                      actor {p.actor} • project {p.project_id}
                    </span>
                    <BasisChips record={p} />
                    <Btn variant="danger" className="ml-auto" onClick={() => onRemove(p)} title="DELETE this policy">
                      <Trash2 className="size-3.5" />
                    </Btn>
                  </div>
                  {p.note && <p className="text-muted-foreground">{p.note}</p>}
                  <p className="text-[10px] text-muted-foreground">added {fmtEpoch(p.created_at)}</p>
                </div>
              ))}
            </div>
          )}
          <div className="pt-2 border-t border-border/50 space-y-2">
            <Field label="Action pattern">
              <input className={inputCls} value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="shell.*" />
            </Field>
            <Field label="Verdict (auto)">
              <select className={inputCls} value={auto} onChange={(e) => setAuto(e.target.value as "allow" | "deny" | "approval")}>
                <option value="allow">allow</option>
                <option value="deny">deny</option>
                <option value="approval">approval</option>
              </select>
            </Field>
            <Field label="Note">
              <input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <Btn disabled={busy} onClick={onAdd}>
              <Plus className="size-3.5" /> Add policy
            </Btn>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
        <p className="text-[11px] font-semibold">Human approvals</p>
        {loading ? (
          <SkeletonList rows={2} />
        ) : error ? (
          <ErrorBox message={`Approvals are unavailable, not empty. (${error})`} onRetry={load} />
        ) : approvals.length === 0 ? (
          <EmptyState title="No approval requests" hint="Nothing is waiting on a human decision right now." />
        ) : (
          <div className="space-y-1.5">
            {approvals.map((r) => (
              <div key={r.request_id} className="rounded-lg bg-muted/40 px-2.5 py-2 text-[11px] flex items-center gap-2 flex-wrap">
                <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                <span className="font-mono">{r.action}</span>
                <span className="text-muted-foreground">
                  {r.actor} • {r.project_id}
                </span>
                <BasisChips record={r} />
                {r.reason && <span className="w-full text-muted-foreground">{r.reason}</span>}
                <span className="text-[10px] text-muted-foreground">{fmtEpoch(r.created_at)}</span>
                {r.decided_by && <span className="text-[10px] text-muted-foreground">decided by {r.decided_by}</span>}
                {r.status === "pending" && (
                  <span className="ml-auto flex gap-1.5">
                    <Btn disabled={busy} onClick={() => onDecide(r, true)}>
                      <Check className="size-3.5" /> Approve
                    </Btn>
                    <Btn variant="danger" disabled={busy} onClick={() => onDecide(r, false)}>
                      <X className="size-3.5" /> Reject
                    </Btn>
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="pt-2 border-t border-border/50 grid grid-cols-1 md:grid-cols-3 gap-2 items-end">
          <Field label="Action to request">
            <input className={inputCls} value={newAction} onChange={(e) => setNewAction(e.target.value)} />
          </Field>
          <Field label="Reason">
            <input className={inputCls} value={newReason} onChange={(e) => setNewReason(e.target.value)} />
          </Field>
          <Btn disabled={busy} onClick={onCreateApproval}>
            <Plus className="size-3.5" /> Request approval
          </Btn>
        </div>
        {formError && <ErrorBox message={formError} />}
      </div>
    </div>
  );
}

/* ─────────────────────────────── Benchmarks ─────────────────────────────── */

function BenchmarksPanel(props: { refreshKey: number; onNotice: (m: string) => void }) {
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [results, setResults] = useState<BenchmarkCaseResult[]>([]);
  const [suitesError, setSuitesError] = useState<string | null>(null);
  const [resultsError, setResultsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [report, setReport] = useState<BenchmarkRunReport | null>(null);

  const load = async () => {
    setLoading(true);
    setSuitesError(null);
    setResultsError(null);
    await Promise.all([
      (async () => {
        try {
          setSuites(await listBenchmarkSuites());
        } catch (e) {
          setSuites([]);
          setSuitesError(errMsg(e));
        }
      })(),
      (async () => {
        try {
          setResults(await listBenchmarkResults());
        } catch (e) {
          setResults([]);
          setResultsError(errMsg(e));
        }
      })(),
    ]);
    setLoading(false);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.refreshKey]);

  const onRun = async (name: string) => {
    setRunning(name);
    setRunError(null);
    setReport(null);
    try {
      const r = await runBenchmarkSuite(name);
      setReport(r);
      props.onNotice(`Suite "${name}" finished: ${r.passed}/${r.total} passed, ${r.failed} failed.`);
      // Refresh the recorded-results list so the new rows appear below.
      try {
        setResults(await listBenchmarkResults());
      } catch (e) {
        setResultsError(errMsg(e));
      }
    } catch (e) {
      setRunError(`${name} could not be run — ${errMsg(e)}`);
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <Play className="size-3.5 text-primary" /> Registered suites
          </p>
          {loading && suites.length === 0 && !suitesError ? (
            <SkeletonList rows={3} />
          ) : suitesError ? (
            <ErrorBox message={`Suites are unavailable, not empty. (${suitesError})`} onRetry={load} />
          ) : suites.length === 0 ? (
            <EmptyState
              title="No suites registered"
              hint="The gateway reports zero registered benchmark suites — nothing to run."
            />
          ) : (
            <div className="space-y-1.5">
              {suites.map((s) => (
                <div key={`${s.name}@${s.version}`} className="rounded-lg bg-muted/40 px-2.5 py-2 text-[11px] flex items-center gap-2 flex-wrap">
                  <span className="font-mono font-semibold">{s.name}</span>
                  <Badge tone="blue">{s.version}</Badge>
                  <span className="text-muted-foreground">{s.cases} cases</span>
                  <BasisChips record={s} />
                  <Btn
                    variant="ghost"
                    className="ml-auto"
                    disabled={running !== null}
                    onClick={() => onRun(s.name)}
                    title="Runs every case in this suite now"
                  >
                    <Play className="size-3.5" /> {running === s.name ? "Running…" : "Run"}
                  </Btn>
                </div>
              ))}
            </div>
          )}
          {runError && <ErrorBox message={runError} />}

          {report && (
            <div className="rounded-lg border border-border/60 p-2.5 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] font-semibold font-mono">
                  {report.suite}@{report.version}
                </span>
                <Badge tone={report.failed === 0 ? "green" : "red"}>
                  {report.passed}/{report.total} passed
                </Badge>
                <span className="text-[10px] text-muted-foreground">failed {report.failed}</span>
                <BasisChips record={report} />
              </div>
              {report.results.length === 0 ? (
                <p className="text-[10px] text-muted-foreground">The run returned no per-case rows.</p>
              ) : (
                <div className="space-y-1">
                  {report.results.map((r) => (
                    <div key={r.result_id || r.case_id} className="text-[10px] flex items-center gap-2 flex-wrap">
                      <Badge tone={r.passed ? "green" : "red"}>{r.passed ? "pass" : "fail"}</Badge>
                      <span className="font-mono">{r.case_id}</span>
                      <span className="text-muted-foreground">score {r.score}</span>
                      <BasisChips record={r} />
                      {r.detail && <span className="w-full text-muted-foreground">{r.detail}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
          <p className="text-[11px] font-semibold">Recorded results</p>
          {loading && results.length === 0 && !resultsError ? (
            <SkeletonList rows={3} />
          ) : resultsError ? (
            <ErrorBox message={`Results are unavailable, not empty. (${resultsError})`} onRetry={load} />
          ) : results.length === 0 ? (
            <EmptyState
              title="No recorded results"
              hint="No benchmark case has been recorded by this gateway process — run a suite to produce real results. Nothing is faked or back-filled."
            />
          ) : (
            <div className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
              {results.map((r) => (
                <div key={r.result_id} className="rounded-lg bg-muted/40 px-2.5 py-2 text-[11px] space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone={r.passed ? "green" : "red"}>{r.passed ? "pass" : "fail"}</Badge>
                    <span className="font-mono">{r.case_id}</span>
                    <span className="text-muted-foreground">{r.suite}</span>
                    <BasisChips record={r} />
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    score {r.score} • {r.duration_sec}s • {fmtEpoch(r.created_at)}
                  </div>
                  {r.detail && <p className="text-[10px] text-muted-foreground">{r.detail}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <p className="text-[10px] text-muted-foreground">
        Suite case counts are the API&apos;s own tally of registered cases — not results. Pass/fail figures only ever
        come from a run answer (passed/total) or a recorded result row.
      </p>
    </div>
  );
}
