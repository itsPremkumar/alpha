"use client";

import React, { useEffect, useState } from "react";
import {
  EnterpriseHierarchyChart,
  EnterpriseRFC,
  TreasuryTelemetry,
  DAGSprint,
  ReleaseCandidate,
  EnterpriseTelemetry,
  fetchEnterpriseHierarchy,
  fetchEnterpriseRFCs,
  fetchEnterpriseTreasury,
  fetchMissionPipeline,
  fetchCouncilReleases,
  fetchEnterpriseTelemetry,
  triggerEnterpriseHeartbeat,
  stepSprintDAG,
  runHoldoutBenchmark,
  signCouncilRelease,
  resetTreasuryCircuitBreaker,
  createEnterpriseRFC,
  submitRFCReview,
  submitDebateArgument,
  evaluateRFCGating,
} from "@/lib/enterprise";
import { Section, EmptyState, ErrorBox, StatCard, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  Activity,
  AlertTriangle,
  Building,
  CheckCircle2,
  Coins,
  Cpu,
  FileCode,
  Flame,
  Gauge,
  GitBranch,
  Layers,
  Lock,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";

export function WarRoomSection() {
  const [subTab, setSubTab] = useState<"org_chart" | "rfcs" | "treasury" | "missions" | "council">("org_chart");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Core Data States
  const [telemetry, setTelemetry] = useState<EnterpriseTelemetry | null>(null);
  const [hierarchy, setHierarchy] = useState<EnterpriseHierarchyChart | null>(null);
  const [rfcs, setRfcs] = useState<EnterpriseRFC[]>([]);
  const [treasury, setTreasury] = useState<TreasuryTelemetry | null>(null);
  const [sprints, setSprints] = useState<DAGSprint[]>([]);
  const [releases, setReleases] = useState<ReleaseCandidate[]>([]);

  // Selection states
  const [selectedRfcId, setSelectedRfcId] = useState<string | null>(null);
  const [newRfcTitle, setNewRfcTitle] = useState("");
  const [newRfcProposal, setNewRfcProposal] = useState("");
  const [showRfcModal, setShowRfcModal] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [tel, hier, rfcList, treas, pipeline, rels] = await Promise.all([
        fetchEnterpriseTelemetry(),
        fetchEnterpriseHierarchy(),
        fetchEnterpriseRFCs(),
        fetchEnterpriseTreasury(),
        fetchMissionPipeline(),
        fetchCouncilReleases(),
      ]);

      if (tel) setTelemetry(tel);
      if (hier) setHierarchy(hier);
      setRfcs(rfcList);
      if (rfcList.length > 0 && !selectedRfcId) setSelectedRfcId(rfcList[0].rfc_id);
      if (treas) setTreasury(treas);
      setSprints(pipeline?.sprints || []);
      setReleases(rels);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleHeartbeat = async () => {
    setActionLoading(true);
    try {
      const res = await triggerEnterpriseHeartbeat();
      if (res.telemetry) setTelemetry(res.telemetry);
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleStepDAG = async (sprintId: string) => {
    setActionLoading(true);
    try {
      await stepSprintDAG(sprintId);
      const pipe = await fetchMissionPipeline();
      setSprints(pipe?.sprints || []);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleRunBenchmark = async (releaseId: string) => {
    setActionLoading(true);
    try {
      await runHoldoutBenchmark(releaseId);
      const rels = await fetchCouncilReleases();
      setReleases(rels);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleSign = async (releaseId: string, role: string, bot: string) => {
    setActionLoading(true);
    try {
      await signCouncilRelease(releaseId, role, bot);
      const rels = await fetchCouncilReleases();
      setReleases(rels);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleResetCircuitBreaker = async (deptId: string) => {
    setActionLoading(true);
    try {
      await resetTreasuryCircuitBreaker(deptId);
      const treas = await fetchEnterpriseTreasury();
      setTreasury(treas);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleCreateRfc = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRfcTitle.trim() || !newRfcProposal.trim()) return;
    setActionLoading(true);
    try {
      const created = await createEnterpriseRFC({
        title: newRfcTitle,
        author_bot: "bot-cto",
        department: "architecture",
        summary: newRfcProposal.slice(0, 100) + "...",
        proposal_content: newRfcProposal,
        affected_departments: ["engineering", "architecture", "security"],
      });
      setRfcs([created, ...rfcs]);
      setSelectedRfcId(created.rfc_id);
      setNewRfcTitle("");
      setNewRfcProposal("");
      setShowRfcModal(false);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setActionLoading(false);
    }
  };

  const selectedRfc = rfcs.find((r) => r.rfc_id === selectedRfcId);

  return (
    <Section
      title="Enterprise War Room Preview"
      hint="Synthetic enterprise telemetry, task progress, scores, and signatures are preview evidence only, not measured execution, security verification, or release authorization. Production promotion is unavailable."
      actions={
        <div className="flex items-center gap-2">
          <Btn variant="primary" onClick={handleHeartbeat} disabled={actionLoading}>
            <Activity className={`size-3.5 ${actionLoading ? "animate-spin" : "animate-pulse text-emerald-400"}`} />
            Step Heartbeat Cycle
          </Btn>
          <Btn variant="ghost" onClick={loadAll} disabled={loading}>
            <RefreshCw className={`size-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
          </Btn>
        </div>
      }
    >
      {error && <ErrorBox message={error} onRetry={loadAll} />}

      {/* Live Enterprise Telemetry Strip */}
      {telemetry && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <StatCard
            label="Heartbeat Cycle"
            value={`#${telemetry.heartbeat_cycle}`}
            sub={`Uptime: ${Math.round(telemetry.uptime_seconds)}s`}
          />
          <StatCard
            label="System Latency (p95)"
            value={`${telemetry.system_latency_p95_ms}ms`}
            sub="Synthetic preview; not measured latency"
          />
          <StatCard
            label="Security Posture"
            value={telemetry.security_posture_score == null ? "No scan" : `${telemetry.security_posture_score}%`}
            sub="Synthetic preview; not a security audit"
          />
          <StatCard
            label="Holdout Benchmark"
            value={telemetry.holdout_pass_rate_percent == null ? "No active release" : `${telemetry.holdout_pass_rate_percent}%`}
            sub="Synthetic preview; no release verified"
          />
          <StatCard
            label="Treasury Burn Rate"
            value={`${Math.round(telemetry.treasury_overall_burn_rate_tpm)} TPM`}
            sub={telemetry.treasury_circuit_breakers_tripped > 0 ? "Circuit Breaker Active" : "Nominal Budget"}
          />
          <StatCard
            label="Autonomous Recovery"
            value={(telemetry.stagnation_recovery_status || "nominal").toUpperCase()}
            sub="Perpetual Keel Watchdog"
          />
        </div>
      )}

      {/* War Room Sub-Navigation Tabs */}
      <div className="flex gap-1 border-b border-border/60 pb-2 overflow-x-auto">
        {[
          { id: "org_chart", label: "C-Suite & Org Tree", icon: <Building className="size-3.5" /> },
          { id: "rfcs", label: "Blackboard & RFCs", icon: <FileCode className="size-3.5" /> },
          { id: "treasury", label: "Fiscal Treasury", icon: <Coins className="size-3.5" /> },
          { id: "missions", label: "Mission-to-Sprint DAG", icon: <GitBranch className="size-3.5" /> },
          { id: "council", label: "Quality Council & Releases", icon: <ShieldCheck className="size-3.5" /> },
        ].map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setSubTab(tab.id as any)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              subTab === tab.id
                ? "bg-primary text-primary-foreground shadow"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {loading && !hierarchy ? (
        <SkeletonList rows={6} />
      ) : (
        <>
          {/* TAB 1: C-Suite & Org Tree */}
          {subTab === "org_chart" && hierarchy && (
            <div className="space-y-6">
              {/* C-Suite Leadership Swarm */}
              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
                  <Sparkles className="size-4 text-amber-400" />
                  C-Suite Autonomous Leadership Swarm
                </h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                  {hierarchy.csuite.map((leader) => (
                    <div
                      key={leader.node_id}
                      className="rounded-xl border border-primary/30 bg-card p-3.5 shadow-sm space-y-2 relative overflow-hidden"
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="font-semibold text-xs text-foreground">{leader.title}</p>
                          <p className="text-[11px] font-mono text-primary">{leader.bot_name}</p>
                        </div>
                        <Badge tone="indigo">{leader.contract.clearance}</Badge>
                      </div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {leader.contract.decision_authority.map((auth) => (
                          <span
                            key={auth}
                            className="text-[9px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-mono"
                          >
                            {auth}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Synthesized Departments Tree */}
              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
                  <Layers className="size-4 text-emerald-400" />
                  Synthesized Sub-Departments & Specialist Workforce
                </h3>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {hierarchy.departments.map((dept) => (
                    <div
                      key={dept.dept_id}
                      className="rounded-xl border border-border/80 bg-card p-4 space-y-3"
                    >
                      <div className="flex items-center justify-between border-b border-border/50 pb-2">
                        <div>
                          <h4 className="font-semibold text-xs text-foreground">{dept.name}</h4>
                          <p className="text-[11px] text-muted-foreground">
                            Lead: <span className="font-mono text-foreground">{dept.lead_bot_name}</span> ({dept.lead_title})
                          </p>
                        </div>
                        <Badge tone={dept.circuit_breaker_active ? "amber" : "green"}>
                          {dept.circuit_breaker_active ? "Throttled" : "Nominal"}
                        </Badge>
                      </div>

                      {/* Department Capabilities */}
                      <div className="space-y-1">
                        <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">
                          Department Capabilities
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {dept.capabilities.map((cap) => (
                            <span
                              key={cap}
                              className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-mono"
                            >
                              {cap}
                            </span>
                          ))}
                        </div>
                      </div>

                      {/* Specialist Workers Reporting Tree */}
                      <div className="space-y-1.5 pt-1">
                        <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">
                          Reporting Workforce ({dept.workers.length})
                        </span>
                        <div className="space-y-1">
                          {dept.workers.map((worker) => (
                            <div
                              key={worker.node_id}
                              className="flex items-center justify-between rounded-lg bg-muted/40 px-2.5 py-1.5 text-xs"
                            >
                              <div className="flex items-center gap-2">
                                <span className="size-1.5 rounded-full bg-emerald-500" />
                                <span className="font-medium text-[11px] text-foreground">{worker.title}</span>
                                <span className="font-mono text-[10px] text-muted-foreground">({worker.bot_name})</span>
                              </div>
                              <div className="flex items-center gap-1">
                                {worker.contract.capabilities.slice(0, 2).map((c) => (
                                  <span key={c} className="text-[9px] px-1 rounded bg-muted text-muted-foreground">
                                    {c}
                                  </span>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: RFC Blackboard & Epistemic Debate */}
          {subTab === "rfcs" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  Cross-department Request For Comments gating protocol with epistemic debate consensus.
                </p>
                <Btn variant="primary" onClick={() => setShowRfcModal(true)}>
                  <FileCode className="size-3.5" /> Propose New RFC
                </Btn>
              </div>

              {showRfcModal && (
                <div className="rounded-xl border border-primary/40 bg-card p-4 space-y-3">
                  <h4 className="text-xs font-semibold text-foreground">Propose Cross-Department RFC</h4>
                  <form onSubmit={handleCreateRfc} className="space-y-2">
                    <input
                      type="text"
                      placeholder="RFC Title (e.g. RFC-003: Distributed Vector Indexing)"
                      value={newRfcTitle}
                      onChange={(e) => setNewRfcTitle(e.target.value)}
                      className="w-full rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    <textarea
                      rows={3}
                      placeholder="Technical Proposal & Rationale..."
                      value={newRfcProposal}
                      onChange={(e) => setNewRfcProposal(e.target.value)}
                      className="w-full rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    <div className="flex justify-end gap-2">
                      <Btn variant="ghost" onClick={() => setShowRfcModal(false)}>
                        Cancel
                      </Btn>
                      <Btn variant="primary" type="submit" disabled={actionLoading}>
                        Submit Proposal
                      </Btn>
                    </div>
                  </form>
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* RFC List */}
                <div className="space-y-2">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                    Blackboard Proposals ({rfcs.length})
                  </h4>
                  {rfcs.map((rfc) => (
                    <button
                      key={rfc.rfc_id}
                      type="button"
                      onClick={() => setSelectedRfcId(rfc.rfc_id)}
                      className={`w-full text-left rounded-xl border p-3 transition-colors ${
                        selectedRfcId === rfc.rfc_id
                          ? "border-primary bg-primary/5 shadow-sm"
                          : "border-border/60 bg-card hover:border-border"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="font-mono text-[10px] text-muted-foreground">{rfc.rfc_id}</span>
                        <Badge tone={rfc.gating_passed ? "green" : rfc.status === "debating" ? "amber" : "gray"}>
                          {rfc.status.toUpperCase()}
                        </Badge>
                      </div>
                      <p className="font-semibold text-xs text-foreground line-clamp-1">{rfc.title}</p>
                      <p className="text-[11px] text-muted-foreground line-clamp-2 mt-1">{rfc.summary}</p>
                      <div className="flex items-center justify-between mt-2 pt-2 border-t border-border/40 text-[10px] text-muted-foreground">
                        <span>Author: {rfc.author_bot}</span>
                        <span>Consensus: {typeof rfc.consensus_score === "number" && Number.isFinite(rfc.consensus_score) ? `${(rfc.consensus_score * 100).toFixed(0)}%` : "—"}</span>
                      </div>
                    </button>
                  ))}
                </div>

                {/* Selected RFC Detail & Debate */}
                <div className="md:col-span-2 rounded-xl border border-border/70 bg-card p-4 space-y-4">
                  {selectedRfc ? (
                    <>
                      <div className="border-b border-border/50 pb-3">
                        <div className="flex items-center justify-between">
                          <h3 className="text-sm font-bold text-foreground">{selectedRfc.title}</h3>
                          <Badge tone={selectedRfc.gating_passed ? "green" : "amber"}>
                            Consensus: {typeof selectedRfc.consensus_score === "number" && Number.isFinite(selectedRfc.consensus_score) ? `${(selectedRfc.consensus_score * 100).toFixed(0)}%` : "—"}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          Author: <span className="font-mono text-foreground">{selectedRfc.author_bot}</span> ({selectedRfc.department})
                        </p>
                        <div className="mt-2 rounded-lg bg-muted/50 p-2.5 text-xs text-foreground">
                          {selectedRfc.proposal_content}
                        </div>
                      </div>

                      {/* Gating Status */}
                      <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-1">
                        <div className="flex items-center gap-2">
                          {selectedRfc.gating_passed ? (
                            <CheckCircle2 className="size-4 text-emerald-500" />
                          ) : (
                            <AlertTriangle className="size-4 text-amber-500" />
                          )}
                          <span className="text-xs font-semibold">
                            {selectedRfc.gating_passed ? "Consensus Gating PASSED" : "Consensus Gating PENDING"}
                          </span>
                        </div>
                        <p className="text-[11px] text-muted-foreground">{selectedRfc.gating_reason}</p>
                      </div>

                      {/* Multi-Agent Reviews */}
                      <div className="space-y-2">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                          Multi-Agent Reviews ({selectedRfc.reviews.length})
                        </h4>
                        <div className="space-y-1.5">
                          {selectedRfc.reviews.map((rev) => (
                            <div
                              key={rev.review_id}
                              className="rounded-lg border border-border/50 bg-muted/30 p-2 text-xs space-y-1"
                            >
                              <div className="flex items-center justify-between">
                                <span className="font-medium text-foreground">
                                  {rev.reviewer_bot} ({rev.department})
                                </span>
                                <Badge tone={rev.verdict === "approve" ? "green" : rev.verdict === "amend" ? "amber" : "red"}>
                                  {rev.verdict.toUpperCase()} ({typeof rev.epistemic_confidence === "number" && Number.isFinite(rev.epistemic_confidence) ? `${(rev.epistemic_confidence * 100).toFixed(0)}% conf` : "conf n/a"})
                                </Badge>
                              </div>
                              <p className="text-[11px] text-muted-foreground">{rev.argument}</p>
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Epistemic Debate Thread */}
                      <div className="space-y-2">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                          Epistemic Debate Thread ({selectedRfc.debate_thread.length})
                        </h4>
                        <div className="space-y-2">
                          {selectedRfc.debate_thread.map((arg) => (
                            <div
                              key={arg.argument_id}
                              className="rounded-lg border border-border/50 bg-card p-2.5 text-xs space-y-1"
                            >
                              <div className="flex items-center justify-between">
                                <span className="font-semibold text-foreground">
                                  {arg.speaker_bot} ({arg.department})
                                </span>
                                <Badge tone={arg.stance === "pro" ? "green" : arg.stance === "con" ? "red" : "indigo"}>
                                  {arg.stance.toUpperCase()}
                                </Badge>
                              </div>
                              <p className="text-[11px] font-medium text-foreground">{arg.claim}</p>
                              {arg.evidence && (
                                <p className="text-[10px] text-muted-foreground bg-muted/40 p-1.5 rounded font-mono">
                                  Evidence: {arg.evidence}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  ) : (
                    <EmptyState title="Select an RFC" hint="Choose a proposal from the blackboard to view debate." />
                  )}
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: Fiscal Treasury */}
          {subTab === "treasury" && treasury && (
            <div className="space-y-6">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <StatCard label="Total Allocated" value={`${(treasury.total_allocated_tokens / 1000).toFixed(0)}k`} sub="Capital tokens" />
                <StatCard label="Total Spent" value={`${(treasury.total_spent_tokens / 1000).toFixed(0)}k`} sub="Consumed" />
                <StatCard label="Current Balance" value={`${(treasury.total_balance_tokens / 1000).toFixed(0)}k`} sub="Reserve" />
                <StatCard label="ROI Velocity" value={`${treasury.average_roi_velocity}x`} sub="Tasks / 10k tokens" />
              </div>

              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
                  <Flame className="size-4 text-orange-400" />
                  Department Token Burn & Circuit Breaker Controls
                </h3>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  {treasury.departments.map((dept) => {
                    const pctSpent = Math.max(0, Math.min(100, Math.round((dept.spent_tokens / Math.max(1, dept.allocated_tokens)) * 100)));
                    return (
                      <div
                        key={dept.dept_id}
                        className={`rounded-xl border p-4 space-y-3 ${
                          dept.circuit_breaker_active ? "border-destructive bg-destructive/5" : "border-border/70 bg-card"
                        }`}
                      >
                        <div className="flex items-center justify-between">
                          <div>
                            <h4 className="font-semibold text-xs text-foreground">{dept.department_name}</h4>
                            <p className="text-[11px] font-mono text-muted-foreground">{dept.dept_id}</p>
                          </div>
                          <Badge tone={dept.circuit_breaker_active ? "red" : "green"}>
                            {dept.circuit_breaker_active ? "CIRCUIT BREAKER TRIPPED" : "NOMINAL"}
                          </Badge>
                        </div>

                        {/* Progress Bar */}
                        <div className="space-y-1">
                          <div className="flex justify-between text-[10px] text-muted-foreground">
                            <span>Consumed: {dept.spent_tokens.toLocaleString()} tokens</span>
                            <span>{pctSpent}%</span>
                          </div>
                          <div className="h-2 rounded-full bg-muted overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                dept.circuit_breaker_active ? "bg-destructive" : pctSpent > 80 ? "bg-amber-500" : "bg-primary"
                              }`}
                              style={{ width: `${pctSpent}%` }}
                            />
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2 text-xs pt-1">
                          <div className="rounded-lg bg-muted/40 p-2">
                            <span className="text-[10px] text-muted-foreground block">Burn Rate</span>
                            <span className="font-semibold font-mono">{dept.burn_rate_tpm} TPM</span>
                          </div>
                          <div className="rounded-lg bg-muted/40 p-2">
                            <span className="text-[10px] text-muted-foreground block">ROI Velocity</span>
                            <span className="font-semibold font-mono">{dept.roi_velocity}x</span>
                          </div>
                        </div>

                        {dept.circuit_breaker_active && (
                          <Btn
                            variant="primary"
                            className="w-full"
                            onClick={() => handleResetCircuitBreaker(dept.dept_id)}
                            disabled={actionLoading}
                          >
                            Reset Circuit Breaker
                          </Btn>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: Mission-to-Sprint DAG */}
          {subTab === "missions" && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  Strategic Mission decomposed into Epics, Technical Specs, and Dynamic Topological DAG Sprints.
                </p>
              </div>

              {sprints.map((sprint) => (
                <div key={sprint.sprint_id} className="rounded-xl border border-border/70 bg-card p-4 space-y-4">
                  <div className="flex items-center justify-between border-b border-border/50 pb-3">
                    <div>
                      <h4 className="font-bold text-xs text-foreground">{sprint.title}</h4>
                      <p className="text-[11px] text-muted-foreground">Sprint ID: {sprint.sprint_id}</p>
                    </div>
                    <div className="flex items-center gap-3">
                      <div className="text-right">
                        <span className="text-xs font-bold">{sprint.progress_percent}%</span>
                        <span className="text-[10px] text-muted-foreground block">Completion</span>
                      </div>
                      <Btn variant="primary" onClick={() => handleStepDAG(sprint.sprint_id)} disabled={actionLoading}>
                        <Play className="size-3.5" /> Step Sprint DAG
                      </Btn>
                    </div>
                  </div>

                  {/* DAG Tasks with Topological Layers */}
                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                      Dynamic DAG Tasks ({sprint.tasks.length})
                    </h5>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
                      {sprint.tasks.map((task) => (
                        <div
                          key={task.task_id}
                          className={`rounded-xl border p-3 space-y-2 text-xs transition-colors ${
                            task.status === "completed"
                              ? "border-emerald-500/40 bg-emerald-500/5"
                              : "border-border/60 bg-muted/20"
                          }`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-semibold text-foreground line-clamp-1">{task.title}</span>
                            <Badge tone={task.status === "completed" ? "green" : "gray"}>
                              {task.status.toUpperCase()}
                            </Badge>
                          </div>
                          <p className="text-[10px] text-muted-foreground font-mono">
                            Assigned: {task.assigned_bot} ({task.department})
                          </p>
                          {task.dependencies.length > 0 && (
                            <p className="text-[10px] text-muted-foreground">
                              Deps: {task.dependencies.length} task(s)
                            </p>
                          )}
                          {task.definition_of_done.length > 0 && (
                            <div className="pt-1 border-t border-border/30">
                              <span className="text-[9px] text-muted-foreground font-bold uppercase">DoD:</span>
                              <p className="text-[10px] text-muted-foreground line-clamp-1">
                                {task.definition_of_done[0]}
                              </p>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* TAB 5: Quality Council & Releases */}
          {subTab === "council" && (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Preview signature records and synthetic benchmark scores do not verify a release. Measured evidence and a durable lifecycle API are required before promotion can be enabled.
              </p>

              <div className="grid grid-cols-1 gap-4">
                {releases.map((rel) => {
                  const hasCTO = rel.signatures.some((s) => s.signatory_role === "CTO_ARCH");
                  const hasSWE = rel.signatures.some((s) => s.signatory_role === "SWE_BENCHMARK");
                  const hasCISO = rel.signatures.some((s) => s.signatory_role === "CISO_ASTRA");
                  const readyToPromote = rel.status === "multi_sig_verified";

                  return (
                    <div key={rel.release_id} className="rounded-xl border border-border/80 bg-card p-4 space-y-4">
                      <div className="flex items-center justify-between border-b border-border/50 pb-3">
                        <div>
                          <div className="flex items-center gap-2">
                            <h4 className="font-bold text-sm text-foreground">{rel.version}</h4>
                            <span className="text-xs text-muted-foreground">({rel.component})</span>
                            <Badge tone="gray">
                              Preview: {rel.status.replaceAll("_", " ").toUpperCase()}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">{rel.description}</p>
                        </div>
                        {readyToPromote && (
                          <Btn variant="primary" disabled title="Unavailable: measured backend evidence and a durable promotion API are required">
                            <Sparkles className="size-3.5" /> Promotion unavailable
                          </Btn>
                        )}
                      </div>

                      {/* 3 Cryptographic Signatures Seals */}
                      <div>
                        <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground block mb-2">
                          Preview Signature Records (not cryptographic proof)
                        </span>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                          {/* 1. Lead Architect CTO */}
                          <div
                            className={`rounded-xl border p-3 space-y-2 text-xs ${
                              hasCTO ? "border-emerald-500/40 bg-emerald-500/5" : "border-border/60 bg-muted/20"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-foreground">1. Lead Architect Sign-off</span>
                              <Badge tone={hasCTO ? "green" : "gray"}>{hasCTO ? "SIGNED" : "PENDING"}</Badge>
                            </div>
                            <p className="text-[10px] text-muted-foreground">CTO_ARCH attestation</p>
                            {!hasCTO && (
                              <Btn
                                variant="ghost"
                                className="w-full text-xs"
                                onClick={() => handleSign(rel.release_id, "CTO_ARCH", "bot-cto")}
                                disabled={actionLoading}
                              >
                                Sign as Lead Architect
                              </Btn>
                            )}
                          </div>

                          {/* 2. SWE Benchmark */}
                          <div
                            className={`rounded-xl border p-3 space-y-2 text-xs ${
                              hasSWE ? "border-emerald-500/40 bg-emerald-500/5" : "border-border/60 bg-muted/20"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-foreground">2. SWE Holdout Benchmark</span>
                              <Badge tone={hasSWE ? "green" : "gray"}>
                                {hasSWE ? `${rel.holdout_benchmark_score}%` : "PENDING"}
                              </Badge>
                            </div>
                            <p className="text-[10px] text-muted-foreground">Holdout suite score &gt;= 90%</p>
                            {!hasSWE && (
                              <Btn
                                variant="ghost"
                                className="w-full text-xs"
                                onClick={() => handleRunBenchmark(rel.release_id)}
                                disabled={actionLoading}
                              >
                                Run Holdout Benchmark
                              </Btn>
                            )}
                          </div>

                          {/* 3. CISO Astra Security */}
                          <div
                            className={`rounded-xl border p-3 space-y-2 text-xs ${
                              hasCISO ? "border-emerald-500/40 bg-emerald-500/5" : "border-border/60 bg-muted/20"
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <span className="font-semibold text-foreground">3. Security & AST Audit</span>
                              <Badge tone={hasCISO ? "green" : "gray"}>{hasCISO ? "SIGNED" : "PENDING"}</Badge>
                            </div>
                            <p className="text-[10px] text-muted-foreground">CISO_ASTRA attestation</p>
                            {!hasCISO && (
                              <Btn
                                variant="ghost"
                                className="w-full text-xs"
                                onClick={() => handleSign(rel.release_id, "CISO_ASTRA", "bot-ciso")}
                                disabled={actionLoading}
                              >
                                Sign as Quality & Security Director
                              </Btn>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
