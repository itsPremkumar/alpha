"use client";

import React, { useEffect, useState } from "react";
import {
  Settings,
  Server,
  Cpu,
  Palette,
  Shield,
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Sparkles,
  Layers,
  Database,
  Terminal,
  Activity,
  Zap,
} from "lucide-react";
import { AIModel } from "@/types/chat";
import { fetchAvailableModels } from "@/lib/api";
import { fetchOpsStatus, fetchOpsVersion, fetchFeatures, FeatureFlags, fetchEvolutionIdentity, EvolutionIdentity } from "@/lib/workspace";
import { probeAll, Probe } from "@/lib/system";
import { fetchIntegrationHealth, IntegrationHealth } from "@/lib/integration";
import { Section, StatCard, Badge, Btn, Field, inputCls, ErrorBox, Notice, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { branding } from "@/lib/branding";

interface SettingsSectionProps {
  currentModel?: string;
  onModelChange?: (modelId: string) => void;
  onOpenView?: (viewId: any) => void;
}

export function SettingsSection({ currentModel = "default", onModelChange, onOpenView }: SettingsSectionProps) {
  const [activeTab, setActiveTab] = useState<"general" | "models" | "connectivity" | "appearance" | "diagnostics">("general");

  // Models state
  const [models, setModels] = useState<AIModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>(currentModel);
  const [modelsLoading, setModelsLoading] = useState(true);

  useEffect(() => {
    if (currentModel) {
      setSelectedModel(currentModel);
    }
  }, [currentModel]);

  // System & connectivity state
  const [probes, setProbes] = useState<Probe[]>([]);
  const [probing, setProbing] = useState(true);
  const [opsVersion, setOpsVersion] = useState<string>("unknown");
  const [identity, setIdentity] = useState<EvolutionIdentity | null>(null);
  const [features, setFeatures] = useState<FeatureFlags | null>(null);
  const [integrationHealth, setIntegrationHealth] = useState<IntegrationHealth | null>(null);

  // Appearance & Preferences (persisted in localStorage)
  const [themeMode, setThemeMode] = useState<string>("dark");
  const [autoSuggestions, setAutoSuggestions] = useState<boolean>(true);
  const [streamSpeed, setStreamSpeed] = useState<string>("normal");
  const [compactDensity, setCompactDensity] = useState<boolean>(false);
  const [codeHighlightTheme, setCodeHighlightTheme] = useState<string>("github-dark");

  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const flash = (msg: string) => {
    setNotice(msg);
    window.setTimeout(() => setNotice(null), 4000);
  };

  // Load preferences from localStorage
  useEffect(() => {
    try {
      const savedTheme = localStorage.getItem("alpha_theme_mode") || "dark";
      const savedDensity = localStorage.getItem("alpha_density") === "compact";
      const savedSugg = localStorage.getItem("alpha_suggestions_auto") !== "false";
      const savedModel = localStorage.getItem("alpha_selected_model");
      setThemeMode(savedTheme);
      setCompactDensity(savedDensity);
      setAutoSuggestions(savedSugg);
      if (savedModel) {
        setSelectedModel(savedModel);
      }
    } catch {
      /* ignore storage access restrictions */
    }
  }, []);

  const handleThemeChange = (mode: string) => {
    setThemeMode(mode);
    try {
      localStorage.setItem("alpha_theme_mode", mode);
      if (typeof document !== "undefined") {
        const root = document.documentElement;
        if (mode === "light") {
          root.classList.remove("dark");
        } else {
          root.classList.add("dark");
        }
      }
    } catch {
      /* ignore */
    }
    flash(`Theme switched to ${mode} mode.`);
  };

  const handleDensityChange = (compact: boolean) => {
    setCompactDensity(compact);
    try {
      localStorage.setItem("alpha_density", compact ? "compact" : "normal");
    } catch {
      /* ignore */
    }
    flash(`Interface density set to ${compact ? "Compact" : "Comfortable"}.`);
  };

  const loadData = async () => {
    setProbing(true);
    setError(null);
    try {
      const [mList, pList, v, f, ih, id] = await Promise.all([
        fetchAvailableModels().catch(() => []),
        probeAll().catch(() => []),
        fetchOpsVersion().catch(() => "unknown"),
        fetchFeatures().catch(() => null),
        fetchIntegrationHealth().catch(() => null),
        fetchEvolutionIdentity().catch(() => null),
      ]);
      setModels(mList);
      setProbes(pList);
      setOpsVersion(v);
      setFeatures(f);
      setIntegrationHealth(ih);
      setIdentity(id);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setProbing(false);
      setModelsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const gatewayProbe = probes.find((p) => p.key === "gateway");
  const isOnline = Boolean(gatewayProbe?.ok);
  const onlineCount = probes.filter((p) => p.ok === true).length;
  const updateState = identity?.updateState ?? "unknown";
  const updateBadgeTone =
    updateState === "UPDATE_AVAILABLE"
      ? "amber"
      : updateState === "UP_TO_DATE"
        ? "green"
        : updateState === "CHECK_FAILED"
          ? "red"
          : "gray";

  return (
    <Section
      title="Settings & Workspace Preferences"
      hint="Control LLM routing, theme appearance, Gateway connectivity, feature flags, and advanced system diagnostics."
      actions={
        <div className="flex items-center gap-2">
          <Badge tone={isOnline ? "green" : "red"}>
            <Server className="size-3" />
            {isOnline ? "Connected (HTTP 200)" : "Offline / Unreachable"}
          </Badge>
          <Btn variant="ghost" onClick={loadData} disabled={probing}>
            <RefreshCw className={`size-3.5 ${probing ? "animate-spin" : ""}`} />
            Refresh status
          </Btn>
        </div>
      }
    >
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={error} onRetry={loadData} />}

      {/* Tabs */}
      <div className="flex border-b border-border/70 pb-px gap-1 overflow-x-auto">
        {[
          { id: "general", label: "General & Identity", icon: <Settings className="size-3.5" /> },
          { id: "models", label: "Model Selection", icon: <Cpu className="size-3.5" /> },
          { id: "connectivity", label: "API & Subsystems", icon: <Server className="size-3.5" /> },
          { id: "appearance", label: "Appearance & UX", icon: <Palette className="size-3.5" /> },
          { id: "diagnostics", label: "Diagnostics & Health", icon: <Activity className="size-3.5" /> },
        ].map((tab) => {
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id as any)}
              className={`inline-flex items-center gap-2 px-3.5 py-2 text-xs font-semibold rounded-t-xl transition-colors border-b-2 ${
                active
                  ? "border-primary text-primary bg-card/60"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/40"
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* TAB CONTENT: GENERAL */}
      {activeTab === "general" && (
        <div className="space-y-4 pt-1">
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Workspace Profile</h3>
                <p className="text-xs text-muted-foreground mt-0.5">Instance identity and application metadata</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone="blue">v{opsVersion}</Badge>
                <Badge tone={updateBadgeTone}>{updateState}</Badge>
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
              <div className="p-3 rounded-xl bg-muted/40 border border-border/40">
                <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider block">Platform Name</span>
                <span className="text-sm font-bold text-foreground mt-0.5 block">{branding.name}</span>
                <span className="text-xs text-muted-foreground mt-1 block">{branding.description}</span>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 border border-border/40">
                <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider block">Runtime Architecture</span>
                <span className="text-sm font-bold text-foreground mt-0.5 block">LangGraph + FastAPI Super-Agent</span>
                <span className="text-xs text-muted-foreground mt-1 block">Full-stack multi-bot sandboxed orchestration</span>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 border border-border/40">
                <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider block">Canonical Repository</span>
                <span className="text-sm font-bold text-foreground mt-0.5 block break-all">{identity?.repositoryUrl ?? "unknown"}</span>
                <span className="text-xs text-muted-foreground mt-1 block">
                  {`Branch ${identity?.repositoryBranch ?? "unknown"} (source of truth) · Channel ${identity?.releaseChannel ?? "unknown"}`}
                </span>
              </div>
              <div className="p-3 rounded-xl bg-muted/40 border border-border/40">
                <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider block">Instance & Update</span>
                <span className="text-sm font-bold text-foreground mt-0.5 block">{identity?.agentId ?? "unknown"}</span>
                <span className="text-xs text-muted-foreground mt-1 block">
                  {`v${identity?.alphaVersion ?? "unknown"} · commit ${identity?.gitCommit ?? "unknown"} · update ${updateState}`}
                </span>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">Active Feature Flags</h3>
            <p className="text-xs text-muted-foreground">Capabilities reported live from the backend Gateway</p>
            {features ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-1">
                <div className="flex items-center justify-between p-2.5 rounded-xl bg-muted/30 border border-border/40">
                  <div className="flex items-center gap-2">
                    <Sparkles className="size-4 text-primary" />
                    <div>
                      <span className="text-xs font-semibold block">Custom Agents API</span>
                      <span className="text-[10px] text-muted-foreground block">Dynamic persona generation</span>
                    </div>
                  </div>
                  <Badge tone={features.agentsApi ? "green" : "gray"}>{features.agentsApi ? "Enabled" : "Disabled"}</Badge>
                </div>

                <div className="flex items-center justify-between p-2.5 rounded-xl bg-muted/30 border border-border/40">
                  <div className="flex items-center gap-2">
                    <Zap className="size-4 text-amber-500" />
                    <div>
                      <span className="text-xs font-semibold block">Browser Live Control</span>
                      <span className="text-[10px] text-muted-foreground block">Automated web exploration</span>
                    </div>
                  </div>
                  <Badge tone={features.browserControl ? "green" : "gray"}>{features.browserControl ? "Enabled" : "Disabled"}</Badge>
                </div>

                <div className="flex items-center justify-between p-2.5 rounded-xl bg-muted/30 border border-border/40">
                  <div className="flex items-center gap-2">
                    <Terminal className="size-4 text-emerald-500" />
                    <div>
                      <span className="text-xs font-semibold block">Durable MCP Tasks</span>
                      <span className="text-[10px] text-muted-foreground block">Model Context Protocol jobs</span>
                    </div>
                  </div>
                  <Badge tone={features.mcpTasks ? "green" : "gray"}>{features.mcpTasks ? "Enabled" : "Disabled"}</Badge>
                </div>

                <div className="flex items-center justify-between p-2.5 rounded-xl bg-muted/30 border border-border/40">
                  <div className="flex items-center gap-2">
                    <Layers className="size-4 text-indigo-500" />
                    <div>
                      <span className="text-xs font-semibold block">Subagent Batches</span>
                      <span className="text-[10px] text-muted-foreground block">Parallel background execution</span>
                    </div>
                  </div>
                  <Badge tone={features.subagentBatches ? "green" : "gray"}>{features.subagentBatches ? "Enabled" : "Disabled"}</Badge>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic">Feature flags currently unreachable from the Gateway.</p>
            )}
          </div>
        </div>
      )}

      {/* TAB CONTENT: MODELS */}
      {activeTab === "models" && (
        <div className="space-y-4 pt-1">
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Model Configuration & Routing</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Choose the default LLM reasoning engine for new chat threads and multi-turn agent runs.
                </p>
              </div>
              <Btn variant="ghost" onClick={loadData}>
                <RefreshCw className="size-3.5" /> Refresh Models
              </Btn>
            </div>

            {modelsLoading ? (
              <SkeletonList rows={3} />
            ) : models.length === 0 ? (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-700 dark:text-amber-300">
                <p className="font-semibold">No remote models currently exposed by Gateway.</p>
                <p className="mt-1 text-muted-foreground">
                  The system will fall back to server-configured default model. Verify your <code className="font-mono">config.yaml</code> and API keys.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
                {models.map((m) => {
                  const isSelected = selectedModel === m.id;
                  return (
                    <div
                      key={m.id}
                      onClick={() => {
                        setSelectedModel(m.id);
                        try {
                          localStorage.setItem("alpha_selected_model", m.id);
                        } catch {}
                        if (onModelChange) onModelChange(m.id);
                        flash(`Switched primary model to ${m.name}`);
                      }}
                      className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                        isSelected
                          ? "border-primary bg-primary/5 ring-1 ring-primary shadow-sm"
                          : "border-border/60 bg-muted/20 hover:bg-muted/50"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-foreground">{m.name}</span>
                        <Badge tone={isSelected ? "blue" : "gray"}>{m.provider}</Badge>
                      </div>
                      <p className="text-[11px] text-muted-foreground mt-1 font-mono truncate">{m.id}</p>
                      {m.description && <p className="text-xs text-muted-foreground mt-2 line-clamp-2">{m.description}</p>}
                      <div className="mt-3 flex items-center justify-between text-[10px]">
                        <span className="text-muted-foreground">
                          {isSelected ? "Active reasoning model" : "Click to select"}
                        </span>
                        {isSelected && <CheckCircle2 className="size-4 text-primary" />}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB CONTENT: CONNECTIVITY & APIS */}
      {activeTab === "connectivity" && (
        <div className="space-y-4 pt-1">
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Gateway & Router Connectivity</h3>
                <p className="text-xs text-muted-foreground mt-0.5">Real-time status of backend service endpoints</p>
              </div>
              <Badge tone={isOnline ? "green" : "red"}>{onlineCount}/{probes.length} subsystems online</Badge>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 pt-2">
              {probes.map((p) => (
                <div key={p.key} className="p-3 rounded-xl bg-muted/30 border border-border/50 space-y-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-foreground">{p.label}</span>
                    <Badge tone={p.ok ? "green" : p.ok === null ? "gray" : "red"}>
                      {p.ok ? "Healthy" : p.ok === null ? "Checking" : "Offline"}
                    </Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2">{p.blurb}</p>
                  <div className="flex items-center justify-between text-[10px] text-muted-foreground font-mono pt-1 border-t border-border/40">
                    <span className="truncate max-w-[140px]">{p.detail}</span>
                    <span>{p.ms ? `${p.ms}ms` : "—"}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-2">
            <h3 className="text-sm font-semibold text-foreground">API Routing Overview</h3>
            <p className="text-xs text-muted-foreground">Gateway routes exposed through the reverse-proxy:</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2 text-xs font-mono pt-2">
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/threads</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">Runs, messages & history</span>
              </div>
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/models</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">Model catalog & quota</span>
              </div>
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/commands</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">Master slash engine</span>
              </div>
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/mcp</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">External MCP tools & servers</span>
              </div>
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/system</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">Live host RAM, CPU, GPU</span>
              </div>
              <div className="p-2 rounded-lg bg-muted/40 border border-border/40">
                <span className="text-primary font-semibold">/api/ops</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">Ops health & manifest</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB CONTENT: APPEARANCE & UX */}
      {activeTab === "appearance" && (
        <div className="space-y-4 pt-1">
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-foreground">Theme & Visual Styling</h3>
              <p className="text-xs text-muted-foreground mt-0.5">Customize the color scheme and appearance</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div
                onClick={() => handleThemeChange("dark")}
                className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                  themeMode === "dark"
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border/60 bg-muted/30 hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-foreground">Dark Theme (Default)</span>
                  {themeMode === "dark" && <CheckCircle2 className="size-4 text-primary" />}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1">High-contrast dark mode tailored for developer productivity</p>
              </div>

              <div
                onClick={() => handleThemeChange("light")}
                className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                  themeMode === "light"
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border/60 bg-muted/30 hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-foreground">Light Theme</span>
                  {themeMode === "light" && <CheckCircle2 className="size-4 text-primary" />}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1">Crisp light mode with clear typography and soft borders</p>
              </div>
            </div>

            <div className="pt-2 border-t border-border/50 space-y-3">
              <h4 className="text-xs font-semibold text-foreground">Interface Density & UX Flow</h4>
              <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30 border border-border/40">
                <div>
                  <span className="text-xs font-semibold block">Compact Density</span>
                  <span className="text-[11px] text-muted-foreground block">Tighter line heights and padding across views</span>
                </div>
                <input
                  type="checkbox"
                  checked={compactDensity}
                  onChange={(e) => handleDensityChange(e.target.checked)}
                  className="size-4 text-primary rounded border-border focus:ring-primary/40 cursor-pointer"
                />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30 border border-border/40">
                <div>
                  <span className="text-xs font-semibold block">AI Follow-up Suggestions</span>
                  <span className="text-[11px] text-muted-foreground block">Automatically draft context-aware suggestions after responses</span>
                </div>
                <input
                  type="checkbox"
                  checked={autoSuggestions}
                  onChange={(e) => {
                    setAutoSuggestions(e.target.checked);
                    try {
                      localStorage.setItem("alpha_suggestions_auto", e.target.checked ? "true" : "false");
                    } catch {}
                    flash(`AI follow-up suggestions ${e.target.checked ? "enabled" : "disabled"}.`);
                  }}
                  className="size-4 text-primary rounded border-border focus:ring-primary/40 cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB CONTENT: DIAGNOSTICS & SYSTEM */}
      {activeTab === "diagnostics" && (
        <div className="space-y-4 pt-1">
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Integration Coverage Manifest</h3>
                <p className="text-xs text-muted-foreground mt-0.5">System-wide wiring proofs and capability statistics</p>
              </div>
              {onOpenView && (
                <Btn variant="ghost" onClick={() => onOpenView("integration")}>
                  Open Full Integration View
                </Btn>
              )}
            </div>

            {integrationHealth ? (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2">
                {Object.entries(integrationHealth.coverage).map(([k, v]) => (
                  <div key={k} className="p-3 rounded-xl bg-muted/30 border border-border/40">
                    <span className="text-[10px] uppercase font-semibold text-muted-foreground block">{k}</span>
                    <div className="mt-1 flex items-baseline gap-1">
                      <span className="text-base font-bold text-foreground">{v.wired}</span>
                      <span className="text-xs text-muted-foreground">/ {v.total}</span>
                    </div>
                    <span className="text-[10px] text-emerald-600 dark:text-emerald-400 block mt-0.5">
                      {v.total > 0 ? `${Math.round((v.wired / v.total) * 100)}% wired` : "No entries"}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic">Manifest details currently unavailable.</p>
            )}
          </div>

          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">Troubleshooting & Cache Reset</h3>
            <p className="text-xs text-muted-foreground">Quick actions to recover from stale local state or broken sessions</p>
            <div className="flex items-center gap-2 flex-wrap pt-1">
              <Btn
                variant="ghost"
                onClick={() => {
                  if (typeof window !== "undefined") {
                    window.location.reload();
                  }
                }}
              >
                <RefreshCw className="size-3.5" /> Hard Reload Web App
              </Btn>
              <Btn
                variant="ghost"
                onClick={() => {
                  try {
                    localStorage.removeItem("alpha_history");
                    flash("Cleared local temporary thread cache.");
                  } catch (e) {
                    flash("Unable to clear local cache.");
                  }
                }}
              >
                Clear Local Session Cache
              </Btn>
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}
