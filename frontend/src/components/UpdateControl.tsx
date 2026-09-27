"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  EvolutionUpdateState,
  getEvolutionUpdateState,
  checkForEvolutionUpdate,
  requestEvolutionUpdate,
  skipEvolutionUpdate,
  recoverEvolutionUpdate,
  canApplyUpdate,
} from "@/lib/evolution";
import { errMsg } from "@/lib/http";
import { RefreshCw, ArrowUpCircle, AlertTriangle, Wrench } from "lucide-react";

/**
 * A small always-visible update control.
 *
 * It reads the *persisted* update state on mount — which performs no network
 * access — and only contacts GitHub when the operator presses the button. A
 * page load must not silently phone home to a third party.
 *
 * Honesty rules this component is built around:
 *
 * - The pill only claims an update exists when the server's own state says
 *   `UPDATE_AVAILABLE`. It never infers one from a version string.
 * - Applying requires the engine's `canApply`, not merely a newer tag. When
 *   that is false the control stays disabled and shows the server's `reason`.
 * - The three mutating routes require a real interactive admin session and
 *   answer 403 under `AGENT_WORKSPACE_AUTH_DISABLED`. That refusal is shown
 *   verbatim; it is never retried into a fake success.
 * - A state the server did not send renders as unknown, not as "up to date".
 */
