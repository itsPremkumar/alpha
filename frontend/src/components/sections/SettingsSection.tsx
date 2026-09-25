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
  Key,
  ExternalLink,
  Plus,
  Trash2,
  Eye,
  EyeOff,
  Search,
  ArrowLeft,
} from "lucide-react";
import { AIModel } from "@/types/chat";
import {
  fetchAvailableModels,
  fetchProvidersCatalog,
  configureProviderCredentials,
  probeFreeModels,
  LLMProviderCatalogItem,
  BUILTIN_FREE_MODELS,
} from "@/lib/api";
import { fetchOpsStatus, fetchOpsVersion, fetchFeatures, FeatureFlags, fetchEvolutionIdentity, EvolutionIdentity } from "@/lib/workspace";
import { probeAll, Probe } from "@/lib/system";
import { applyThemeMode, isThemeMode, THEME_STORAGE_KEY } from "@/lib/theme";
import { fetchIntegrationHealth, IntegrationHealth } from "@/lib/integration";
import { Section, StatCard, Badge, Btn, Field, inputCls, ErrorBox, Notice, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { branding } from "@/lib/branding";
import { getCapabilities, type CapabilitiesReport } from "@/lib/multimodal";
import { readAutoplayEnabled, writeAutoplayEnabled } from "@/lib/voice";
import { checkForEvolutionUpdate, getEvolutionUpdateState, requestEvolutionUpdate, type EvolutionUpdateState } from "@/lib/evolution";

const FALLBACK_PROVIDER_CATALOG: LLMProviderCatalogItem[] = [
  {
    id: "gemini",
    name: "Google AI Studio (Gemini)",
    category: "recurring_free",
    key_env: "GEMINI_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://aistudio.google.com/apikey",
    free_tier_note: "Recurring $0 free tier: 15 RPM / 1,500 requests per day with Gemini 2.5 Flash & Pro.",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    default_models: [
      { id: "gemini-2.5-flash", name: "Gemini 2.5 Flash", model_id: "gemini-2.5-flash", supports_thinking: true },
      { id: "gemini-2.5-flash-lite", name: "Gemini 2.5 Flash-Lite", model_id: "gemini-2.5-flash-lite", supports_thinking: false },
      { id: "gemini-2.0-pro-exp-02-05", name: "Gemini 2.0 Pro Exp", model_id: "gemini-2.0-pro-exp-02-05", supports_thinking: true },
    ],
  },
  {
    id: "groq",
    name: "GroqCloud",
    category: "recurring_free",
    key_env: "GROQ_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://console.groq.com/keys",
    free_tier_note: "Recurring $0 free quota: 30 RPM / 14,400 requests/day at 500+ tokens/sec on LPUs.",
    base_url: "https://api.groq.com/openai/v1",
    default_models: [
      { id: "groq-llama-3.3-70b", name: "LLaMA 3.3 70B Versatile (Groq)", model_id: "llama-3.3-70b-versatile" },
      { id: "groq-deepseek-r1-70b", name: "DeepSeek R1 Distill 70B (Groq)", model_id: "deepseek-r1-distill-llama-70b", supports_thinking: true },
      { id: "groq-llama-3.1-8b", name: "LLaMA 3.1 8B Instant (Groq)", model_id: "llama-3.1-8b-instant" },
    ],
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    category: "free_gateway",
    key_env: "OPENROUTER_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://openrouter.ai/keys",
    free_tier_note: "Single API key unlocks Union Alpha and 25+ rotating :free models at $0 cost.",
    base_url: "https://openrouter.ai/api/v1",
    default_models: [
      { id: "union-alpha", name: "Union Alpha", model_id: "stealth/union-alpha", supports_thinking: true },
      { id: "openrouter-free-llama-70b", name: "LLaMA 3.3 70B :free", model_id: "meta-llama/llama-3.3-70b-instruct:free" },
      { id: "openrouter-free-deepseek-r1", name: "DeepSeek R1 :free", model_id: "deepseek/deepseek-r1:free", supports_thinking: true },
    ],
  },
  {
    id: "sambanova",
    name: "SambaNova Cloud",
    category: "recurring_free",
    key_env: "SAMBANOVA_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://cloud.sambanova.ai/apis",
    free_tier_note: "Recurring $0 developer tier on ultra-fast SN40L Reconfigurable Dataflow Units.",
    base_url: "https://api.sambanova.ai/v1",
    default_models: [
      { id: "sambanova-llama-3.3-70b", name: "LLaMA 3.3 70B (SambaNova)", model_id: "Meta-Llama-3.3-70B-Instruct" },
      { id: "sambanova-deepseek-r1", name: "DeepSeek R1 (SambaNova)", model_id: "DeepSeek-R1", supports_thinking: true },
    ],
  },
  {
    id: "mistral",
    name: "Mistral La Plateforme",
    category: "recurring_free",
    key_env: "MISTRAL_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://console.mistral.ai/api-keys/",
    free_tier_note: "Free Experiment tier with Codestral, Mistral Small, and Nemo.",
    base_url: "https://api.mistral.ai/v1",
    default_models: [
      { id: "mistral-codestral", name: "Codestral (Mistral)", model_id: "codestral-latest" },
      { id: "mistral-small", name: "Mistral Small (Mistral)", model_id: "mistral-small-latest" },
    ],
  },
  {
    id: "cohere",
    name: "Cohere Coral",
    category: "recurring_free",
    key_env: "COHERE_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://dashboard.cohere.com/api-keys",
    free_tier_note: "Free Trial Key with 1,000 API calls/month.",
    base_url: "https://api.cohere.ai/compatibility/v1",
    default_models: [
      { id: "cohere-command-r-plus", name: "Command R+ (Cohere)", model_id: "command-r-plus-08-2024" },
    ],
  },
  {
    id: "cloudflare",
    name: "Cloudflare Workers AI",
    category: "recurring_free",
    key_env: "CLOUDFLARE_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://dash.cloudflare.com/",
    free_tier_note: "10,000 free neurons/day across Cloudflare global edge network.",
    base_url: "https://api.cloudflare.com/client/v4/accounts/default/ai/v1",
    default_models: [
      { id: "cf-llama-3.3-70b", name: "LLaMA 3.3 70B FP8 (Cloudflare)", model_id: "@cf/meta/llama-3.3-70b-instruct-fp8-fast" },
    ],
  },
  {
    id: "nvidia",
    name: "NVIDIA NIM",
    category: "trial_credits",
    key_env: "NVIDIA_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://build.nvidia.com/",
    free_tier_note: "1,000 free inference API credits on NVIDIA enterprise DGX cloud.",
    base_url: "https://integrate.api.nvidia.com/v1",
    default_models: [
      { id: "nvidia-nemotron-70b", name: "Nemotron 70B (NVIDIA NIM)", model_id: "nvidia/llama-3.1-nemotron-70b-instruct" },
      { id: "nvidia-deepseek-r1", name: "DeepSeek R1 (NVIDIA NIM)", model_id: "deepseek-ai/deepseek-r1", supports_thinking: true },
    ],
  },
  {
    id: "cerebras",
    name: "Cerebras Cloud",
    category: "trial_credits",
    key_env: "CEREBRAS_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://cloud.cerebras.ai/",
    free_tier_note: "1 Million free tokens/day on wafer-scale inference engine (2,000+ t/s).",
    base_url: "https://api.cerebras.ai/v1",
    default_models: [
      { id: "cerebras-llama-3.3-70b", name: "LLaMA 3.3 70B (Cerebras)", model_id: "llama-3.3-70b" },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    category: "paid",
    key_env: "OPENAI_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://platform.openai.com/api-keys",
    free_tier_note: "Official OpenAI commercial platform (GPT-4o, GPT-4o-mini, o3-mini).",
    base_url: "https://api.openai.com/v1",
    default_models: [
      { id: "gpt-4o", name: "GPT-4o", model_id: "gpt-4o" },
      { id: "gpt-4o-mini", name: "GPT-4o mini", model_id: "gpt-4o-mini" },
      { id: "o3-mini", name: "o3-mini", model_id: "o3-mini", supports_thinking: true },
    ],
  },
  {
    id: "anthropic",
    name: "Anthropic Claude",
    category: "paid",
    key_env: "ANTHROPIC_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://console.anthropic.com/settings/keys",
    free_tier_note: "Official Anthropic API (Claude 3.7 Sonnet with Hybrid Thinking, Claude 3.5 Sonnet).",
    base_url: "https://api.anthropic.com",
    default_models: [
      { id: "claude-3-7-sonnet", name: "Claude 3.7 Sonnet", model_id: "claude-3-7-sonnet-latest", supports_thinking: true },
      { id: "claude-3-5-sonnet", name: "Claude 3.5 Sonnet", model_id: "claude-3-5-sonnet-latest" },
    ],
  },
  {
    id: "deepseek",
    name: "DeepSeek Official",
    category: "paid",
    key_env: "DEEPSEEK_API_KEY",
    configured: false,
    masked_key: null,
    portal_url: "https://platform.deepseek.com/api_keys",
    free_tier_note: "Direct high-concurrency DeepSeek V3 and R1 reasoning endpoints.",
    base_url: "https://api.deepseek.com/v1",
    default_models: [
      { id: "deepseek-chat", name: "DeepSeek V3", model_id: "deepseek-chat" },
      { id: "deepseek-reasoner", name: "DeepSeek R1", model_id: "deepseek-reasoner", supports_thinking: true },
    ],
  },
  {
    id: "ovhcloud",
    name: "OVHcloud AI Endpoints",
    category: "keyless_free",
    key_env: null,
    configured: true,
    masked_key: null,
    portal_url: "https://endpoints.kepler.ai.cloud.ovh.net/",
    free_tier_note: "100% Free European sovereign endpoints. No API key required.",
    base_url: "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
    default_models: [
      { id: "free:ovhcloud:Meta-Llama-3_3-70B-Instruct", name: "LLaMA 3.3 70B (OVHcloud Free)", model_id: "Meta-Llama-3_3-70B-Instruct" },
      { id: "free:ovhcloud:Qwen3-Coder-30B-A3B-Instruct", name: "Qwen3 Coder 30B (OVHcloud Free)", model_id: "Qwen3-Coder-30B-A3B-Instruct" },
      { id: "free:ovhcloud:Mistral-7B-Instruct-v0.3", name: "Mistral 7B v0.3 (OVHcloud Free)", model_id: "Mistral-7B-Instruct-v0.3" },
    ],
  },
  {
    id: "pollinations",
    name: "Pollinations AI",
    category: "keyless_free",
    key_env: null,
    configured: true,
    masked_key: null,
    portal_url: "https://pollinations.ai",
    free_tier_note: "Instant public AI router with high concurrency. Zero auth required.",
    base_url: "https://text.pollinations.ai",
    default_models: [
      { id: "free:pollinations:openai-fast", name: "OpenAI Fast (Pollinations Free)", model_id: "openai-fast" },
      { id: "free:pollinations:openai", name: "OpenAI Standard (Pollinations Free)", model_id: "openai" },
    ],
  },
  {
    id: "llm7",
    name: "LLM7 Gateway",
    category: "keyless_free",
    key_env: null,
    configured: true,
    masked_key: null,
    portal_url: "https://llm7.io",
    free_tier_note: "Free community router with Codestral and Mistral-Nemo.",
    base_url: "https://api.llm7.io/v1",
    default_models: [
      { id: "free:llm7:codestral-latest", name: "Codestral Latest (LLM7 Free)", model_id: "codestral-latest" },
      { id: "free:llm7:mistral-Nemo-Instruct-2407", name: "Mistral Nemo 12B (LLM7 Free)", model_id: "mistral-Nemo-Instruct-2407" },
    ],
  },
];

/** Observed probe-status badge styling for the Voice & Speakers engine matrix. */
function probeBadge(status: string): string {
  if (status === "available") return "bg-emerald-500/10 text-emerald-400 border-emerald-500/30";
  if (status === "not_configured") return "bg-amber-500/10 text-amber-400 border-amber-500/30";
  if (status === "not_installed") return "bg-red-500/10 text-red-400 border-red-500/30";
  if (status === "probe_failed") return "bg-red-500/10 text-red-400 border-red-500/30";
  return "bg-muted text-muted-foreground border-border";
}

interface SettingsSectionProps {
  initialTab?: "general" | "models" | "connectivity" | "appearance" | "diagnostics";
  currentModel?: string;
  onModelChange?: (modelId: string) => void;
  onModelsUpdated?: () => Promise<void> | void;
  onOpenView?: (viewId: any) => void;
}

export function SettingsSection({
  initialTab = "general",
  currentModel = "default",
  onModelChange,
  onModelsUpdated,
  onOpenView,
}: SettingsSectionProps) {
  const [activeTab, setActiveTab] = useState<"general" | "models" | "connectivity" | "appearance" | "diagnostics">(initialTab);

  useEffect(() => {
    if (initialTab) {
      setActiveTab(initialTab);
    }
  }, [initialTab]);

  // Models & Providers state
  const [models, setModels] = useState<AIModel[]>(BUILTIN_FREE_MODELS);
  const [providersCatalog, setProvidersCatalog] = useState<LLMProviderCatalogItem[]>(FALLBACK_PROVIDER_CATALOG);
  const [selectedModel, setSelectedModel] = useState<string>(currentModel);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelFilter, setModelFilter] = useState<"all" | "keyless" | "quota" | "paid">("all");
  const [modelSearch, setModelSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<"all" | "recurring_free" | "free_gateway" | "trial_credits" | "paid" | "keyless_free">("all");
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [savingProvider, setSavingProvider] = useState<string | null>(null);
  const [probingFree, setProbingFree] = useState(false);
  const [probeSummary, setProbeSummary] = useState<string | null>(null);

  // Custom model state
  const [customModelId, setCustomModelId] = useState("");
  const [customDisplayName, setCustomDisplayName] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("http://127.0.0.1:11434/v1");
  const [customApiKey, setCustomApiKey] = useState("");
  const [savingCustom, setSavingCustom] = useState(false);

  useEffect(() => {
    if (currentModel) {
      setSelectedModel(currentModel);
    }
  }, [currentModel]);

  // Voice & Speakers: observed multimodal capabilities + local autoplay preference.
  const [voiceReport, setVoiceReport] = useState<CapabilitiesReport | null>(null);
  const [voiceReportError, setVoiceReportError] = useState<string | null>(null);
  const [voiceAutoplay, setVoiceAutoplay] = useState(false);

  useEffect(() => {
    setVoiceAutoplay(readAutoplayEnabled());
    let alive = true;
    getCapabilities()
      .then((report) => {
        if (alive) setVoiceReport(report);
      })
      .catch((err: unknown) => {
        if (alive) setVoiceReportError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  // System & connectivity state
  const [probes, setProbes] = useState<Probe[]>([]);
  const [probing, setProbing] = useState(true);
  // True only when the probe request itself failed - the UI must never render
  // a fabricated "0/0 subsystems online" for a failed check.
  const [probesUnavailable, setProbesUnavailable] = useState<boolean>(false);
  const [opsVersion, setOpsVersion] = useState<string>("unknown");
  const [identity, setIdentity] = useState<EvolutionIdentity | null>(null);
  const [updateDetails, setUpdateDetails] = useState<EvolutionUpdateState | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [features, setFeatures] = useState<FeatureFlags | null>(null);
  const [integrationHealth, setIntegrationHealth] = useState<IntegrationHealth | null>(null);

  // Appearance & Preferences (persisted in localStorage).
  // Initial "system": the system-preference default wins until the mount
  // effect below loads an explicit user choice — never a phantom "dark".
  const [themeMode, setThemeMode] = useState<string>("system");
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

  // Load preferences from localStorage - theme resolves exactly the way
  // ThemeController does (stored value, else "system"), so the checked card
  // always matches what is actually applied to the document.
  useEffect(() => {
    try {
      const storedTheme = localStorage.getItem(THEME_STORAGE_KEY);
      setThemeMode(isThemeMode(storedTheme) ? storedTheme : "system");
      const savedDensity = localStorage.getItem("alpha_density") === "compact";
      const savedSugg = localStorage.getItem("alpha_suggestions_auto") !== "false";
      const savedModel = localStorage.getItem("alpha_selected_model");
      setCompactDensity(savedDensity);
      setAutoSuggestions(savedSugg);
      if (savedModel) {
        setSelectedModel(savedModel);
      }
    } catch {
      /* storage unavailable - ThemeController still applies "system" */
      setThemeMode("system");
    }
  }, []);

  const handleThemeChange = (mode: string) => {
    const nextMode = isThemeMode(mode) ? mode : "system";
    setThemeMode(nextMode);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, nextMode);
    } catch {
      /* ignore */
    }
    // Single source of truth: the shared resolver handles system-preference
    // resolution, the classList toggle AND colorScheme - the old hand-rolled
    // toggle skipped both, fighting the theme system's default.
    applyThemeMode(nextMode);
    flash(`Theme switched to ${nextMode === "system" ? "System (match OS)" : `${nextMode} mode`}.`);
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
    setProbesUnavailable(false);
    try {
      const [mList, pCat, pResult, v, f, ih, id, updateState] = await Promise.all([
        fetchAvailableModels().catch(() => BUILTIN_FREE_MODELS),
        fetchProvidersCatalog().catch(() => FALLBACK_PROVIDER_CATALOG),
        probeAll()
          .then((list) => ({ ok: true as const, list }))
          .catch(() => ({ ok: false as const, list: [] as Probe[] })),
        fetchOpsVersion().catch(() => "unknown"),
        fetchFeatures().catch(() => null),
        fetchIntegrationHealth().catch(() => null),
        fetchEvolutionIdentity().catch(() => null),
        getEvolutionUpdateState().catch(() => null),
      ]);
      setModels(mList && mList.length > 0 ? mList : BUILTIN_FREE_MODELS);
      setProvidersCatalog(pCat && pCat.length > 0 ? pCat : FALLBACK_PROVIDER_CATALOG);
      setProbes(pResult.list);
      setProbesUnavailable(!pResult.ok);
      setOpsVersion(v);
      setFeatures(f);
      setIntegrationHealth(ih);
      setIdentity(id);
      setUpdateDetails(updateState);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setProbing(false);
      setModelsLoading(false);
    }
  };

  const handleUpdateCheck = async () => {
    setUpdateBusy(true);
    setError(null);
    try {
      const result = await checkForEvolutionUpdate();
      setNotice(
        result.state === "UPDATE_AVAILABLE"
          ? `Update ${result.latestTag ?? ""} is available and awaiting an explicit apply.`
          : `Update check: ${result.state}${result.error ? ` — ${result.error}` : ""}`,
      );
      await loadData();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setUpdateBusy(false);
    }
  };

  const handleUpdateApply = async () => {
    const target = updateDetails?.availableVersion ?? updateDetails?.latestTag ?? "the verified update";
    if (typeof globalThis.confirm === "function" && !globalThis.confirm(`Apply ${target}? Alpha will restart and verify the checkout.`)) {
      return;
    }
    setUpdateBusy(true);
    setError(null);
    try {
      // ``true`` confirms an attended manual handoff only; the server still
      // enforces canApply, clean-worktree, fast-forward, and health checks.
      const result = await requestEvolutionUpdate(true);
      setUpdateDetails((current) => (current ? { ...current, state: "APPLY_REQUESTED", canApply: false } : current));
      setNotice(`Update transaction ${String(result.transaction_id ?? "")} queued; Alpha will restart and verify it.`);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setUpdateBusy(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const gatewayProbe = probes.find((p) => p.key === "gateway");
  const isOnline = Boolean(gatewayProbe?.ok);
  const onlineCount = probes.filter((p) => p.ok === true).length;
  const updateState = updateDetails?.state ?? identity?.updateState ?? "unknown";
  const updateCanApply = updateDetails?.canApply === true;
  const updateReason = typeof updateDetails?.reason === "string" ? updateDetails.reason : "";
  const updateBadgeTone =
    updateState === "UPDATE_AVAILABLE"
      ? "amber"
      : updateState === "UP_TO_DATE"
        ? "green"
        : updateState === "CHECK_FAILED" || updateState === "RECOVERY_REQUIRED"
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
              <div className="flex items-center gap-2 flex-wrap justify-end">
                <Badge tone="blue">v{opsVersion}</Badge>
                <Badge tone={updateBadgeTone}>{updateState}</Badge>
                <Btn variant="ghost" onClick={handleUpdateCheck} disabled={updateBusy} title="Check the configured GitHub release/branch">
                  <RefreshCw className={`size-3.5 ${updateBusy ? "animate-spin" : ""}`} />
                  Check update
                </Btn>
                {updateState === "UPDATE_AVAILABLE" && updateCanApply && (
                  <Btn onClick={handleUpdateApply} disabled={updateBusy} title="Queue the verified source update">
                    <RefreshCw className="size-3.5" />
                    Apply update
                  </Btn>
                )}
                {updateState === "UPDATE_AVAILABLE" && !updateCanApply && (
                  <span className="max-w-[22rem] text-[11px] text-amber-600 dark:text-amber-300">
                    {updateReason || "The update is not applicable until the server safety checks pass."}
                  </span>
                )}
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
        <div className="space-y-5 pt-1">
          {/* Top Control Bar */}
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  {onOpenView && (
                    <button
                      type="button"
                      onClick={() => onOpenView("chat")}
                      className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted mr-1"
                      title="Back to Chat"
                    >
                      <ArrowLeft className="size-4" />
                    </button>
                  )}
                  <h3 className="text-sm font-bold text-foreground">Model & API Key Configuration Studio</h3>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Select your primary LLM reasoning engine, manage API keys for free/commercial providers, or probe today's live keyless models.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <Btn
                  variant="ghost"
                  onClick={async () => {
                    setProbingFree(true);
                    setProbeSummary(null);
                    try {
                      const result = await probeFreeModels();
                      const onlineCount = Object.values(result.probes || {}).filter((p: any) => p?.ok).length;
                      const totalCount = Object.keys(result.probes || {}).length;
                      setProbeSummary(`⚡ Probed ${totalCount} endpoints: ${onlineCount} online & ready today.`);
                      flash(`Free catalog probed: ${result.synced_models_count} models synced.`);
                      await loadData();
                      if (onModelsUpdated) await onModelsUpdated();
                    } catch (e: any) {
                      setError(e?.message || "Free probe failed");
                    } finally {
                      setProbingFree(false);
                    }
                  }}
                  disabled={probingFree}
                  className="text-amber-500 border-amber-500/30 hover:bg-amber-500/10"
                >
                  <Zap className={`size-3.5 ${probingFree ? "animate-spin text-amber-400" : ""}`} />
                  {probingFree ? "Probing Free Models…" : "⚡ Probe Today's Free Models"}
                </Btn>
                <Btn variant="ghost" onClick={loadData}>
                  <RefreshCw className="size-3.5" /> Refresh
                </Btn>
                {onOpenView && (
                  <Btn variant="primary" onClick={() => onOpenView("chat")}>
                    ← Return to Chat
                  </Btn>
                )}
              </div>
            </div>

            {probeSummary && (
              <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs font-medium text-amber-700 dark:text-amber-300 flex items-center justify-between">
                <span>{probeSummary}</span>
                <button
                  type="button"
                  onClick={() => setProbeSummary(null)}
                  className="text-muted-foreground hover:text-foreground text-xs"
                >
                  Dismiss
                </button>
              </div>
            )}

            {/* Currently Active Model Highlight */}
            <div className="p-3 rounded-xl bg-primary/5 border border-primary/20 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <div className="size-8 rounded-lg bg-primary/10 flex items-center justify-center font-bold text-primary shrink-0">
                  <Cpu className="size-4" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-foreground">
                      Active Model: {models.find((m) => m.id === selectedModel)?.name || selectedModel}
                    </span>
                    <Badge tone="blue">Selected</Badge>
                  </div>
                  <span className="text-[11px] font-mono text-muted-foreground block truncate">
                    {selectedModel}
                  </span>
                </div>
              </div>
              <span className="text-[11px] text-muted-foreground">
                All agent tasks, tools, and slash commands execute with this engine.
              </span>
            </div>
          </div>

          {/* Section 1: Provider API Key Management Studio */}
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                  <Key className="size-4 text-primary" />
                  Provider Credentials & Free Quota Hub
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Enter your API keys to unlock models. Keys are securely injected into the runtime environment without requiring a server reboot.
                </p>
              </div>
              {/* Category Filter Pills */}
              <div className="flex items-center gap-1 overflow-x-auto pb-1 max-w-full">
                {[
                  { id: "all", label: "All" },
                  { id: "recurring_free", label: "🎁 Free Quota ($0)" },
                  { id: "free_gateway", label: "🌐 Gateways" },
                  { id: "trial_credits", label: "🎟️ Credits" },
                  { id: "keyless_free", label: "✨ Keyless Free" },
                  { id: "paid", label: "🚀 Frontier" },
                ].map((cat) => (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => setProviderFilter(cat.id as any)}
                    className={`px-2.5 py-1 text-[11px] font-medium rounded-lg transition-colors whitespace-nowrap ${
                      providerFilter === cat.id
                        ? "bg-primary text-primary-foreground font-semibold"
                        : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
                    }`}
                  >
                    {cat.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-2">
              {providersCatalog
                .filter((prov) => {
                  if (providerFilter === "all") return true;
                  return prov.category === providerFilter;
                })
                .map((prov) => {
                  const isKeyless = prov.category === "keyless_free";
                  const isConfigured = prov.configured;
                  const keyVal = keyInputs[prov.id] ?? "";
                  const isSecretShown = Boolean(showSecrets[prov.id]);
                  const isSavingThis = savingProvider === prov.id;

                  return (
                    <div
                      key={prov.id}
                      className={`p-3.5 rounded-xl border transition-all space-y-2.5 ${
                        isConfigured
                          ? "border-emerald-500/30 bg-emerald-500/[0.02]"
                          : "border-border/60 bg-muted/20"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-bold text-foreground">{prov.name}</span>
                            <Badge
                              tone={
                                isKeyless
                                  ? "purple"
                                  : prov.category === "recurring_free"
                                    ? "green"
                                    : prov.category === "free_gateway"
                                      ? "blue"
                                      : prov.category === "trial_credits"
                                        ? "amber"
                                        : "gray"
                              }
                            >
                              {isKeyless
                                ? "✨ Keyless Free"
                                : prov.category === "recurring_free"
                                  ? "🎁 $0 Free Quota"
                                  : prov.category === "free_gateway"
                                    ? "🌐 Gateway"
                                    : prov.category === "trial_credits"
                                      ? "🎟️ Free Credits"
                                      : "🚀 Commercial"}
                            </Badge>
                          </div>
                          <p className="text-[11px] text-muted-foreground mt-0.5">{prov.free_tier_note}</p>
                        </div>
                        <Badge tone={isConfigured ? "green" : "gray"}>
                          {isKeyless ? "Active" : isConfigured ? `Key Set (${prov.masked_key || "****"})` : "No Key"}
                        </Badge>
                      </div>

                      {/* Default Models Tags */}
                      {prov.default_models && prov.default_models.length > 0 && (
                        <div className="space-y-1">
                          <span className="text-[10px] uppercase font-semibold text-muted-foreground tracking-wider block">
                            Included Models (Click to switch)
                          </span>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {prov.default_models.map((dm) => {
                              const isThisModelSelected = selectedModel === dm.id;
                              return (
                                <button
                                  key={dm.id}
                                  type="button"
                                  onClick={() => {
                                    setSelectedModel(dm.id);
                                    try {
                                      localStorage.setItem("alpha_selected_model", dm.id);
                                    } catch {}
                                    if (onModelChange) onModelChange(dm.id);
                                    flash(`Switched active model to ${dm.name}`);
                                  }}
                                  className={`text-[10px] px-2 py-0.5 rounded-md font-mono transition-colors border ${
                                    isThisModelSelected
                                      ? "bg-primary text-primary-foreground border-primary font-bold shadow-xs"
                                      : "bg-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted border-border/40"
                                  }`}
                                  title={`Click to activate ${dm.name}`}
                                >
                                  {dm.id.replace(/^free:[^:]+:/, "").slice(0, 24)}
                                  {isThisModelSelected && " ✓"}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* API Key Form for non-keyless providers */}
                      {!isKeyless && (
                        <div className="pt-1 border-t border-border/40 space-y-2">
                          <div className="flex items-center gap-2">
                            <div className="relative flex-1">
                              <input
                                type={isSecretShown ? "text" : "password"}
                                value={keyVal}
                                onChange={(e) => setKeyInputs((prev) => ({ ...prev, [prov.id]: e.target.value }))}
                                placeholder={isConfigured ? "Enter new key to replace..." : `Paste ${prov.key_env || "API key"}...`}
                                className="w-full text-xs bg-muted/50 border border-border/70 rounded-lg pl-2.5 pr-8 py-1.5 font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                              />
                              <button
                                type="button"
                                onClick={() => setShowSecrets((prev) => ({ ...prev, [prov.id]: !isSecretShown }))}
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                aria-label="Toggle secret visibility"
                              >
                                {isSecretShown ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                              </button>
                            </div>
                            <button
                              type="button"
                              disabled={!keyVal.trim() || isSavingThis}
                              onClick={async () => {
                                setSavingProvider(prov.id);
                                try {
                                  await configureProviderCredentials({
                                    provider: prov.id,
                                    api_key: keyVal.trim(),
                                  });
                                  flash(`✅ Configured ${prov.name} API key!`);
                                  setKeyInputs((prev) => ({ ...prev, [prov.id]: "" }));
                                  await loadData();
                                  if (onModelsUpdated) await onModelsUpdated();
                                } catch (err: any) {
                                  setError(err?.message || "Failed to configure provider");
                                } finally {
                                  setSavingProvider(null);
                                }
                              }}
                              className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 disabled:opacity-40 shrink-0"
                            >
                              {isSavingThis ? "Saving…" : "Save Key"}
                            </button>
                            {isConfigured && (
                              <button
                                type="button"
                                disabled={isSavingThis}
                                onClick={async () => {
                                  setSavingProvider(prov.id);
                                  try {
                                    await configureProviderCredentials({
                                      provider: prov.id,
                                      remove: true,
                                    });
                                    flash(`Removed key for ${prov.name}`);
                                    await loadData();
                                    if (onModelsUpdated) await onModelsUpdated();
                                  } catch (err: any) {
                                    setError(err?.message || "Failed to remove key");
                                  } finally {
                                    setSavingProvider(null);
                                  }
                                }}
                                className="p-1.5 rounded-lg border border-destructive/30 text-destructive hover:bg-destructive/10 shrink-0"
                                title="Remove configured key"
                                aria-label="Remove key"
                              >
                                <Trash2 className="size-3.5" />
                              </button>
                            )}
                          </div>
                          {prov.portal_url && (
                            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                              <span>Env: <code>${prov.key_env}</code></span>
                              <a
                                href={prov.portal_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-primary hover:underline font-semibold"
                              >
                                Get Free API Key <ExternalLink className="size-2.5" />
                              </a>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          </div>

          {/* Section 2: Add Custom / Local OpenAI-Compatible Model */}
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Plus className="size-4 text-primary" />
              Connect Custom or Local Endpoint (Ollama, LM Studio, vLLM, Private Proxy)
            </h3>
            <p className="text-xs text-muted-foreground">
              Connect local AI servers running on your machine or any OpenAI-compatible custom gateway.
            </p>

            <div className="flex items-center gap-2 flex-wrap text-xs pt-1">
              <span className="text-[11px] font-semibold text-muted-foreground">Quick Presets:</span>
              <button
                type="button"
                onClick={() => {
                  setCustomBaseUrl("http://127.0.0.1:11434/v1");
                  setCustomModelId("llama3.2");
                  setCustomDisplayName("Ollama LLaMA 3.2");
                  setCustomApiKey("ollama");
                }}
                className="px-2 py-0.5 rounded-md border border-border bg-muted/40 hover:bg-muted text-[11px]"
              >
                🦙 Ollama (11434)
              </button>
              <button
                type="button"
                onClick={() => {
                  setCustomBaseUrl("http://localhost:1234/v1");
                  setCustomModelId("local-model");
                  setCustomDisplayName("LM Studio Local");
                  setCustomApiKey("lm-studio");
                }}
                className="px-2 py-0.5 rounded-md border border-border bg-muted/40 hover:bg-muted text-[11px]"
              >
                🧪 LM Studio (1234)
              </button>
              <button
                type="button"
                onClick={() => {
                  setCustomBaseUrl("http://localhost:8000/v1");
                  setCustomModelId("deepseek-r1");
                  setCustomDisplayName("vLLM DeepSeek R1");
                }}
                className="px-2 py-0.5 rounded-md border border-border bg-muted/40 hover:bg-muted text-[11px]"
              >
                ⚡ vLLM Server (8000)
              </button>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5 pt-2">
              <div>
                <label className="text-[10px] font-semibold uppercase text-muted-foreground block mb-1">
                  Model ID (Identifier) *
                </label>
                <input
                  type="text"
                  value={customModelId}
                  onChange={(e) => setCustomModelId(e.target.value)}
                  placeholder="e.g. llama3.2 or custom-gpt"
                  className="w-full text-xs bg-muted/50 border border-border/70 rounded-lg px-2.5 py-1.5 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-[10px] font-semibold uppercase text-muted-foreground block mb-1">
                  Display Name
                </label>
                <input
                  type="text"
                  value={customDisplayName}
                  onChange={(e) => setCustomDisplayName(e.target.value)}
                  placeholder="e.g. Local LLaMA 3.2"
                  className="w-full text-xs bg-muted/50 border border-border/70 rounded-lg px-2.5 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-[10px] font-semibold uppercase text-muted-foreground block mb-1">
                  Base URL *
                </label>
                <input
                  type="text"
                  value={customBaseUrl}
                  onChange={(e) => setCustomBaseUrl(e.target.value)}
                  placeholder="http://127.0.0.1:11434/v1"
                  className="w-full text-xs bg-muted/50 border border-border/70 rounded-lg px-2.5 py-1.5 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div>
                <label className="text-[10px] font-semibold uppercase text-muted-foreground block mb-1">
                  API Key (Optional for local)
                </label>
                <input
                  type="password"
                  value={customApiKey}
                  onChange={(e) => setCustomApiKey(e.target.value)}
                  placeholder="sk-... or ollama"
                  className="w-full text-xs bg-muted/50 border border-border/70 rounded-lg px-2.5 py-1.5 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </div>

            <div className="flex justify-end pt-1">
              <button
                type="button"
                disabled={!customModelId.trim() || savingCustom}
                onClick={async () => {
                  setSavingCustom(true);
                  try {
                    await configureProviderCredentials({
                      provider: "custom",
                      model_id: customModelId.trim(),
                      display_name: customDisplayName.trim() || customModelId.trim(),
                      base_url: customBaseUrl.trim() || "http://127.0.0.1:11434/v1",
                      api_key: customApiKey.trim() || "ollama",
                    });
                    const targetId = customModelId.trim();
                    flash(`✅ Added custom model ${targetId}!`);
                    setSelectedModel(targetId);
                    try {
                      localStorage.setItem("alpha_selected_model", targetId);
                    } catch {}
                    if (onModelChange) onModelChange(targetId);
                    setCustomModelId("");
                    setCustomDisplayName("");
                    await loadData();
                    if (onModelsUpdated) await onModelsUpdated();
                  } catch (err: any) {
                    setError(err?.message || "Failed to add custom model");
                  } finally {
                    setSavingCustom(false);
                  }
                }}
                className="px-4 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 disabled:opacity-40"
              >
                {savingCustom ? "Registering…" : "Add & Select Model"}
              </button>
            </div>
          </div>

          {/* Section 3: All Available Models Catalog */}
          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                  <Database className="size-4 text-primary" />
                  Available Reasoning Models ({models.length})
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Click any model card to set it as the active reasoning engine.
                </p>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                {/* Search */}
                <div className="relative">
                  <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                  <input
                    type="text"
                    value={modelSearch}
                    onChange={(e) => setModelSearch(e.target.value)}
                    placeholder="Filter models…"
                    className="text-xs bg-muted/50 border border-border rounded-lg pl-8 pr-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary w-40"
                  />
                </div>
                {/* Model Category Filters */}
                <div className="flex items-center gap-1">
                  {[
                    { id: "all", label: "All" },
                    { id: "keyless", label: "✨ Keyless Free" },
                    { id: "quota", label: "🎁 Free Quota" },
                    { id: "paid", label: "🚀 Standard" },
                  ].map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      onClick={() => setModelFilter(f.id as any)}
                      className={`px-2 py-1 text-[11px] rounded-lg transition-colors ${
                        modelFilter === f.id
                          ? "bg-primary text-primary-foreground font-semibold"
                          : "bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground"
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {modelsLoading ? (
              <SkeletonList rows={3} />
            ) : models.length === 0 ? (
              <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-700 dark:text-amber-300">
                <p className="font-semibold">No models currently listed.</p>
                <p className="mt-1 text-muted-foreground">
                  The system will fall back to the default router. Click "⚡ Probe Today's Free Models" above to load keyless models.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pt-2">
                {models
                  .filter((m) => {
                    const q = modelSearch.toLowerCase();
                    const matchesSearch =
                      !q ||
                      m.id.toLowerCase().includes(q) ||
                      m.name.toLowerCase().includes(q) ||
                      (m.provider && m.provider.toLowerCase().includes(q));
                    if (!matchesSearch) return false;

                    if (modelFilter === "keyless") {
                      return m.is_free && (m.free_status === "no_key_free" || m.quota_type === "keyless_free" || m.quota_type === "keyless" || m.id.startsWith("free:") || m.id === "alpha-free");
                    }
                    if (modelFilter === "quota") {
                      return m.is_free && !(m.free_status === "no_key_free" || m.quota_type === "keyless_free" || m.quota_type === "keyless" || m.id.startsWith("free:") || m.id === "alpha-free");
                    }
                    if (modelFilter === "paid") {
                      return !m.is_free;
                    }
                    return true;
                  })
                  .map((m) => {
                    const isSelected = selectedModel === m.id;
                    const isKeyless = Boolean(m.is_free && (m.free_status === "no_key_free" || m.quota_type === "keyless_free" || m.id.startsWith("free:") || m.id === "alpha-free"));

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
                        className={`cursor-pointer rounded-xl border p-3.5 transition-all flex flex-col justify-between ${
                          isSelected
                            ? "border-primary bg-primary/5 ring-1 ring-primary shadow-sm"
                            : "border-border/60 bg-muted/20 hover:bg-muted/50 hover:border-primary/40"
                        }`}
                      >
                        <div>
                          <div className="flex items-start justify-between gap-1.5">
                            <span className="text-xs font-bold text-foreground leading-snug">{m.name}</span>
                            <Badge
                              tone={
                                isSelected
                                  ? "blue"
                                  : isKeyless
                                    ? "purple"
                                    : m.is_free
                                      ? "green"
                                      : "gray"
                              }
                            >
                              {isKeyless ? "✨ Free No Key" : m.is_free ? "🎁 Free Quota" : m.provider || "Standard"}
                            </Badge>
                          </div>
                          <p className="text-[10px] text-muted-foreground mt-1 font-mono truncate">{m.id}</p>
                          {m.description && (
                            <p className="text-xs text-muted-foreground mt-2 line-clamp-2">{m.description}</p>
                          )}
                        </div>

                        <div className="mt-3 pt-2 border-t border-border/40 flex items-center justify-between text-[10px]">
                          <div className="flex items-center gap-1 text-muted-foreground">
                            {m.supports_reasoning && <span className="text-amber-500 font-semibold">🧠 Thinking</span>}
                            <span>• {m.provider}</span>
                          </div>
                          <div className="flex items-center gap-1 font-semibold">
                            {isSelected ? (
                              <span className="text-primary flex items-center gap-1">
                                <CheckCircle2 className="size-3.5" /> Active
                              </span>
                            ) : (
                              <span className="text-muted-foreground hover:text-foreground">Click to select</span>
                            )}
                          </div>
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
              <Badge tone={probing ? "gray" : probesUnavailable ? "gray" : isOnline ? "green" : "red"}>
                {probing
                  ? "Checking subsystems…"
                  : probesUnavailable
                    ? "Subsystem status unavailable"
                    : `${onlineCount}/${probes.length} subsystems online`}
              </Badge>
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

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div
                onClick={() => handleThemeChange("dark")}
                className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                  themeMode === "dark"
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border/60 bg-muted/30 hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-foreground">Dark Theme</span>
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

              <div
                onClick={() => handleThemeChange("system")}
                className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                  themeMode === "system"
                    ? "border-primary bg-primary/5 ring-1 ring-primary"
                    : "border-border/60 bg-muted/30 hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-foreground">System (Default)</span>
                  {themeMode === "system" && <CheckCircle2 className="size-4 text-primary" />}
                </div>
                <p className="text-[11px] text-muted-foreground mt-1">Follows your OS light/dark preference - what applies when nothing is chosen</p>
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

          <div className="rounded-2xl border border-border/70 bg-card p-4 space-y-3">
            <h3 className="text-sm font-semibold text-foreground">Voice & Speakers</h3>
            <p className="text-xs text-muted-foreground">
              Wake word, speech engines, and speaker playback (observed from the gateway capabilities report).
            </p>
            {voiceReportError ? (
              <ErrorBox message={`Capabilities unavailable: ${voiceReportError}`} />
            ) : !voiceReport ? (
              <SkeletonList rows={3} />
            ) : (
              <div className="space-y-3">
                <label className="flex items-start gap-2 text-sm text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={voiceAutoplay}
                    onChange={(e) => {
                      setVoiceAutoplay(e.target.checked);
                      writeAutoplayEnabled(e.target.checked);
                    }}
                    className="mt-0.5 size-4 rounded border-border text-primary focus:ring-primary/40 cursor-pointer"
                  />
                  <span>
                    Autoplay replies
                    <span className="block text-[11px] text-muted-foreground">
                      Saved in this browser; automatic playback of new replies is not wired in this build — use the speaker button on any answer.
                    </span>
                  </span>
                </label>
                {voiceReport.voice.enabled ? (
                  <p className="text-xs text-muted-foreground">
                    Wake word: <span className="font-mono text-foreground">{voiceReport.voice.wake_word?.engine ?? "unset"}</span>
                    {" · threshold "}
                    <span className="font-mono text-foreground">{voiceReport.voice.wake_word?.threshold ?? "—"}</span>
                    {" · armed by default "}
                    {voiceReport.voice.wake_word?.armed_default ? "yes" : "no"} (arming is always user-initiated in the UI)
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground italic">voice disabled (voice.enabled=false)</p>
                )}
                <div className="space-y-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Engine availability (observed)
                  </span>
                  {(["wake_word", "tts", "stt", "ocr", "vision", "image_gen"] as const).map((capability) => {
                    const rows = voiceReport.rows.filter((row) => row.capability === capability);
                    if (rows.length === 0) return null;
                    return (
                      <div key={capability} className="space-y-1">
                        <span className="text-[11px] text-foreground">{capability.replace("_", " ")}</span>
                        <div className="flex flex-wrap gap-1">
                          {rows.map((row) => (
                            <span
                              key={`${row.tier}-${row.engine}`}
                              title={row.detail}
                              className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${probeBadge(row.status)}`}
                            >
                              {row.tier} · {row.engine}: {row.status}
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <p className="text-[10px] text-muted-foreground">{voiceReport.note}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}
