"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { BotProfile, collidingBotLabels, botRosterLabel } from "@/types/bots";
import { uniqueDepartments, computeFleetHealth } from "@/lib/bots";
import { isRecent, PRESENCE_WINDOW_SECONDS } from "@/lib/time";
import {
  fetchBotHealthOverview,
  fetchPauseState,
  healthRowFor,
  healthRowIndex,
  needsAttention,
  WORKING_FACETS,
  workingStatusFor,
  type FleetHealthOverview,
  type PauseState,
  type WorkingStatus,
} from "@/lib/bot-working-status";
import {
  activeFilterCount,
  activeFilterLabels,
  DEFAULT_BOT_FILTERS,
  filterBots,
  hasUnreportedModel,
  hasUnreportedStatus,
  isDefaultFilters,
  MODEL_NOT_REPORTED,
  sortBots,
  STATUS_NOT_REPORTED,
  uniqueModels,
  uniqueStatuses,
  type ActivityFacet,
  type BotFilterCriteria,
  type RunsFacet,
  type SortKey,
  type WorkingFacet,
} from "@/lib/bot-roster-filters";
import { BotProfileCard } from "./BotProfileCard";
import { FleetHealthBar } from "./FleetHealthBar";
import { BotLivenessStrip } from "./BotLivenessStrip";
import { Notice } from "@/components/ui";
import { Search, RotateCcw, X } from "lucide-react";

interface BotGalleryProps {
  bots: BotProfile[];
  activeBotName: string | null;
  isLoading: boolean;
  /**
   * The server's own reason the roster read failed, or null when it succeeded.
   *
   * Optional so the gallery keeps its old behaviour for callers that do not
   * track it, and load-bearing for the ones that do: `bots: []` after a failed
   * read would otherwise render as "The Gateway reported no bots", which is the
   * opposite claim. Only the parent knows which of the two happened.
   */
  loadError?: string | null;
  onSelect: (bot: BotProfile) => void;
  onChat: (bot: BotProfile) => void;
  onRefresh: () => void;
}

/** One server read, kept apart from "the answer is empty" and "the answer is old". */
type Read<T> =
  | { state: "loading" }
  | { state: "ok"; value: T }
  | { state: "error"; reason: string };

/** Names to name, not to dump: a fleet of 57 with 30 in a state is a sentence, not a wall of text. */
const NAMED_ATTENTION_BOTS = 5;

const ACTIVITY_FACETS: Array<{ value: ActivityFacet; label: string }> = [
  { value: "all", label: "Any inbox state" },
  { value: "unread", label: "Unread messages" },
  { value: "read", label: "No unread messages" },
  { value: "not-reported", label: "Inbox not reported" },
];

const RUNS_FACETS: Array<{ value: RunsFacet; label: string }> = [
  { value: "all", label: "Any run history" },
  { value: "measured", label: "Runs measured" },
  { value: "unmeasured", label: "No run count reported" },
  { value: "failures", label: "Has failed runs" },
];

const SORT_KEYS: Array<{ value: SortKey; label: string }> = [
  { value: "server", label: "Server order" },
  { value: "name", label: "Name" },
  { value: "department", label: "Department" },
  { value: "recent", label: "Most recent activity" },
  { value: "reputation", label: "Highest reputation" },
  { value: "tasks", label: "Most tasks done" },
];

