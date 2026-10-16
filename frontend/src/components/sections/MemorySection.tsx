"use client";

import React, { useEffect, useState } from "react";
import {
  fetchMemory,
  addFact,
  deleteFact,
  updateFact,
  reloadMemory,
  clearMemory,
  fetchMemoryStatus,
  fetchProspectiveMemory,
  transitionProspectiveMemory,
  fetchCognitiveOverview,
  recallCognitiveMemory,
  fetchWorkingMemory,
  addWorkingMemory,
  clearWorkingMemory,
  fetchSemanticGraph,
  addSemanticBelief,
  deleteSemanticBelief,
  fetchProceduralSkills,
  registerProceduralSkill,
  deleteProceduralSkill,
  triggerConsolidation,
  triggerBeliefReconciliation,
  MemoryFact,
  MemoryStatus,
  MemoryStatusEnvelope,
  MemoryExtractionHealth,
  ProspectiveMemoryItem,
  L1CaptureStatus,
  CognitiveOverview,
  ScoredMemoryItem,
  WorkingMemoryItem,
  SemanticNodeItem,
  ProceduralSkillItem,
} from "@/lib/memory";
import {
  Section,
  StatCard,
  EmptyState,
  ErrorBox,
  Notice,
  Btn,
  Field,
  SkeletonList,
  inputCls,
  Badge,
} from "@/components/ui";
import { errMsg } from "@/lib/http";
import { absoluteStamp, relTime } from "@/lib/time";
import {
  Plus,
  Pencil,
  Trash2,
  RefreshCw,
  Search,
  Brain,
  Layers,
  Sparkles,
  GitBranch,
  Zap,
  Moon,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";

type ActiveTab = "overview" | "recall" | "beliefs" | "skills" | "working" | "facts" | "prospective";

function L1CaptureCard({ capture, error }: { capture?: L1CaptureStatus; error: string | null }) {
  const state = capture?.status;
  const tone = state === "available" ? "blue" : state === "degraded" ? "amber" : state === "unreadable" || state === "unavailable" ? "red" : "gray";
  const summary = error
    ? `Capture status unavailable: ${error}`
    : state === "disabled"
      ? `L1 capture is disabled (${capture?.reason ?? "disabled by configuration"}).`
      : state === "no_recent_runs"
        ? `No L1 generation runs were recorded in the last ${capture?.window_days ?? 7} days.`
        : state === "no_readable_entries"
          ? "The recent L1 generation log contains no readable entries."
        : state === "not_reported"
          ? "The latest readable L1 run predates capture outcome reporting."
          : state === "unreadable"
            ? "The recent L1 generation log could not be read."
            : state === "degraded"
              ? "The latest readable capture outcome is shown; part of the generation log could not be read."
              : state === "available"
                ? "Latest owner-scoped L1 capture outcome. Memory content is not shown here."
                : "Capture status was not reported by the Gateway.";
  const timestamp = capture?.latest?.timestamp;
  const parsedTimestamp = timestamp ? Date.parse(timestamp) : NaN;
  const displayTimestamp = timestamp
    ? Number.isNaN(parsedTimestamp)
      ? timestamp
      : new Date(parsedTimestamp).toLocaleString()
    : null;

  return (
    <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3" aria-label="L1 capture status">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Layers className="size-4 text-cyan-400" />
          <span className="text-xs font-semibold">Automatic L1 Capture</span>
        </div>
        <Badge tone={tone}>{state ?? (error ? "unavailable" : "not reported")}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{summary}</p>
      {capture?.latest && (
        <div className="space-y-2 rounded-xl bg-muted/40 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
            <span>{displayTimestamp ?? "Run time not reported"}</span>
            <span>Run: {capture.latest.run_status ?? "status not reported"}</span>
          </div>
          {capture.latest.surfaces.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {capture.latest.surfaces.map((surface) => {
                const counts = [
                  surface.written !== undefined ? `${surface.written} stored` : null,
                  surface.considered !== undefined ? `${surface.considered} considered` : null,
                  surface.rejected !== undefined && surface.rejected > 0 ? `${surface.rejected} rejected` : null,
                ].filter(Boolean).join(" · ");
                return (
                  <Badge key={surface.surface} tone={surface.status === "error" ? "red" : surface.status === "written" ? "green" : "gray"}>
                    {surface.surface}: {surface.status}{counts ? ` · ${counts}` : ""}
                  </Badge>
                );
              })}
            </div>
          ) : (
            <p className="text-[11px] text-muted-foreground">No per-surface result was recorded for this run.</p>
          )}
        </div>
      )}
    </div>
  );
}

function ProspectiveMemoryCard({ memory, error }: { memory?: MemoryStatus["prospective"]; error: string | null }) {
  const state = memory?.status;
  const tone = state === "available" ? "green" : state === "degraded" ? "amber" : state === "unreadable" || state === "unavailable" ? "red" : "gray";
  const description = error
    ? `Reminder status unavailable: ${error}`
    : state === "disabled"
      ? "Reminder and commitment memory is disabled by configuration."
      : state === "empty"
        ? "No reminders or commitments are stored for this account."
        : state === "degraded"
          ? memory?.reason === "unsupported_store_format"
            ? "The reminder store was created by a newer version. Its file was preserved; update Alpha to read it."
            : `Reminder data was recovered with a warning (${memory?.reason ?? "recovery needed"}).`
          : state === "unreadable"
            ? "The reminder store could not be read. No reminder data is shown."
            : state === "unavailable"
              ? "Reminder status could not be determined."
              : "Owner-scoped reminder and commitment lifecycle counts. Reminder text is not shown here.";

  return (
    <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3" aria-label="Prospective memory status">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="size-4 text-violet-400" />
          <span className="text-xs font-semibold">Reminders &amp; Commitments</span>
        </div>
        <Badge tone={tone}>{state ?? (error ? "unavailable" : "not reported")}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{description}</p>
      {memory && (state === "available" || state === "degraded") && (
        <div className="flex flex-wrap gap-2" aria-label="Reminder lifecycle counts">
          <Badge tone="blue">{memory.pending} pending</Badge>
          <Badge tone="gray">{memory.fired} triggered</Badge>
          <Badge tone="green">{memory.done} done</Badge>
          {(memory.cancelled > 0 || memory.expired > 0) && (
            <Badge tone="gray">{memory.cancelled} cancelled · {memory.expired} expired</Badge>
          )}
        </div>
      )}
    </div>
  );
}

