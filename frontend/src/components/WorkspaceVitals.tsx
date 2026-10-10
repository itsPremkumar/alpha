"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Blocks,
  CircleDollarSign,
  CircleHelp,
  ChevronRight,
  Cpu,
  Database,
  Gauge,
  Layers,
  RefreshCw,
  Server,
  Sigma,
  Wifi,
  WifiOff,
} from "lucide-react";

import { Badge } from "@/components/ui";
import { Probe, probeAll } from "@/lib/system";
import { Connectivity, connectivityView, fetchConnectivity, recheckConnectivity } from "@/lib/network";
import { errMsg } from "@/lib/http";
import { ConsoleStats, fetchConsoleStats, fetchOpsVersion } from "@/lib/workspace";
import { SystemVitals, fetchSystemVitals } from "@/lib/systemMonitor";

/**
 * Live vitals for the whole workspace, sourced from the Gateway.
 *
 * The main screen should never leave the user guessing whether the backend is
 * actually there, so this strip consolidates the highest-signal numbers
 * (is the Gateway up, what is the internet link doing and how fast, the version,
 * usage, and subsystem readiness) in one place instead of scattering them across
 * settings pages.
 *
 * Two of those are genuinely different questions and must not be conflated:
 * `online`/`offline` is whether this Gateway answered, while the **internet**
 * entry is the runtime's own connectivity reading from
 * `GET /api/ops/network` — a four-state link with hysteresis, per-endpoint
 * round-trips, and the backend's automatic re-probe schedule, plus the one
 * control on this strip an operator can actually press. A Gateway that is up
 * says nothing about whether the link is.
 *
 * ## The legibility contract this file is built around
 *
 * Found by measuring the real DOM of the running app, not by reading this JSX.
 * At a 1000px viewport the whole row rendered as:
 *
 *     Gateway online  v2.1.0  ● 5.3G/5.9G 90%  ⚡7  ▤8  ⬚0  ⬦1.3M  ⬦—  ⚡6/7
 *     ●  ●  ●  ●  ●  ●  ●
 *
 * and the audit found, in the emitted markup:
 *
 * - **Fourteen labels were `display: none`.** Every noun — `runs`, `chats`,
 *   `agents`, `tokens`, `cost`, `RAM`, `subsystems ready`, and all seven
 *   subsystem names — sat behind `hidden lg:inline` / `hidden xl:inline`. A
 *   number with no noun is not information, and a `title` is not a substitute
 *   for a label a sighted user can read.
 * - **The `6/7` readiness ratio carried no `title` at all** (`title: null` in
 *   the live DOM) — not even a hover answer.
 * - **The watchdog and company entries were 6px wide.** No glyph, no text; the
 *   `p.key === "watchdog"` / `"company"` branches in the old JSX simply had no
 *   icon. Five green dots and one grey dot were the entire subsystem story.
 * - **Tokens and cost shared one `Coins` glyph** — two different units drawn
 *   identically, so neither was identifiable.
 * - **The row had no grouping.** One flat `gap-x-3` flex run mixed a boxed
 *   `Badge` with bare numbers and anonymous dots at the same weight.
 * - **`cost: —` was ambiguous.** `GET /api/console/stats` answers
 *   `total_cost: null, currency: null`. The dash meant "not reported", but the
 *   tooltip said `cost: —`, which reads as a measured `$0.00`.
 *
 * Three rules hold here, and `ui-legibility.test.mjs` asserts them against the
 * rendered markup rather than against this comment:
 *
 * 1. **Every value carries its label, at every width.** No `hidden lg:inline`
 *    on a noun.
 * 2. **Every entry has a `title` naming the unit and the route it came from**,
 *    so the hover answers "what is this, and who measured it".
 * 3. **A dash is never a value on its own.** Where a measurement can be
 *    absent, the adjacent words say which of *zero* / *not reported* /
 *    *not applicable* it is — `absent` is not `0`.
 */

/** Every workspace measurement. `null` always means "the server did not say". */
export interface Vitals {
  /** true = online, false = offline, null = probe failed so status is unknown. */
  online: boolean | null;
  version: string;
  stats: ConsoleStats | null;
  probes: Probe[];
  /** True when the probe request itself failed — counts below are unknown, not zero. */
  probesFailed: boolean;
  host: SystemVitals | null;
  /**
   * The Gateway's own internet-connectivity reading (`GET /api/ops/network`).
   *
   * `null` means that read failed, which is a *different* claim from a link that
   * was measured and found down — so it is carried separately from
   * `connectivityFailed` and the strip words both.
   */
  connectivity: Connectivity | null;
  /** True when the connectivity read itself failed. */
  connectivityFailed: boolean;
}