export function BotGallery({
  bots,
  activeBotName,
  isLoading,
  loadError,
  onSelect,
  onChat,
  onRefresh,
}: BotGalleryProps) {
  const [filters, setFilters] =
    useState<BotFilterCriteria>(DEFAULT_BOT_FILTERS);
  const patch = useCallback(
    (p: Partial<BotFilterCriteria>) => setFilters((f) => ({ ...f, ...p })),
    [],
  );

  /* ---------------------------------------------------------------- health */

  /**
   * The two reads that answer "is this bot working".
   *
   * They are settled independently on purpose: the liveness report coming back
   * while the kill-switch read fails must not blank a working status view, and
   * vice versa — "nobody is paused" and "we could not check" lead to opposite
   * actions, exactly as `lib/network.ts` documents for connectivity.
   */
  const [liveness, setLiveness] = useState<Read<FleetHealthOverview>>({
    state: "loading",
  });
  const [pauses, setPauses] = useState<Read<PauseState>>({ state: "loading" });
  /** Guards against a slow first read landing after a refresh has started. */
  const readGeneration = useRef(0);

  const reloadWorkingSources = useCallback(() => {
    const generation = readGeneration.current + 1;
    readGeneration.current = generation;
    setLiveness({ state: "loading" });
    setPauses({ state: "loading" });
    void fetchBotHealthOverview().then((result) => {
      if (readGeneration.current !== generation) return;
      setLiveness(
        result.ok
          ? { state: "ok", value: result.value }
          : { state: "error", reason: result.error },
      );
    });
    void fetchPauseState().then((result) => {
      if (readGeneration.current !== generation) return;
      setPauses(
        result.ok
          ? { state: "ok", value: result.value }
          : { state: "error", reason: result.error },
      );
    });
  }, []);

  // A roster refresh carries new `last_active` values, so the liveness read
  // rides with it instead of going stale over the same cards.
  useEffect(() => {
    reloadWorkingSources();
  }, [reloadWorkingSources, bots]);

  /**
   * One working status per bot, derived once and shared by the cards, the
   * filter and the summary. The card never derives it again, so a filter and a
   * badge cannot answer the same question differently.
   */
  const workingByBot = useMemo(() => {
    const index =
      liveness.state === "ok" ? healthRowIndex(liveness.value.bots) : null;
    const paused = pauses.state === "ok" ? pauses.value.paused : null;
    // An engaged switch with no reported reason still stops every bot: the
    // absence of a sentence is a fact about the payload, not about the fleet.
    const killSwitchReason =
      pauses.state === "ok" && pauses.value.active === true
        ? (pauses.value.reason ?? "engaged, no reason reported")
        : null;
    const statuses = new Map<string, WorkingStatus>();
    for (const bot of bots) {
      const key = bot.name.trim().toLowerCase();
      const pausedReason = killSwitchReason
        ? `Global kill switch: ${killSwitchReason}`
        : paused
          ? (paused[key] ?? null)
          : null;
      const row = index ? healthRowFor(index, bot.name) : null;
      statuses.set(bot.name, workingStatusFor(bot, row, { pausedReason }));
    }
    return statuses;
  }, [bots, liveness, pauses]);

  const statusOf = useCallback(
    (bot: BotProfile): WorkingStatus | undefined => workingByBot.get(bot.name),
    [workingByBot],
  );

  /* -------------------------------------------------------------- derived  */

  const departments = useMemo(() => uniqueDepartments(bots), [bots]);
  const health = useMemo(() => computeFleetHealth(bots), [bots]);
  const statuses = useMemo(() => uniqueStatuses(bots), [bots]);
  const models = useMemo(() => uniqueModels(bots), [bots]);
  const filterSummary = useMemo(
    () => activeFilterLabels(filters).join(" · "),
    [filters],
  );

  /**
   * "Working right now" is a display reading off `last_active`, not a liveness
   * verdict — `alpha.bots.health` owns healthy/stale/stalled/dead. The strip is
   * hidden when the window is empty so an idle fleet is not given a header that
   * says nothing.
   */
  const activeNow = useMemo(
    () => bots.filter((b) => isRecent(b.last_active, PRESENCE_WINDOW_SECONDS)),
    [bots],
  );
  const unreadTotal = useMemo(
    () =>
      bots.reduce(
        (sum, b) =>
          sum + (typeof b.unread_count === "number" ? b.unread_count : 0),
        0,
      ),
    [bots],
  );

  /** Bots a human has to act on: stalled on a task, or stopped by an operator. */
  const attentionBots = useMemo(
    () =>
      bots
        .filter((b) => {
          const status = workingByBot.get(b.name);
          return status ? needsAttention(status) : false;
        })
        .map((b) => b.display_name || b.name),
    [bots, workingByBot],
  );

  const filtered = useMemo(
    () => sortBots(filterBots(bots, filters, statusOf), filters.sort),
    [bots, filters, statusOf],
  );

  /**
   * Which display labels collide in the FULL roster, not the filtered one.
   *
   * Deliberately `bots` and not `filtered`: a filter that hides five of the
   * eight "Data Engineer" cards would otherwise make the remaining three look
   * unique, and the qualifier would disappear as the user typed. The collision
   * is a property of the fleet, not of the current view.
   */
  const collidingLabels = useMemo(() => collidingBotLabels(bots), [bots]);

  const reload = useCallback(() => {
    onRefresh();
    reloadWorkingSources();
  }, [onRefresh, reloadWorkingSources]);

  const anyFilter = !isDefaultFilters(filters);

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-4 max-w-6xl w-full mx-auto">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-base font-semibold tracking-tight">Bot Team</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Browse every specialist profile, compare capabilities, and pick who
            answers in chat.
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
          className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted"
        >
          <RotateCcw className="size-3.5" /> Refresh
        </button>
      </div>

      {/* The fleet strip must not render while the roster is still in flight.
          `computeFleetHealth([])` returns total/active/paused of 0, so gating
          only the card grid below left this strip asserting a *measured empty
          fleet* for the whole load. Measured here: the roster takes ~20s to
          arrive (it is fetched with `activity=true`, which costs a per-bot
          inbox read plus a secret scan), and for those 20s the header read
          "0 Total bots / 0 Active / 0 Paused" against a server that reports 57.

          That is the specific thing the client honesty rules forbid — a count
          the server did not report is `null`, never `0`. The grid below
          already had a skeleton for exactly the reason; this strip did not,
          because it sat outside that branch. */}
      {isLoading ? (
        <div
          className="grid grid-cols-2 sm:grid-cols-5 gap-2"
          aria-busy="true"
          aria-label="Fleet health loading"
        >
          {Array.from({ length: 5 }).map((_, i) => (
            <div
              key={i}
              className="rounded-xl border border-border/60 bg-card px-3 py-2.5 animate-pulse"
            >
              <div className="h-4 rounded bg-muted w-10" />
              <div className="h-2.5 rounded bg-muted w-16 mt-2" />
            </div>
          ))}
        </div>
      ) : (
        <FleetHealthBar health={health} />
      )}

      {/* A roster read that failed is disclosed, not absorbed: an empty grid
          otherwise reads as "the Gateway reported no bots", which is the
          opposite claim and the one nobody can recover from on screen. */}
      {loadError && (
        <Notice
          tone="warn"
          message={`The bot roster could not be read — ${loadError}. The list below may be stale or incomplete.`}
        />
      )}

      {/* The operator stop is a fact about the fleet, not a setting: a bot the
          kill switch holds must never read as working. Engaged but with no
          reason is still named rather than shown as a generic stop. */}
      {pauses.state === "ok" && pauses.value.active === true && (
        <Notice
          tone="warn"
          message={`Global kill switch engaged${pauses.value.reason ? ` — ${pauses.value.reason}` : " — the Gateway reported no reason"}. No bot is taking work until it is released.`}
        />
      )}

      {/* An actionable set, not every non-working bot: a resting fleet reads
          `dead` on the monitor, so escalating that here would raise this
          notice permanently and train operators to ignore it. Stalled work and
          an operator's stop are the two that need a decision now. */}
      {attentionBots.length > 0 && (
        <Notice
          tone="warn"
          message={`${attentionBots.length} bot${attentionBots.length === 1 ? "" : "s"} need attention: stalled on a task, or stopped by an operator. ${attentionBots
            .slice(0, NAMED_ATTENTION_BOTS)
            .join(
              ", ",
            )}${attentionBots.length > NAMED_ATTENTION_BOTS ? ` and ${attentionBots.length - NAMED_ATTENTION_BOTS} more` : ""}`}
        />
      )}

      <BotLivenessStrip
        state={liveness.state}
        overview={liveness.state === "ok" ? liveness.value : null}
        reason={liveness.state === "error" ? liveness.reason : undefined}
        onRetry={reloadWorkingSources}
      />

      {/* The pause read failing is disclosed rather than absorbed: an operator
          who has stopped a bot cannot be told apart from one that is running,
          and the difference decides whether they intervene. */}
      {pauses.state === "error" && (
        <Notice
          tone="neutral"
          message={`Operator pause state unavailable — ${pauses.reason}. A stopped bot would otherwise read as working.`}
        />
      )}

      {activeNow.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap rounded-xl border border-border/60 bg-card px-3 py-2">
          <span className="text-[11px] font-medium text-muted-foreground inline-flex items-center gap-1.5">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            Working now
          </span>
          {activeNow.map((b) => (
            <button
              key={b.name}
              type="button"
              onClick={() => onSelect(b)}
              title={`Open ${b.display_name || b.name}`}
              className="text-[11px] px-2 py-0.5 rounded-full border border-border/60 hover:border-primary/50 hover:bg-muted"
            >
              {b.display_name || b.name}
            </button>
          ))}
          {unreadTotal > 0 && (
            <span className="ml-auto text-[11px] text-muted-foreground">
              {unreadTotal} unread across the team
            </span>
          )}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div className="relative">
          <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={filters.search}
            onChange={(e) => patch({ search: e.target.value })}
            /* A placeholder is not a label: it vanishes as soon as the field has
               content, and a screen reader may not announce it at all. Measured
               as `unlabelled_inputs: INPUT[text]` on this view. */
            aria-label="Search bots"
            placeholder="Search bots by name, role, skill, capability, model..."
            className="w-full bg-card border border-border/70 rounded-xl pl-8 pr-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <select
            value={filters.department}
            onChange={(e) => patch({ department: e.target.value })}
            aria-label="Filter by department"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="all">All departments</option>
            {departments.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <select
            value={filters.status}
            onChange={(e) => patch({ status: e.target.value })}
            aria-label="Filter by status"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="all">Any status</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
            {/* The Gateway validates `status` against its own list, and a bot
                with none is a real population: offered, not dropped. */}
            {hasUnreportedStatus(bots) && (
              <option value={STATUS_NOT_REPORTED}>status not reported</option>
            )}
          </select>
          <select
            value={filters.working}
            onChange={(e) => patch({ working: e.target.value as WorkingFacet })}
            aria-label="Filter by working state"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            {WORKING_FACETS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <select
            value={filters.model}
            onChange={(e) => patch({ model: e.target.value })}
            aria-label="Filter by model"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="all">Any model</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {hasUnreportedModel(bots) && (
              <option value={MODEL_NOT_REPORTED}>model not reported</option>
            )}
          </select>
          <select
            value={filters.activity}
            onChange={(e) =>
              patch({ activity: e.target.value as ActivityFacet })
            }
            aria-label="Filter by inbox activity"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            {ACTIVITY_FACETS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <select
            value={filters.runs}
            onChange={(e) => patch({ runs: e.target.value as RunsFacet })}
            aria-label="Filter by run history"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            {RUNS_FACETS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </select>
          <select
            value={filters.sort}
            onChange={(e) => patch({ sort: e.target.value as SortKey })}
            aria-label="Sort bots"
            className="text-xs bg-card border border-border/70 rounded-xl px-2.5 py-2 font-medium cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            {SORT_KEYS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          {anyFilter && (
            <button
              type="button"
              onClick={() => setFilters(DEFAULT_BOT_FILTERS)}
              className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-2 rounded-xl border border-border hover:bg-muted"
            >
              <X className="size-3.5" /> Clear {activeFilterCount(filters)}{" "}
              filter{activeFilterCount(filters) === 1 ? "" : "s"}
            </button>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="rounded-2xl border border-border/60 bg-card p-4 space-y-3 animate-pulse"
            >
              <div className="flex gap-3">
                <div className="size-11 rounded-xl bg-muted" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 rounded bg-muted w-2/3" />
                  <div className="h-2.5 rounded bg-muted w-1/2" />
                </div>
              </div>
              <div className="h-2.5 rounded bg-muted w-full" />
              <div className="h-2.5 rounded bg-muted w-3/4" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-14 text-xs text-muted-foreground space-y-2">
          <p>
            {loadError
              ? `No bots can be shown — the roster read failed: ${loadError}`
              : bots.length === 0
                ? "The Gateway reported no bots."
                : anyFilter
                  ? `No bots match these filters: ${filterSummary}.`
                  : "No bots match these filters."}
          </p>
          {anyFilter && (
            <button
              type="button"
              onClick={() => setFilters(DEFAULT_BOT_FILTERS)}
              className="inline-flex items-center gap-1 text-[11px] font-medium px-2.5 py-1.5 rounded-xl border border-border hover:bg-muted"
            >
              <X className="size-3.5" /> Clear the filters
            </button>
          )}
        </div>
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground">
            Showing {filtered.length} of {bots.length} bots
            {filters.department !== "all" ? ` in ${filters.department}` : ""}
            {filterSummary ? ` · ${filterSummary}` : ""}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pb-6">
            {filtered.map((bot) => (
              <BotProfileCard
                key={bot.name}
                bot={bot}
                isActive={bot.name === activeBotName}
                onSelect={onSelect}
                onChat={onChat}
                rosterLabel={botRosterLabel(bot, collidingLabels)}
                working={workingByBot.get(bot.name)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