export function MemorySection() {
  const [tab, setTab] = useState<ActiveTab>("overview");

  // Legacy facts state
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [summary, setSummary] = useState("");
  const [status, setStatus] = useState<MemoryStatusEnvelope | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Cognitive states
  const [cognitive, setCognitive] = useState<CognitiveOverview | null>(null);
  const [workingItems, setWorkingItems] = useState<WorkingMemoryItem[]>([]);
  const [semanticNodes, setSemanticNodes] = useState<SemanticNodeItem[]>([]);
  const [proceduralSkills, setProceduralSkills] = useState<ProceduralSkillItem[]>([]);
  const [prospectiveItems, setProspectiveItems] = useState<ProspectiveMemoryItem[]>([]);

  // Interactive recall simulator state
  const [recallQuery, setRecallQuery] = useState("AgentArchitecture streaming protocol");
  const [recallLimit, setRecallLimit] = useState(6);
  const [bm25Weight, setBm25Weight] = useState(0.35);
  const [vectorWeight, setVectorWeight] = useState(0.35);
  const [temporalWeight, setTemporalWeight] = useState(0.15);
  const [graphWeight, setGraphWeight] = useState(0.15);
  const [recallResults, setRecallResults] = useState<ScoredMemoryItem[]>([]);
  const [searching, setSearching] = useState(false);

  // Forms
  const [draftFact, setDraftFact] = useState("");
  const [editingFact, setEditingFact] = useState<{ id: string; text: string } | null>(null);

  const [draftSub, setDraftSub] = useState("");
  const [draftPred, setDraftPred] = useState("");
  const [draftObj, setDraftObj] = useState("");

  const [draftSkillName, setDraftSkillName] = useState("");
  const [draftSkillDesc, setDraftSkillDesc] = useState("");
  const [draftSkillTrigger, setDraftSkillTrigger] = useState("");
  const [draftSkillSteps, setDraftSkillSteps] = useState("");

  const [draftWmContent, setDraftWmContent] = useState("");
  const [draftWmTag, setDraftWmTag] = useState("goal");

  // UI status
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Per-surface load failures: a failed fetch is rendered as "unavailable",
  // never substituted with empty facts/items/graph that read as "you have
  // nothing".
  const [factsError, setFactsError] = useState<string | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [workingError, setWorkingError] = useState<string | null>(null);
  const [beliefsError, setBeliefsError] = useState<string | null>(null);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [prospectiveError, setProspectiveError] = useState<string | null>(null);

  const flash = (msg: string) => {
    setNotice(msg);
    window.setTimeout(() => setNotice(null), 4500);
  };

  // Tab loaders: on failure they flag the tab instead of leaving (or
  // substituting) an empty list that would render as a truthful empty state.
  const loadWorking = async () => {
    try {
      const items = await fetchWorkingMemory();
      setWorkingItems(items);
      setWorkingError(null);
    } catch (e) {
      setWorkingError(errMsg(e));
    }
  };

  const loadBeliefs = async () => {
    try {
      const res = await fetchSemanticGraph();
      setSemanticNodes(res.nodes);
      setBeliefsError(null);
    } catch (e) {
      setBeliefsError(errMsg(e));
    }
  };

  const loadSkills = async () => {
    try {
      const skills = await fetchProceduralSkills();
      setProceduralSkills(skills);
      setSkillsError(null);
    } catch (e) {
      setSkillsError(errMsg(e));
    }
  };

  const loadProspective = async () => {
    try {
      const result = await fetchProspectiveMemory();
      setProspectiveItems(result.items);
      setProspectiveError(result.status === "recovered" ? (result.reason ?? "Stored reminders were recovered with a warning.") : null);
    } catch (e) {
      setProspectiveError(errMsg(e));
    }
  };

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    setFactsError(null);
    setOverviewError(null);
    setStatus(null);
    setStatusError(null);
    try {
      const [mem, st, cog] = await Promise.all([
        // Failures become explicit per-surface errors — never {}fakes like
        // { facts: [], summary: "" } that would render as a real empty store.
        fetchMemory()
          .then((value) => ({ ok: true as const, value }))
          .catch((e) => ({ ok: false as const, error: errMsg(e) })),
        fetchMemoryStatus()
          .then((value) => ({ ok: true as const, value }))
          .catch((e) => ({ ok: false as const, error: errMsg(e) })),
        fetchCognitiveOverview()
          .then((value) => ({ ok: true as const, value }))
          .catch((e) => ({ ok: false as const, error: errMsg(e) })),
      ]);
      if (mem.ok) {
        setFacts(mem.value.facts);
        setSummary(mem.value.summary);
      } else {
        setFactsError(mem.error);
      }
      if (st.ok) {
        setStatus(st.value);
        setStatusError(null);
      } else {
        setStatus(null);
        setStatusError(st.error);
      }
      if (cog.ok) {
        setCognitive(cog.value);
      } else {
        setOverviewError(cog.error);
      }

      // Load specific tab data in background — each failure flags its tab.
      void loadWorking();
      void loadBeliefs();
      void loadSkills();
      void loadProspective();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  // Hybrid Recall Execution
  const onRunRecall = async () => {
    if (!recallQuery.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const results = await recallCognitiveMemory({
        query: recallQuery.trim(),
        limit: recallLimit,
        bm25_weight: bm25Weight,
        vector_weight: vectorWeight,
        temporal_weight: temporalWeight,
        graph_weight: graphWeight,
      });
      setRecallResults(results);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSearching(false);
    }
  };

  const onTransitionProspective = async (item: ProspectiveMemoryItem, action: "complete" | "cancel") => {
    setActionBusy(true);
    try {
      await transitionProspectiveMemory(item.id, action);
      flash(action === "complete" ? "Reminder marked complete." : "Reminder cancelled.");
      await Promise.all([
        loadProspective(),
        fetchMemoryStatus()
          .then((nextStatus) => {
            setStatus(nextStatus);
            setStatusError(null);
          })
          .catch((e) => setStatusError(errMsg(e))),
      ]);
    } catch (e) {
      setProspectiveError(errMsg(e));
    } finally {
      setActionBusy(false);
    }
  };

  // Consolidation Trigger
  const onTriggerConsolidation = async () => {
    setActionBusy(true);
    try {
      const report = await triggerConsolidation();
      if (report) {
        flash(
          `Sleep/Dream Cycle completed: Pruned ${report.light_sleep_pruned} noise items, crystallized ${report.deep_sleep_beliefs_crystallized} beliefs!`
        );
      } else {
        flash("Consolidation dream cycle executed successfully.");
      }
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionBusy(false);
    }
  };

  // Belief Reconciliation Trigger
  const onTriggerReconciliation = async () => {
    setActionBusy(true);
    try {
      const res = await triggerBeliefReconciliation();
      flash(
        `Epistemic Reconciliation completed: Reconciled ${res.reconciled_count} conflicting beliefs (${res.conflicts_detected_count} analyzed).`
      );
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setActionBusy(false);
    }
  };

  // Working Memory Actions
  const onAddWorking = async () => {
    if (!draftWmContent.trim()) return;
    try {
      await addWorkingMemory(draftWmContent.trim(), draftWmTag);
      setDraftWmContent("");
      flash("Working memory scratchpad updated.");
      const items = await fetchWorkingMemory();
      setWorkingItems(items);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onClearWorking = async () => {
    if (!window.confirm("Clear all items from working scratchpad?")) return;
    try {
      await clearWorkingMemory();
      setWorkingItems([]);
      flash("Working memory cleared.");
    } catch (e) {
      setError(errMsg(e));
    }
  };

  // Semantic Belief Add
  const onAddBelief = async () => {
    if (!draftSub.trim() || !draftPred.trim() || !draftObj.trim()) return;
    try {
      await addSemanticBelief(draftSub.trim(), draftPred.trim(), draftObj.trim());
      setDraftSub("");
      setDraftPred("");
      setDraftObj("");
      flash("New epistemic belief crystallized into Semantic Graph.");
      const res = await fetchSemanticGraph();
      setSemanticNodes(res.nodes);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  // Procedural Skill Register
  const onRegisterSkill = async () => {
    if (!draftSkillName.trim() || !draftSkillTrigger.trim()) return;
    try {
      const steps = draftSkillSteps
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      await registerProceduralSkill(
        draftSkillName.trim(),
        draftSkillDesc.trim(),
        draftSkillTrigger.trim(),
        steps
      );
      setDraftSkillName("");
      setDraftSkillDesc("");
      setDraftSkillTrigger("");
      setDraftSkillSteps("");
      flash("Procedural skill playbook registered.");
      const skills = await fetchProceduralSkills();
      setProceduralSkills(skills);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onDeleteBelief = async (nodeId: string) => {
    if (!window.confirm("Delete this belief node from Semantic Graph?")) return;
    try {
      await deleteSemanticBelief(nodeId);
      setSemanticNodes((prev) => prev.filter((n) => n.node_id !== nodeId));
      flash("Belief node removed from graph.");
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onDeleteSkill = async (skillId: string) => {
    if (!window.confirm("Delete this procedural skill?")) return;
    try {
      await deleteProceduralSkill(skillId);
      setProceduralSkills((prev) => prev.filter((s) => s.skill_id !== skillId));
      flash("Procedural skill removed.");
    } catch (e) {
      setError(errMsg(e));
    }
  };

  // Legacy Fact Actions
  const onAddFact = async () => {
    if (!draftFact.trim()) return;
    try {
      await addFact(draftFact.trim());
      setDraftFact("");
      flash("Fact saved to agent memory.");
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onDeleteFact = async (id: string) => {
    if (!window.confirm("Forget this fact?")) return;
    try {
      await deleteFact(id);
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onSaveEditFact = async () => {
    if (!editingFact || !editingFact.text.trim()) return;
    try {
      await updateFact(editingFact.id, editingFact.text.trim());
      setEditingFact(null);
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onClearAllFacts = async () => {
    if (!window.confirm("Erase all legacy memory facts? This cannot be undone.")) return;
    try {
      await clearMemory();
      await loadAll();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  // Distinguish a successful status read without a health hook from a pending
  // or failed read; only the Gateway can establish extraction health.
  const extractionBadge = status && !statusError ? extractionHealthBadge(status.health) : null;

  return (
    <Section
      title="Cognitive Memory Command Center"
      hint="Multi-Tier Cognitive Memory: Working, Episodic, Semantic Belief Graph, Procedural Skills, Spatio-Temporal, Associative Networks, and Sleep/Dream Consolidation."
      actions={
        <>
          <Btn variant="ghost" onClick={() => reloadMemory().then(loadAll).catch((e) => setError(errMsg(e)))}>
            <RefreshCw className="size-3.5" /> Reload
          </Btn>
          <Btn variant="ghost" onClick={onTriggerConsolidation} disabled={actionBusy} title="Run 3-Phase Sleep/Dream Consolidation">
            <Moon className="size-3.5 text-indigo-400" /> Sleep Consolidation
          </Btn>
          <Btn variant="ghost" onClick={onTriggerReconciliation} disabled={actionBusy} title="Reconcile Contradictory Beliefs">
            <Sparkles className="size-3.5 text-amber-400" /> Reconcile Beliefs
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={loadAll} />}
      {notice && <Notice message={notice} />}
      {statusError && (
        <p role="status" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-[11px] text-destructive">
          Memory status is unavailable: {statusError}. Extraction health and the on/off state stay unknown until the status read succeeds.
        </p>
      )}

      {/* Cognitive Status Badges */}
      <div className="flex gap-2 flex-wrap text-[11px] items-center">
        <Badge tone="blue">Cognitive V2.0</Badge>
        {overviewError && <Badge tone="red">overview unavailable</Badge>}
        {cognitive && (
          <>
            <Badge tone="purple">{cognitive.tiers.semantic_graph.total_nodes} beliefs</Badge>
            <Badge tone="green">{cognitive.tiers.procedural_memory.skills_count} skills</Badge>
            <Badge tone="amber">{cognitive.tiers.episodic_memory.flat_traces_count} traces</Badge>
            <Badge tone="cyan">{cognitive.tiers.working_memory.count} working items</Badge>
            <Badge tone="gray">Density: {cognitive.tiers.semantic_graph.density}</Badge>
          </>
        )}
        {status && (
          <Badge
            tone={status.config.enabled === true ? "green" : "gray"}
            title={
              status.config.enabled === true
                ? "The Gateway reports the memory mechanism enabled (GET /api/memory/status → config.enabled)."
                : status.config.enabled === false
                  ? "The Gateway reports the memory mechanism disabled (GET /api/memory/status → config.enabled)."
                  : "The Gateway did not report whether memory is enabled."
            }
          >
            gateway memory {status.config.enabled === true ? "on" : status.config.enabled === false ? "off" : "state not reported"}
          </Badge>
        )}
        {!status && !statusError && <Badge tone="gray">extraction health: checking&hellip;</Badge>}
        {extractionBadge && <Badge tone={extractionBadge.tone} title={extractionBadge.title}>{extractionBadge.label}</Badge>}
      </div>

      {/* Command Center Tabs */}
      <div className="flex border-b border-border/70 gap-1 overflow-x-auto pb-1 text-xs">
        <button
          onClick={() => setTab("overview")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "overview" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          <Brain className="size-3.5" /> Overview & Tiers
        </button>
        <button
          onClick={() => {
            setTab("recall");
            if (recallResults.length === 0) onRunRecall();
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "recall" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          <Search className="size-3.5" /> Hybrid Recall Simulator
        </button>
        <button
          onClick={() => {
            setTab("beliefs");
            void loadBeliefs();
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "beliefs" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          <GitBranch className="size-3.5" /> Belief Graph ({beliefsError ? "?" : semanticNodes.length})
        </button>
        <button
          onClick={() => {
            setTab("skills");
            void loadSkills();
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "skills" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          <Zap className="size-3.5" /> Procedural Skills ({skillsError ? "?" : proceduralSkills.length})
        </button>
        <button
          onClick={() => {
            setTab("working");
            void loadWorking();
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "working" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          <Layers className="size-3.5" /> Working Scratchpad ({workingError ? "?" : workingItems.length})
        </button>
        <button
          onClick={() => setTab("facts")}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "facts" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
        >
          Facts & Preferences ({factsError ? "?" : facts.length})
        </button>
        <button
          onClick={() => {
            setTab("prospective");
            void loadProspective();
          }}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-colors ${
            tab === "prospective" ? "bg-primary text-primary-foreground" : "hover:bg-muted text-muted-foreground"
          }`}
          aria-label={`Reminders and commitments (${prospectiveError ? "unavailable" : prospectiveItems.length})`}
        >
          <CheckCircle2 className="size-3.5" /> Reminders ({prospectiveError ? "?" : prospectiveItems.length})
        </button>
      </div>

      {loading ? (
        <SkeletonList rows={4} />
      ) : (
        <>
          {/* TAB 1: COGNITIVE OVERVIEW */}
          {tab === "overview" && (
            <div className="grid gap-3 xl:grid-cols-2">
              <L1CaptureCard capture={status?.l1_capture} error={statusError} />
              {status && !statusError ? <MemoryHealthCard health={status.health} config={status.config} /> : null}
              <ProspectiveMemoryCard memory={status?.prospective} error={statusError} />
            </div>
          )}
          {tab === "overview" && overviewError && (
            <ErrorBox
              message={`Cognitive overview unavailable — tier counts could not be loaded, so none are shown. (${overviewError})`}
              onRetry={loadAll}
            />
          )}
          {tab === "overview" && !overviewError && cognitive && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <StatCard
                  label="Working Scratchpad"
                  value={String(cognitive.tiers.working_memory.count)}
                  sub={`${cognitive.tiers.working_memory.active_high_attention} active focus`}
                />
                <StatCard
                  label="Episodic Traces"
                  value={String(cognitive.tiers.episodic_memory.flat_traces_count)}
                  sub={`${cognitive.tiers.episodic_memory.hierarchical_episodes_count} episodes`}
                />
                <StatCard
                  label="Semantic Beliefs"
                  value={String(cognitive.tiers.semantic_graph.total_nodes)}
                  sub={`${cognitive.tiers.semantic_graph.active_beliefs} active`}
                />
                <StatCard
                  label="Procedural Skills"
                  value={String(cognitive.tiers.procedural_memory.skills_count)}
                  sub="Reusable recipes"
                />
                <StatCard
                  label="Spatio-Temporal"
                  value={String(cognitive.tiers.spatio_temporal.events_count)}
                  sub="Intervals & timeline"
                />
                <StatCard
                  label="Associative Links"
                  value={String(cognitive.tiers.associative_network.links_count)}
                  sub="Hebbian network"
                />
              </div>

              {/* Sleep / Dream Consolidation Card */}
              <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Moon className="size-4 text-indigo-400" />
                    <span className="text-xs font-semibold">Sleep/Dream Reflection & Consolidation</span>
                  </div>
                  <Btn variant="primary" onClick={onTriggerConsolidation} disabled={actionBusy}>
                    <Sparkles className="size-3.5" /> Run Dream Cycle
                  </Btn>
                </div>

                {cognitive.consolidation.latest_report ? (
                  <div className="rounded-xl bg-muted/40 p-3 space-y-1.5 text-xs">
                    <div className="flex items-center justify-between text-muted-foreground text-[11px]">
                      <span>Cycle ID: {cognitive.consolidation.latest_report.cycle_id}</span>
                      <span>Total Cycles Run: {cognitive.consolidation.total_cycles}</span>
                    </div>
                    <p className="font-medium text-foreground leading-relaxed">
                      {cognitive.consolidation.latest_report.summary}
                    </p>
                    <div className="flex gap-2 flex-wrap pt-1 text-[11px]">
                      <Badge tone="gray">
                        Light Sleep Pruned: {cognitive.consolidation.latest_report.light_sleep_pruned}
                      </Badge>
                      <Badge tone="purple">
                        REM Patterns: {cognitive.consolidation.latest_report.rem_sleep_patterns_discovered}
                      </Badge>
                      <Badge tone="green">
                        Deep Sleep Crystallized: {cognitive.consolidation.latest_report.deep_sleep_beliefs_crystallized}
                      </Badge>
                      <Badge tone="amber">
                        Conflicts Reconciled: {cognitive.consolidation.latest_report.conflicts_reconciled}
                      </Badge>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No sleep consolidation cycle has been triggered yet. Click above to prune noise, synthesize patterns, and crystallize beliefs.
                  </p>
                )}
              </div>
            </div>
          )}

          {tab === "prospective" && (
            <div className="space-y-3" aria-label="Manage reminders and commitments">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold">Reminders &amp; Commitments</h3>
                  <p className="text-xs text-muted-foreground">Alpha captures explicit reminders from chat. Review, complete, or cancel them here.</p>
                </div>
                <Btn variant="ghost" onClick={() => void loadProspective()} disabled={actionBusy}>
                  <RefreshCw className="size-3.5" /> Refresh
                </Btn>
              </div>
              {prospectiveError ? (
                <ErrorBox
                  message={`Reminder data unavailable — this is not an empty list. (${prospectiveError})`}
                  onRetry={loadProspective}
                />
              ) : prospectiveItems.length === 0 ? (
                <EmptyState title="No reminders stored" hint="In chat, say “remind me to…” or “don't let me forget to…” to capture an explicit reminder." />
              ) : (
                <div className="space-y-2">
                  {prospectiveItems.map((item) => {
                    const due = item.due_at === null ? null : new Date(item.due_at * 1000).toLocaleString();
                    const active = item.status === "pending" || item.status === "fired";
                    return (
                      <article key={item.id} className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div className="min-w-0 space-y-1">
                            <p className="text-sm whitespace-pre-wrap break-words">{item.content}</p>
                            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                              <Badge tone="purple">{item.kind}</Badge>
                              <Badge tone={item.status === "pending" ? "blue" : item.status === "fired" ? "amber" : item.status === "done" ? "green" : "gray"}>{item.status}</Badge>
                              <span>Priority {item.priority}</span>
                              {due && <span>Due {due}</span>}
                              {item.kind === "recurring" && <span>Occurrence {item.occurrence}</span>}
                            </div>
                          </div>
                          {active && (
                            <div className="flex shrink-0 gap-2">
                              <Btn onClick={() => void onTransitionProspective(item, "complete")} disabled={actionBusy}>
                                <CheckCircle2 className="size-3.5" /> Complete
                              </Btn>
                              <Btn variant="ghost" onClick={() => void onTransitionProspective(item, "cancel")} disabled={actionBusy}>
                                <Trash2 className="size-3.5" /> Cancel
                              </Btn>
                            </div>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: HYBRID RECALL SIMULATOR */}
          {tab === "recall" && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
                <Field
                  label="Hybrid Recall Query"
                  hint="Fuses BM25 keyword matching, vector similarity, knowledge graph traversal, and temporal recency decay."
                >
                  <div className="flex gap-2">
                    <input
                      value={recallQuery}
                      onChange={(e) => setRecallQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && onRunRecall()}
                      placeholder="Enter query to test multi-tier hybrid recall…"
                      className={inputCls}
                    />
                    <Btn onClick={onRunRecall} disabled={searching || !recallQuery.trim()}>
                      <Search className="size-3.5" /> Recall
                    </Btn>
                  </div>
                </Field>

                {/* Fusion Weights Sliders */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-2 text-[11px]">
                  <div>
                    <div className="flex justify-between mb-1">
                      <span className="text-muted-foreground">BM25 Lexical</span>
                      <span className="font-mono font-semibold">{Math.round(bm25Weight * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={bm25Weight}
                      onChange={(e) => setBm25Weight(parseFloat(e.target.value))}
                      className="w-full accent-primary"
                    />
                  </div>
                  <div>
                    <div className="flex justify-between mb-1">
                      <span className="text-muted-foreground">Vector Semantic</span>
                      <span className="font-mono font-semibold">{Math.round(vectorWeight * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={vectorWeight}
                      onChange={(e) => setVectorWeight(parseFloat(e.target.value))}
                      className="w-full accent-primary"
                    />
                  </div>
                  <div>
                    <div className="flex justify-between mb-1">
                      <span className="text-muted-foreground">Graph Traversal</span>
                      <span className="font-mono font-semibold">{Math.round(graphWeight * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={graphWeight}
                      onChange={(e) => setGraphWeight(parseFloat(e.target.value))}
                      className="w-full accent-primary"
                    />
                  </div>
                  <div>
                    <div className="flex justify-between mb-1">
                      <span className="text-muted-foreground">Temporal Freshness</span>
                      <span className="font-mono font-semibold">{Math.round(temporalWeight * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={temporalWeight}
                      onChange={(e) => setTemporalWeight(parseFloat(e.target.value))}
                      className="w-full accent-primary"
                    />
                  </div>
                </div>
              </div>

              {/* Scored Results */}
              <div className="space-y-2">
                <div className="text-xs font-semibold text-muted-foreground">
                  Scored Memory Hits ({recallResults.length})
                </div>
                {searching ? (
                  <SkeletonList rows={3} />
                ) : recallResults.length === 0 ? (
                  <EmptyState title="No matches found" hint="Try adjusting query terms or increasing weights." />
                ) : (
                  recallResults.map((item) => (
                    <div key={item.item_id} className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs font-semibold text-foreground">{item.title}</span>
                          <Badge tone="blue">{item.tier}</Badge>
                        </div>
                        <div className="flex items-center gap-1.5 text-xs font-mono font-bold text-primary">
                          Score: {(item.composite_score * 100).toFixed(1)}%
                        </div>
                      </div>

                      <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">
                        {item.snippet}
                      </p>

                      <div className="flex gap-2 flex-wrap text-[10px] font-mono text-muted-foreground pt-1">
                        <span>BM25: {(item.bm25_score * 100).toFixed(0)}%</span>
                        <span>Vector: {(item.vector_score * 100).toFixed(0)}%</span>
                        <span>Graph: {(item.graph_score * 100).toFixed(0)}%</span>
                        <span>Temporal: {(item.temporal_score * 100).toFixed(0)}%</span>
                        <span>Salience: {(item.salience_score * 100).toFixed(0)}%</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 3: BELIEF GRAPH */}
          {tab === "beliefs" && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
                <span className="text-xs font-semibold">Add Epistemic Belief to Semantic Graph</span>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <input
                    value={draftSub}
                    onChange={(e) => setDraftSub(e.target.value)}
                    placeholder="Subject (e.g. UserPreferredLanguage)"
                    className={inputCls}
                  />
                  <input
                    value={draftPred}
                    onChange={(e) => setDraftPred(e.target.value)}
                    placeholder="Predicate (e.g. is_configured_as)"
                    className={inputCls}
                  />
                  <input
                    value={draftObj}
                    onChange={(e) => setDraftObj(e.target.value)}
                    placeholder="Object (e.g. TypeScript)"
                    className={inputCls}
                  />
                </div>
                <div className="flex justify-end">
                  <Btn onClick={onAddBelief} disabled={!draftSub.trim() || !draftPred.trim() || !draftObj.trim()}>
                    <Plus className="size-3.5" /> Crystallize Belief
                  </Btn>
                </div>
              </div>

              <div className="space-y-2">
                {beliefsError ? (
                  <ErrorBox
                    message={`Belief graph unavailable — failed to load, not empty. (${beliefsError})`}
                    onRetry={loadBeliefs}
                  />
                ) : semanticNodes.length === 0 ? (
                  <EmptyState title="Belief graph empty" hint="Crystallize beliefs above or run sleep consolidation." />
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    {semanticNodes.map((node) => (
                      <div key={node.node_id} className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-foreground">{node.subject}</span>
                          <div className="flex items-center gap-1.5">
                            <Badge
                              tone={
                                node.status === "active"
                                  ? "green"
                                  : node.status === "contested"
                                  ? "amber"
                                  : node.status === "superseded"
                                  ? "gray"
                                  : "red"
                              }
                            >
                              {node.status}
                            </Badge>
                            <button
                              onClick={() => onDeleteBelief(node.node_id)}
                              className="text-muted-foreground/60 hover:text-red-500 transition-colors p-0.5 rounded"
                              title="Delete belief"
                            >
                              <Trash2 className="size-3" />
                            </button>
                          </div>
                        </div>
                        <p className="text-xs text-muted-foreground font-mono">
                          <span className="text-primary">{node.predicate}</span> &rarr; {node.object_val}
                        </p>
                        <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1">
                          <span>Confidence: {Math.round(node.confidence * 100)}%</span>
                          <span>Revision: #{node.revision}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* TAB 4: PROCEDURAL SKILLS */}
          {tab === "skills" && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
                <span className="text-xs font-semibold">Register Procedural Skill Playbook</span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <input
                    value={draftSkillName}
                    onChange={(e) => setDraftSkillName(e.target.value)}
                    placeholder="Skill name (e.g. run_lint_and_build)"
                    className={inputCls}
                  />
                  <input
                    value={draftSkillTrigger}
                    onChange={(e) => setDraftSkillTrigger(e.target.value)}
                    placeholder="Trigger pattern regex (e.g. (build|compile))"
                    className={inputCls}
                  />
                </div>
                <input
                  value={draftSkillDesc}
                  onChange={(e) => setDraftSkillDesc(e.target.value)}
                  placeholder="Description of what this procedure achieves"
                  className={inputCls}
                />
                <textarea
                  value={draftSkillSteps}
                  onChange={(e) => setDraftSkillSteps(e.target.value)}
                  placeholder="Execution steps (one per line)"
                  rows={3}
                  className={inputCls}
                />
                <div className="flex justify-end">
                  <Btn onClick={onRegisterSkill} disabled={!draftSkillName.trim() || !draftSkillTrigger.trim()}>
                    <Plus className="size-3.5" /> Save Skill Playbook
                  </Btn>
                </div>
              </div>

              <div className="space-y-2">
                {skillsError ? (
                  <ErrorBox
                    message={`Procedural skills unavailable — failed to load, not empty. (${skillsError})`}
                    onRetry={loadSkills}
                  />
                ) : proceduralSkills.length === 0 ? (
                  <EmptyState title="No skills indexed" hint="Register skills above or let sleep reflection discover them." />
                ) : (
                  proceduralSkills.map((sk) => (
                    <div key={sk.skill_id} className="rounded-xl border border-border/60 bg-card p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-foreground">{sk.name}</span>
                        <div className="flex items-center gap-1.5">
                          <Badge tone={sk.success_rate >= 0.8 ? "green" : "amber"}>
                            {Math.round(sk.success_rate * 100)}% Success ({sk.success_count} / {sk.success_count + sk.failure_count})
                          </Badge>
                          <button
                            onClick={() => onDeleteSkill(sk.skill_id)}
                            className="text-muted-foreground/60 hover:text-red-500 transition-colors p-0.5 rounded"
                            title="Delete skill"
                          >
                            <Trash2 className="size-3" />
                          </button>
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground">{sk.description}</p>
                      <div className="text-[11px] font-mono text-primary bg-muted/30 px-2 py-1 rounded-lg">
                        Trigger: {sk.trigger_pattern}
                      </div>
                      {sk.steps && sk.steps.length > 0 && (
                        <ol className="list-decimal list-inside text-xs text-muted-foreground space-y-0.5">
                          {sk.steps.map((st, i) => (
                            <li key={i}>{st}</li>
                          ))}
                        </ol>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 5: WORKING SCRATCHPAD */}
          {tab === "working" && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold">Push to Working Memory Scratchpad</span>
                  <Btn variant="danger" onClick={onClearWorking} disabled={workingItems.length === 0}>
                    <Trash2 className="size-3.5" /> Clear Scratchpad
                  </Btn>
                </div>
                <div className="flex gap-2">
                  <select
                    value={draftWmTag}
                    onChange={(e) => setDraftWmTag(e.target.value)}
                    className="bg-card border border-border/70 rounded-xl px-2.5 py-2 text-xs"
                  >
                    <option value="goal">Goal</option>
                    <option value="hypothesis">Hypothesis</option>
                    <option value="focus">Focus</option>
                    <option value="scratch">Scratch</option>
                  </select>
                  <input
                    value={draftWmContent}
                    onChange={(e) => setDraftWmContent(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && onAddWorking()}
                    placeholder="Enter scratchpad item or active focus…"
                    className={inputCls}
                  />
                  <Btn onClick={onAddWorking} disabled={!draftWmContent.trim()}>
                    <Plus className="size-3.5" /> Push
                  </Btn>
                </div>
              </div>

              <div className="space-y-2">
                {workingError ? (
                  <ErrorBox
                    message={`Working scratchpad unavailable — failed to load, not empty. (${workingError})`}
                    onRetry={loadWorking}
                  />
                ) : workingItems.length === 0 ? (
                  <EmptyState title="Scratchpad empty" hint="Add in-flight working items above." />
                ) : (
                  workingItems.map((item) => (
                    <div key={item.item_id} className="rounded-xl border border-border/60 bg-card p-3 flex items-center justify-between gap-3">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <Badge tone="purple">{item.context_tag}</Badge>
                          <span className="text-xs text-foreground font-medium">{item.content}</span>
                        </div>
                      </div>
                      <div className="text-[11px] font-mono text-muted-foreground whitespace-nowrap">
                        Attention: {Math.round(item.attention_score * 100)}%
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 6: FACTS & PREFERENCES (CLASSIC) */}
          {tab === "facts" && (
            <div className="space-y-4">
              <div className="flex justify-end">
                <Btn variant="danger" onClick={onClearAllFacts}>
                  <Trash2 className="size-3.5" /> Erase all facts
                </Btn>
              </div>

              <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
                <Field label="Teach the agent something new" hint='Example: "I prefer TypeScript over JavaScript" or "Always run unit tests before committing".'>
                  <div className="flex gap-2">
                    <input
                      value={draftFact}
                      onChange={(e) => setDraftFact(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && onAddFact()}
                      placeholder="Type a fact to remember…"
                      className={inputCls}
                      aria-label="New memory fact"
                    />
                    <Btn onClick={onAddFact} disabled={!draftFact.trim()}>
                      <Plus className="size-3.5" /> Remember
                    </Btn>
                  </div>
                </Field>
              </div>

              {!factsError && summary && (
                <div className="rounded-2xl border border-border/60 bg-card p-4">
                  <p className="text-[11px] font-semibold mb-1">Summary</p>
                  <p className="text-xs text-muted-foreground leading-relaxed whitespace-pre-wrap">{summary}</p>
                </div>
              )}

              {factsError ? (
                <ErrorBox
                  message={`Memory facts unavailable — failed to load, not empty. (${factsError})`}
                  onRetry={loadAll}
                />
              ) : facts.length === 0 ? (
                <EmptyState title="No facts stored" hint="The agent learns as you chat, or teach it using the box above." />
              ) : (
                <div className="space-y-2">
                  {facts.map((f) => (
                    <div key={f.id} className="rounded-xl border border-border/60 bg-card px-4 py-2.5 flex items-center gap-2">
                      {editingFact?.id === f.id ? (
                        <>
                          <input
                            value={editingFact.text}
                            onChange={(e) => setEditingFact({ id: editingFact.id, text: e.target.value })}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") onSaveEditFact();
                              if (e.key === "Escape") setEditingFact(null);
                            }}
                            className={inputCls}
                            autoFocus
                          />
                          <Btn onClick={onSaveEditFact}>Save</Btn>
                          <Btn variant="ghost" onClick={() => setEditingFact(null)}>
                            Cancel
                          </Btn>
                        </>
                      ) : (
                        <>
                          <span className="flex-1 text-xs">{f.content}</span>
                          <button
                            type="button"
                            onClick={() => setEditingFact({ id: f.id, text: f.content })}
                            className="text-muted-foreground hover:text-foreground p-1"
                            title="Edit"
                          >
                            <Pencil className="size-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => onDeleteFact(f.id)}
                            className="text-muted-foreground hover:text-destructive p-1"
                            title="Forget"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

/* ── Extraction health (GET /api/memory/status → health) ──────────────── */

/**
 * The badge this surface shows for the extraction pipeline — never invented.
 *
 * `health === null` (backend without the hook, or a read the Gateway could not
 * render) is "no health statement available": a grey, disclosed row, never a
 * green one. An *unknown* status word from a newer server is rendered verbatim
 * in amber rather than treated as one of the known words.
 */
export function extractionHealthBadge(health: MemoryExtractionHealth | null): {
  label: string;
  tone: "green" | "red" | "gray" | "amber";
  title: string;
} {
  if (!health) {
    return {
      label: "extraction health not reported",
      tone: "gray",
      title:
        "The Gateway returned no extraction health statement for this backend — its health hook is absent or unreadable. This is not a claim that extraction is healthy.",
    };
  }
  const prefix = health.reason ? `Gateway reports "extraction ${health.status}": ${health.reason}` : `Gateway reports "extraction ${health.status}"`;
  if (health.status === "ok") {
    return { label: "extraction ok", tone: "green", title: prefix };
  }
  if (health.status === "degraded") {
    return { label: "extraction degraded", tone: "red", title: prefix };
  }
  if (health.status === "disabled") {
    return { label: "extraction disabled", tone: "gray", title: prefix };
  }
  return {
    label: `extraction: ${health.status}`,
    tone: "amber",
    title: `${prefix} — status word from the server, shown verbatim.`,
  };
}

/** A server-absent count is "not reported", never 0. */
export function extractionCountText(value: number | null | undefined): string {
  return typeof value === "number" ? String(value) : "not reported";
}

/** Relative primary stamp through the shared clock vocabulary. */
export function extractionTimeText(value: string | null | undefined): string {
  if (!value) return "not reported";
  return relTime(value) ?? "not reported";
}

/**
 * The extraction-health card.
 *
 * Everything here is what `GET /api/memory/status` sent — counters since the
 * last Gateway restart (the record is process-local by design), the masked,
 * bounded error the server already trimmed, and *nothing* about whether a run
 * succeeded that the server did not report.
 */
export function MemoryHealthCard(props: {
  health: MemoryExtractionHealth | null;
  config: MemoryStatusEnvelope["config"];
}) {
  const { health, config } = props;
  if (!health) {
    return (
      <p className="rounded-xl border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-200">
        This backend did not report extraction health, so the UI cannot say whether memory extraction is working — the answer is unknown, not healthy.
      </p>
    );
  }
  return (
    <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5">
      <p className="text-[11px] font-semibold">Memory extraction health</p>
      <div className="mt-1.5 grid gap-x-4 gap-y-1 text-[11px] text-muted-foreground sm:grid-cols-2">
        <span title="Failures the extraction pipeline recorded since the last Gateway restart (GET /api/memory/status → health)">
          Consecutive failures:{" "}
          <span className={health.consecutive_failures ? "text-destructive font-medium" : "text-foreground"}>{extractionCountText(health.consecutive_failures)}</span>
          {" "}· total {extractionCountText(health.total_failures)}
        </span>
        <span title="Successful extractions recorded since the last Gateway restart">
          Total successes: <span className="text-foreground">{extractionCountText(health.total_successes)}</span>
        </span>
        <span title={absoluteStamp(health.last_attempt_at) ?? undefined}>
          Last attempt: {extractionTimeText(health.last_attempt_at)}
        </span>
        <span title={absoluteStamp(health.last_success_at) ?? undefined}>
          Last success: {extractionTimeText(health.last_success_at)}
        </span>
        {health.last_failure_at ? (
          <span title={absoluteStamp(health.last_failure_at) ?? undefined}>
            Last failure: {extractionTimeText(health.last_failure_at)}
          </span>
        ) : null}
        <span>
          Extraction:{" "}
          {health.extraction_enabled === true ? "on" : health.extraction_enabled === false ? "off" : "unknown"}
        </span>
        {health.model ? (
          <span className="break-all">
            Model: <span className="text-foreground">{health.model}</span>
            {health.model_source ? ` (from ${health.model_source})` : ""}
          </span>
        ) : (
          <span>Model: not reported</span>
        )}
        {health.extraction_enabled === false && ManagerUnavailable(config) ? (
          <span className="sm:col-span-2 text-amber-700 dark:text-amber-300">
            {ManagerUnavailable(config)}
          </span>
        ) : null}
      </div>
      {health.reason ? (
        <p className="mt-1.5 text-[11px] text-muted-foreground">Gateway reason: {health.reason}</p>
      ) : null}
      {health.last_error ? (
        <p className="mt-1.5 break-all rounded-lg border border-destructive/25 bg-destructive/5 px-2 py-1 text-[11px] text-destructive" title={health.last_error}>
          Last error{health.last_error_type ? ` (${health.last_error_type})` : ""}
          {health.last_error_scope ? ` on ${health.last_error_scope}` : ""}: {health.last_error}
        </p>
      ) : null}
      <p className="mt-1.5 border-t border-border/50 pt-1.5 text-[10px] text-muted-foreground">
        Counters cover this Gateway process since its last restart. The shown error text is already truncated and credential-masked by the server before it is sent.
      </p>
    </div>
  );
}

/** A disabled extraction with no resolvable model — the reason, not a guess. */
function ManagerUnavailable(config: MemoryStatusEnvelope["config"]): string | null {
  if (config.manager_class) return `This memory backend (${config.manager_class}) reports extraction disabled — automatic fact extraction is not running.`;
  return "The Gateway reports extraction disabled — automatic fact extraction is not running.";
}