/**
 * The subsurfaces summarised by the readiness ratio, in display order.
 *
 * Exported so the test can pin the exact set. A subsystem that quietly dropped
 * out of this list would also drop out of the denominator, turning a red row
 * green without anything changing on the server.
 */
export const VITALS_SUBSYSTEM_KEYS = [
  "memory",
  "skills",
  "scheduled",
  "channels",
  "mcp",
  "watchdog",
  "company",
] as const;

/**
 * NOTE — the `key -> glyph` map that used to live here is gone with the header
 * chips it served. Each chip duplicated a workspace view that already exists
 * (Memory / Skills / Scheduled / Channels / System / Companies), so the seven
 * glyphs are no longer rendered anywhere; the readiness cluster keeps only the
 * `6/7 ready` ratio with `Layers`. The System view draws a status dot per
 * probe rather than a glyph, so nothing else in this file needed the mapping.
 * Re-derive it rather than restoring it beside the ratio, where it would put
 * the duplication straight back.
 */

function compactNumber(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/**
 * Cost is the one header number the server can legitimately leave empty, and
 * the possible readings are opposite claims, so they never share a rendering:
 *
 * - a number — the server measured it, including a real `$0.0000`
 * - `null` — the server reported no total at all. The label says **not
 *   reported** in words, because a bare dash next to the word "cost" reads as
 *   a measured zero.
 */
export function costView(cost: number | null, currency: string | null): {
  value: string;
  label: string;
  title: string;
} {
  if (cost === null || !Number.isFinite(cost)) {
    return {
      value: "—",
      label: "cost not reported",
      title:
        "Cost: NOT REPORTED. GET /api/console/stats returned total_cost: null, so the Gateway has no priced " +
        "total for these runs — normally because no model_pricing is configured for the models that served " +
        "them. This dash is NOT a measured $0.00 and NOT a claim that the cost is zero.",
    };
  }
  const symbol = currency ? ` ${currency}` : "";
  return {
    value: `$${cost.toFixed(cost < 1 ? 4 : 2)}${symbol}`,
    label: currency ? `cost in ${currency}` : "cost, no currency reported",
    title: `Cost: ${cost}${currency ? ` ${currency}` : " with no currency reported by the server"}, from GET /api/console/stats (total_cost).`,
  };
}

async function load(): Promise<Vitals> {
  // Three-state probes (same pattern as SettingsSection): a failed probe
  // request is "status unavailable" — never collapsed into an empty/zero view.
  // The connectivity read follows the same rule for the same reason: a Gateway
  // that did not answer about the internet is not a Gateway with no internet.
  const [probesRes, statsRes, version, host, connectivityRes] = await Promise.all([
    probeAll()
      .then((list) => ({ ok: true as const, list }))
      .catch(() => ({ ok: false as const, list: [] as Probe[] })),
    fetchConsoleStats().catch(() => null),
    fetchOpsVersion().catch(() => "unknown"),
    fetchSystemVitals().catch(() => null),
    fetchConnectivity()
      .then((value) => ({ ok: true as const, value }))
      .catch(() => ({ ok: false as const, value: null as Connectivity | null })),
  ]);
  const probes = probesRes.list;
  const gateway = probes.find((p) => p.key === "gateway");
  return {
    online: probesRes.ok ? Boolean(gateway?.ok) : null,
    version,
    stats: statsRes,
    probes,
    probesFailed: !probesRes.ok,
    host,
    connectivity: connectivityRes.value,
    connectivityFailed: !connectivityRes.ok,
  };
}

function formatGiB(mb: number): string {
  if (!Number.isFinite(mb) || mb < 0) return "—";
  // A measured zero is a real answer and gets rendered as one; only a
  // non-finite or negative reading falls back to the dash.
  if (mb === 0) return "0G";
  return `${(mb / 1024).toFixed(1)}G`;
}

/**
 * One measurement: glyph, value, and the noun that says what the number is —
 * **always visible, at every width**. The `title` supplements the label with
 * the unit and the route, so the hover answers the second question.
 */
function Metric({
  icon,
  value,
  label,
  title,
  emphasis = false,
}: {
  icon: React.ReactNode;
  value: string;
  label: string;
  title: string;
  emphasis?: boolean;
}) {
  return (
    <span
      title={title}
      data-vital={label}
      className="inline-flex items-center gap-1 whitespace-nowrap px-2 py-1 first:pl-2.5 last:pr-2.5"
    >
      <span className="text-muted-foreground/70" aria-hidden="true">
        {icon}
      </span>
      <span
        className={`tabular-nums ${emphasis ? "font-semibold text-foreground" : "font-medium text-foreground/90"}`}
      >
        {value}
      </span>
      <span className="text-muted-foreground">{label}</span>
    </span>
  );
}

/**
 * A bordered group of related measurements.
 *
 * Three clusters are what give the row its hierarchy: *is the backend up and
 * how loaded is the box*, *how much work has this workspace done*, *which
 * subsystems are answering right now*. One border style and one separator style
 * across all three, so the row scans as three things rather than sixteen.
 */
/**
 * A bordered group of related measurements.
 *
 * Three clusters are what give the row its hierarchy: *is the backend up and
 * how loaded is the box*, *how much work has this workspace done*, *which
 * subsystems are answering right now*. One border style and one separator style
 * across all three, so the row scans as three things rather than sixteen.
 *
 * When `onOpen` is supplied the cluster becomes a real button that opens the
 * Vitals page, because a strip that only *summarises* is a dead end: the
 * operator's next question is always "show me", and this is where that lands.
 * The whole cluster is the target rather than a separate chevron affordance,
 * so there is exactly one obvious thing to click.
 */
function Cluster({
  label,
  title,
  onOpen,
  children,
}: {
  label: string;
  title: string;
  onOpen?: () => void;
  children: React.ReactNode;
}) {
  const body = (
    <>
      {onOpen && (
        <span className="self-stretch px-1.5 flex items-center text-muted-foreground/60 group-hover/cluster:text-foreground transition-colors" aria-hidden="true">
          <ChevronRight className="size-3.5" />
        </span>
      )}
      {children}
    </>
  );

  if (!onOpen) {
    return (
      <div role="group" aria-label={label} title={title} className="inline-flex items-stretch divide-x divide-border/60 rounded-xl border border-border/60 bg-card/40">
        {body}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={`Open the full Vitals page — ${label}`}
      title={`${title} Click for the full detail, per endpoint and per subsystem.`}
      className="group/cluster inline-flex items-stretch divide-x divide-border/60 rounded-xl border border-border/60 bg-card/40 text-left transition-colors hover:border-primary/40 hover:bg-muted/50 focus:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
    >
      {body}
    </button>
  );
}

export function WorkspaceVitals({
  className = "",
  onOpenVitals,
}: {
  className?: string;
  /**
   * Opens the full Vitals page. Passing it turns the strip's clusters into
   * real navigation: a cluster answers "which thing is this" with one click,
   * and the page behind it holds every measurement the strip summarises.
   */
  onOpenVitals?: () => void;
}) {
  const [vitals, setVitals] = useState<Vitals | null>(null);
  const [loading, setLoading] = useState(true);
  /**
   * A manual "retry now" against `POST /api/ops/network/recheck`.
   *
   * The control is here rather than inside `VitalsStrip` because the fetching
   * half owns both the in-flight guard and the re-read: the strip stays a pure
   * function of `vitals`, which is what makes it renderable in a test with no
   * Gateway and no DOM.
   */
  const [rechecking, setRechecking] = useState(false);
  const [recheckError, setRecheckError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setVitals(await load());
    } finally {
      setLoading(false);
    }
  }, []);

  // Named `onRetryConnectivity`, deliberately NOT `retryConnectivity`: the
  // imported client function already has that name, and a local `const` of the
  // same name is in scope inside its own initialiser — so the call below would
  // have resolved to this very callback and recursed instead of probing anything.
  const onRetryConnectivity = useCallback(async () => {
    // Guarded, not debounced: the guide's rule is that a repeated trigger must
    // not create two records, and a second in-flight probe would be a second
    // reading of the same link rather than anything useful.
    if (rechecking) return;
    setRechecking(true);
    setRecheckError(null);
    try {
      await recheckConnectivity();
      // The recheck's own response is discarded on purpose. The strip renders
      // one source of truth, and re-reading it means the row can never show a
      // value the server did not just confirm.
      await refresh();
    } catch (e) {
      // A rejected call surfaces the server's reason, and the strip is still
      // re-read: a failed probe is a *new* measurement result, not a reason to
      // keep showing the previous one as if it were current.
      setRecheckError(errMsg(e));
      await refresh();
    } finally {
      setRechecking(false);
    }
  }, [rechecking, refresh]);

  useEffect(() => {
    void refresh();
    // Auto-refresh the strip (host RAM included) while the tab is visible.
    const id = window.setInterval(() => {
      if (!document.hidden) void refresh();
    }, 10000);
    return () => window.clearInterval(id);
  }, [refresh]);

  if (loading && !vitals) {
    return (
      <div className={`flex items-center gap-3 text-[11px] text-muted-foreground ${className}`}>
        <Activity className="size-3.5 animate-pulse" />
        <span>Checking backend…</span>
      </div>
    );
  }

  if (!vitals) return null;

  return (
    <VitalsStrip
      vitals={vitals}
      className={className}
      onOpenVitals={onOpenVitals}
      onRetryConnectivity={onRetryConnectivity}
      rechecking={rechecking}
      recheckError={recheckError}
    />
  );
}

