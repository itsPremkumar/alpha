"use client";

import React, { useEffect, useState } from "react";
import {
  fetchMemory,
  addFact,
  deleteFact,
  updateFact,
  reloadMemory,
  clearMemory,
  memoryStatus,
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

type ActiveTab = "overview" | "recall" | "beliefs" | "skills" | "working" | "facts";

export function MemorySection() {
  const [tab, setTab] = useState<ActiveTab>("overview");

  // Legacy facts state
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [summary, setSummary] = useState("");
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);

  // Cognitive states
  const [cognitive, setCognitive] = useState<CognitiveOverview | null>(null);
  const [workingItems, setWorkingItems] = useState<WorkingMemoryItem[]>([]);
  const [semanticNodes, setSemanticNodes] = useState<SemanticNodeItem[]>([]);
  const [proceduralSkills, setProceduralSkills] = useState<ProceduralSkillItem[]>([]);

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

  const loadAll = async () => {
    setLoading(true);
    setError(null);
    setFactsError(null);
    setOverviewError(null);
    try {
      const [mem, st, cog] = await Promise.all([
        // Failures become explicit per-surface errors — never {}fakes like
        // { facts: [], summary: "" } that would render as a real empty store.
        fetchMemory()
          .then((value) => ({ ok: true as const, value }))
          .catch((e) => ({ ok: false as const, error: errMsg(e) })),
        memoryStatus().catch(() => null),
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
      setStatus(st);
      if (cog.ok) {
        setCognitive(cog.value);
      } else {
        setOverviewError(cog.error);
      }

      // Load specific tab data in background — each failure flags its tab.
      void loadWorking();
      void loadBeliefs();
      void loadSkills();
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
        {status && typeof status.enabled !== "undefined" && (
          <Badge tone={status.enabled ? "green" : "gray"}>gateway memory {status.enabled ? "on" : "off"}</Badge>
        )}
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
      </div>

      {loading ? (
        <SkeletonList rows={4} />
      ) : (
        <>
          {/* TAB 1: COGNITIVE OVERVIEW */}
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