export function UpdateControl() {
  const [state, setState] = useState<EvolutionUpdateState | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 6000);
  };

  // Persisted state only. This route never reaches the network.
  const loadPersisted = useCallback(async () => {
    try {
      setState(await getEvolutionUpdateState());
      setError(null);
    } catch (err) {
      setError(`The update status could not be read. ${errMsg(err)}`);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void loadPersisted();
  }, [loadPersisted]);

  // Close the popover on an outside click, the same pattern NavTabs uses.
  useEffect(() => {
    if (!open) return;
    function handleClickOutside(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const check = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await checkForEvolutionUpdate();
      setState(result);
      setOpen(true);
      if (result.state === "UPDATE_AVAILABLE") {
        flash(`Update ${result.availableVersion || result.latestTag || ""} is available.`);
      } else if (result.state === "UP_TO_DATE") {
        flash(`Alpha ${result.installedVersion || ""} is up to date.`);
      } else if (result.error) {
        setError(result.error);
      }
    } catch (err) {
      setError(`The update check did not run. ${errMsg(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (busy || !canApplyUpdate(state)) return;
    const target = state?.availableVersion || state?.latestTag || "the verified update";
    if (!window.confirm(`Apply ${target}? Alpha will restart and verify the checkout.`)) return;
    setBusy(true);
    setError(null);
    try {
      // `true` confirms an attended handoff only; the server still enforces
      // canApply, a clean worktree, ancestry, and post-restart health checks.
      const result = await requestEvolutionUpdate(true);
      const id = typeof result.transaction_id === "string" ? result.transaction_id : "";
      flash(id ? `Update transaction ${id} queued. Alpha will restart and verify it.` : "The update was queued.");
      await loadPersisted();
    } catch (err) {
      setError(`The update was not applied. ${errMsg(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    if (busy) return;
    const version = state?.availableVersion || state?.latestTag || "";
    if (!version) {
      setError("There is no verified version to skip.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await skipEvolutionUpdate(version);
      flash(`Update ${version} will be ignored until a newer one is released.`);
      await loadPersisted();
    } catch (err) {
      setError(`The version was not skipped. ${errMsg(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const recover = async () => {
    if (busy) return;
    if (!window.confirm("Restore the backup ref from the last update attempt? The current checkout will be verified.")) return;
    setBusy(true);
    setError(null);
    try {
      await recoverEvolutionUpdate();
      flash("Recovery ran. The persisted state below reflects the result.");
      await loadPersisted();
    } catch (err) {
      setError(`Recovery did not complete. ${errMsg(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const current = state?.state ?? (loaded ? "unknown" : "loading");
  const available = current === "UPDATE_AVAILABLE";
  const failed = current === "CHECK_FAILED";
  const needsRecovery = current === "RECOVERY_REQUIRED";
  const applicable = canApplyUpdate(state);
  const installed = state?.installedVersion || null;
  const skipped = state?.skippedVersions ?? [];

  const tone = available || needsRecovery ? "text-amber-600 dark:text-amber-400" : failed ? "text-destructive" : "text-muted-foreground";
  const label = !loaded
    ? "…"
    : available
      ? `Update ${state?.availableVersion || state?.latestTag || ""}`
      : needsRecovery
        ? "Update interrupted"
        : failed
          ? "Update check failed"
          : installed
            ? `v${installed}`
            : "Update";

  const title = available
    ? applicable
      ? `${installed || "installed"} → ${state?.availableVersion || state?.latestTag}. Click for update options.`
      : `${installed || "installed"} → ${state?.availableVersion || state?.latestTag}, but not applicable yet: ${state?.reason || "the server's safety checks have not passed"}.`
    : failed
      ? state?.error || "The last update check failed."
      : needsRecovery
        ? "An update stopped after staging. Recovery restores the previous checkout."
        : installed
          ? `Installed version ${installed}. Click to check GitHub for a newer release.`
          : "Check GitHub for a newer release.";

  return (
    <div ref={rootRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : void check())}
        disabled={busy}
        aria-expanded={open}
        title={title}
        aria-label={title}
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md hover:bg-muted transition-colors disabled:opacity-50 whitespace-nowrap ${tone}`}
      >
        {available ? (
          <ArrowUpCircle className={`size-3 ${busy ? "animate-spin" : ""}`} />
        ) : failed || needsRecovery ? (
          <AlertTriangle className="size-3" />
        ) : (
          <RefreshCw className={`size-3 ${busy ? "animate-spin" : ""}`} />
        )}
        <span className="text-[11px] font-medium tabular-nums">{label}</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-80 rounded-2xl border border-border/80 bg-card shadow-xl z-50 p-3 space-y-2.5 text-left">
          <div>
            <p className="text-[11px] font-semibold">Software update</p>
            <p className="text-[10px] text-muted-foreground mt-0.5">
              Checks the GitHub release configured for this build.
            </p>
          </div>

          <dl className="space-y-1 text-[11px]">
            <Row label="Installed" value={installed || "not reported"} />
            <Row label="Latest tag" value={state?.latestTag || "not reported"} />
            <Row label="State" value={current} />
            {state?.checkedAt && <Row label="Checked" value={new Date(state.checkedAt).toLocaleString()} />}
            {state?.deploymentMode && <Row label="Deployment" value={state.deploymentMode} />}
            {state?.source && <Row label="Source" value={state.source} />}
            {skipped.length > 0 && <Row label="Skipped" value={skipped.join(", ")} />}
            {state?.backupRef && <Row label="Backup ref" value={state.backupRef} />}
            {state?.transactionId && <Row label="Transaction" value={state.transactionId} />}
          </dl>

          {state?.reason && (
            <p className="text-[10px] text-amber-600 dark:text-amber-300">{state.reason}</p>
          )}
          {state?.error && <p className="text-[10px] text-destructive">{state.error}</p>}
          {state?.stateCorrupt === true && (
            <p className="text-[10px] text-destructive">
              The persisted update state is corrupt and was not trusted, so no update state is claimed.
            </p>
          )}
          {error && <p className="text-[10px] text-destructive">{error}</p>}
          {notice && <p className="text-[10px] text-emerald-600 dark:text-emerald-400">{notice}</p>}

          <div className="flex items-center gap-1.5 flex-wrap pt-0.5">
            <SmallBtn onClick={() => void check()} disabled={busy}>
              <RefreshCw className={`size-3 ${busy ? "animate-spin" : ""}`} /> Check again
            </SmallBtn>
            {available && (
              <>
                <SmallBtn
                  onClick={() => void apply()}
                  disabled={busy || !applicable}
                  title={
                    applicable
                      ? "Queue the verified source update"
                      : state?.reason || "The server has not marked this update applicable."
                  }
                >
                  <ArrowUpCircle className="size-3" /> Apply
                </SmallBtn>
                <SmallBtn onClick={() => void skip()} disabled={busy}>
                  Skip this version
                </SmallBtn>
              </>
            )}
            {needsRecovery && (
              <SmallBtn onClick={() => void recover()} disabled={busy}>
                <Wrench className="size-3" /> Recover
              </SmallBtn>
            )}
          </div>

          {!applicable && available && (
            <p className="text-[10px] text-muted-foreground">
              Apply stays disabled until the server&apos;s own safety checks pass. A newer tag on its own is
              not permission to change the checkout.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function Row(props: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted-foreground shrink-0 w-24">{props.label}</dt>
      <dd className="font-medium break-all min-w-0">{props.value}</dd>
    </div>
  );
}

function SmallBtn(props: {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      disabled={props.disabled}
      title={props.title}
      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[11px] font-medium hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
    >
      {props.children}
    </button>
  );
}