/**
 * The manual "retry now" control that sits beside the internet reading.
 *
 * It is offered only when a recheck could actually change the answer: a link
 * that is down, degraded, or unmeasured. A link that is merely *unmeasured
 * because this process has no probe at all* renders as off with the reason in
 * the tooltip instead — a button that could only ever come back with the same
 * refusal implies a pending state that does not exist.
 */
function ConnectivityRetry({
  onRetry,
  pending,
  error,
}: {
  onRetry: () => void;
  pending: boolean;
  error: string | null;
}) {
  return (
    <span className="inline-flex items-center px-2 py-1" data-internet-retry={pending ? "in-flight" : "idle"}>
      <button
        type="button"
        onClick={onRetry}
        // Disabled while in flight: a second click would be a second probe of
        // the same link, not a second useful reading.
        disabled={pending}
        className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60 disabled:hover:bg-muted/40 disabled:hover:text-muted-foreground"
        title={
          error
            ? `The last retry did not run: ${error}`
            : "Ask the backend to measure the link now, through the same probe its own poll loop uses. The backend also keeps retrying on its own schedule."
        }
        aria-label={pending ? "Re-checking the internet connection" : "Retry the internet connection check"}
      >
        <RefreshCw className={`size-3 shrink-0 ${pending ? "animate-spin" : ""}`} aria-hidden="true" />
        <span className="font-medium">{pending ? "Retrying…" : "Retry"}</span>
      </button>
      {error && (
        <span className="ml-1.5 whitespace-nowrap text-muted-foreground" data-internet-retry-error>
          Retry failed — {error}
        </span>
      )}
    </span>
  );
}

/**
 * The pure, data-in half of the strip.
 *
 * Split out so the rendered markup can be asserted without a Gateway, a DOM,
 * or the fetching wrapper. Every number it shows arrived in `vitals`; nothing
 * here invents, defaults, or re-derives a fact.
 */
export function VitalsStrip({
  vitals,
  className = "",
  onOpenVitals,
  onRetryConnectivity,
  rechecking = false,
  recheckError = null,
}: {
  vitals: Vitals;
  className?: string;
  onOpenVitals?: () => void;
  /** Omitted in the pure-render tests, which pass no control at all. */
  onRetryConnectivity?: () => void;
  rechecking?: boolean;
  recheckError?: string | null;
}) {
  const s = vitals.stats;
  const host = vitals.host;
  const subsystems = vitals.probes.filter((p) => (VITALS_SUBSYSTEM_KEYS as readonly string[]).includes(p.key));
  const readyCount = subsystems.filter((p) => p.ok).length;
  // The subsurfaces that did NOT answer, carried up into the ratio's tooltip.
  // The per-subsystem chips used to render each reason in the row; they were
  // removed from the header because every one of them duplicated a workspace
  // view that already exists (Memory / Skills / Scheduled / Channels /
  // System / Companies). The reason itself must not go with them — a failure
  // still has to be readable from the row that counts it, so it moves into the
  // ratio's `title`, and the full rows stay in the System view.
  const failingSubsystems = subsystems.filter((p) => !p.ok);
  const cost = costView(s ? s.cost : null, s ? s.currency : null);
  const link = connectivityView(vitals.connectivity ?? null, vitals.connectivityFailed);

  return (
    <div className={`flex flex-wrap items-center gap-1.5 text-[11px] ${className}`}>
      {/* ── Cluster 1 · is the backend there, and how loaded is the box ─────── */}
      <Cluster
        label="Backend connection"
        onOpen={onOpenVitals}
        title="Measured live: GET /api/features (reachability), GET /api/ops/version, GET /api/system/vitals (host load), GET /api/ops/network (internet link)."
      >
        <span className="inline-flex items-center px-2.5">
          <Badge tone={vitals.probesFailed ? "gray" : vitals.online ? "green" : "red"}>
            <Server className="size-3" />
            {vitals.probesFailed
              ? "Gateway status unavailable"
              : vitals.online
                ? "Gateway online"
                : "Gateway offline"}
          </Badge>
        </span>

        <Metric
          icon={<Cpu className="size-3" />}
          value={`v${vitals.version}`}
          label="Alpha"
          title={`Alpha version ${vitals.version}, reported by GET /api/ops/version. This is the installed build, not the newest published release — the update control beside this strip owns that question.`}
        />

        {/* The internet link, from the durable-runtime connectivity monitor.
            It sits with "is the backend there" rather than with the host-load
            numbers because that is the question it answers, and it carries its
            own Retry because that is the one control here an operator can
            actually press. A healthy row stays quiet: the server's
            "Connectivity confirmed." sentence is not repeated beside a green
            dot, the same rule every passing subsystem row follows. */}
        <span className="inline-flex items-center" data-connectivity-state={vitals.connectivity?.state ?? "unmeasured"}>
          <Metric
            icon={link.tone === "red" || link.tone === "gray" ? <WifiOff className="size-3" /> : <Wifi className="size-3" />}
            value={link.value}
            label={link.label}
            emphasis={link.tone === "red"}
            title={link.title}
          />
          {/* The detail renders whenever there is something to say, including on
              a green row — that is how a stale reading is disclosed instead of
              presented as current. A fresh healthy link produces no sentence at
              all, so the quiet case stays quiet. */}
          {link.detail && (
            <span className="whitespace-nowrap text-muted-foreground" data-connectivity-detail>
              — {link.detail}
            </span>
          )}
          {link.canRetry && onRetryConnectivity && (
            <ConnectivityRetry onRetry={onRetryConnectivity} pending={rechecking} error={recheckError} />
          )}
        </span>

        {host ? (
          <>
            <Metric
              icon={
                <span
                  className={`size-1.5 rounded-full ${
                    host.ram.percent === null
                      ? "bg-muted-foreground/40"
                      : host.ram.percent >= 90
                        ? "bg-red-500"
                        : host.ram.percent >= 70
                          ? "bg-amber-500"
                          : "bg-emerald-500"
                  }`}
                />
              }
              value={`${formatGiB(host.ram.used_mb)}/${formatGiB(host.ram.total_mb)}`}
              label={host.ram.percent === null ? "RAM % not reported" : `RAM ${host.ram.percent.toFixed(0)}%`}
              emphasis
              title={
                host.ram.percent === null
                  ? `Host RAM: ${formatGiB(host.ram.used_mb)} used of ${formatGiB(host.ram.total_mb)} installed. The percentage was not reported by the server, so this is not a measured 0%. From GET /api/system/vitals (memory.ram). Units are GiB, converted from the server's MB.`
                  : `Host RAM: ${formatGiB(host.ram.used_mb)} used of ${formatGiB(host.ram.total_mb)} installed (${host.ram.percent.toFixed(1)}%), from GET /api/system/vitals (memory.ram). Units are GiB, converted from the server's MB.`
              }
            />
            <Metric
              icon={<Gauge className="size-3" />}
              value={`${host.cpu.percent.toFixed(0)}%`}
              label="CPU"
              title={`Host CPU: ${host.cpu.percent.toFixed(1)}% across ${host.cpu.cores} cores, from GET /api/system/vitals (cpu.percent).`}
            />
          </>
        ) : (
          <Metric
            icon={<CircleHelp className="size-3" />}
            value="—"
            label="host load not reported"
            title="Host RAM and CPU: NOT REPORTED. GET /api/system/vitals did not answer, so how loaded the machine running this UI is unknown. This dash is NOT a measured 0%."
          />
        )}
      </Cluster>

      {/* ── Cluster 2 · how much work this workspace has done ───────────────── */}
      <Cluster
        label="Workspace totals"
        onOpen={onOpenVitals}
        title={
          s
            ? "Lifetime totals from GET /api/console/stats. These are the server's own counts, not this UI's, and cost is shown only when the server priced it."
            : "Lifetime totals: NOT REPORTED. GET /api/console/stats did not answer, so every count here is unknown rather than zero."
        }
      >
        {s ? (
          <>
            <Metric icon={<Activity className="size-3" />} value={compactNumber(s.runs)} label="runs" title={`${s.runs} runs recorded on this Gateway, from GET /api/console/stats (total_runs).`} />
            <Metric icon={<Database className="size-3" />} value={compactNumber(s.threads)} label="chats" title={`${s.threads} conversations stored on this Gateway, from GET /api/console/stats (total_threads).`} />
            <Metric
              icon={<Blocks className="size-3" />}
              value={s.agents === null ? "—" : compactNumber(s.agents)}
              label="agents"
              title={
                s.agents === null
                  ? `Custom agent profiles not reported: ${s.agentsReason ?? "GET /api/console/stats returned total_agents: null"}. This is a failed filesystem read, not zero profiles.`
                  : `${s.agents} custom agent profiles, from GET /api/console/stats (total_agents). Zero means none are defined; a failure to read it renders as "not reported" instead.`
              }
            />
            <Metric icon={<Sigma className="size-3" />} value={compactNumber(s.tokens)} label="tokens" title={`${s.tokens} model tokens billed to this workspace, from GET /api/console/stats (total_tokens).`} />
            <Metric icon={<CircleDollarSign className="size-3" />} value={cost.value} label={cost.label} title={cost.title} />
          </>
        ) : (
          // One entry, but the tooltip enumerates each stat individually: a
          // user hovering "not reported" needs to know that *runs*, *chats*,
          // *agents*, *tokens* and *Cost* are all unknown rather than 0, and
          // which route would have supplied them.
          <Metric
            icon={<CircleHelp className="size-3" />}
            value="—"
            label="totals not reported"
            title={
              "NOT REPORTED — GET /api/console/stats did not answer. Runs, chats, agents, tokens and Cost are " +
              "all unknown, NOT zero. This is a failed read, not a workspace with no history: an empty list must " +
              "mean the server said there is nothing, and the server said nothing at all."
            }
          />
        )}
      </Cluster>

      {/* ── Cluster 3 · which subsystems are answering right now ───────────── */}
      <Cluster
        label="Subsystem readiness"
        onOpen={onOpenVitals}
        title={
          vitals.probesFailed
            ? "Subsystem status: UNAVAILABLE. The probe request itself failed, so readiness is unknown — this is neither 0 of 7 nor 7 of 7, because nobody measured it."
            : subsystems.length === 0
              ? // The probe request SUCCEEDED and reported no subsurfaces. That is
                // a real answer — "the server has no subsystems to check" — and
                // rendering it as `0/0` would look like a measured zero of a
                // non-zero denominator. Say what it is.
                "Subsystem readiness: the Gateway reported no subsurfaces to probe. 0 of 0 is what it answered, not a missing measurement and not a failure."
              : `${readyCount} of ${subsystems.length} probed subsystems answered successfully. A subsystem counts as ready only when its own request succeeded.`
        }
      >
        {vitals.probesFailed ? (
          // The wording `load-failure-honesty.test.mjs` pins. It is kept
          // verbatim, and it is also the honest one: the probe request itself
          // failed, so no subsystem was measured and this is not `0/7`.
          <Metric
            icon={<CircleHelp className="size-3" />}
            value=""
            label="Subsystem status unavailable"
            title="Subsystem readiness: UNKNOWN. The probe request failed, so no subsystem was measured. This is NOT 0 of 7, and not a healthy 7 of 7 either — nobody measured anything."
          />
        ) : (
          <Metric
            icon={<Layers className="size-3" />}
            value={subsystems.length === 0 ? "—" : `${readyCount}/${subsystems.length}`}
            label={subsystems.length === 0 ? "ready, none to probe" : "ready"}
            emphasis={subsystems.length > 0 && readyCount < subsystems.length}
            title={
              subsystems.length === 0
                ? "Subsystem readiness: the Gateway reported no subsurfaces to probe, so nothing was measured. This is the server's answer, not a missing reading and not a failing one."
                : `${readyCount} of ${subsystems.length} probed subsystems answered successfully. Counted ready only on a successful response.` +
                  (failingSubsystems.length
                    ? ` Not ready: ${failingSubsystems.map((p) => `${p.label} — ${p.detail}`).join("; ")}.`
                    : " Every probed subsurface answered.") +
                  " Full per-subsystem rows are in the System view."
            }
          />
        )}
      </Cluster>
    </div>
  );
}
