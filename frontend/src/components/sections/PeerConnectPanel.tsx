"use client";

// The Connect panel: one copyable string out, one pasted string in.
//
// This is the surface the whole feature exists for. The old tab asked an operator
// to reveal a pairing code, copy it, then separately type a URL into a second
// field on the other machine — two actions in two places, in an order that had to
// be got right. Here it is one string and one box.
//
// Three rules the implementation is built around:
//
// 1. **The paste decides what happens.** `onPaste` inspects the clipboard before
//    the textarea sees it: a connection string previews and offers Connect, an
//    image is decoded for a QR code, anything else falls through untouched. A
//    handler that ate every paste would break ordinary text entry.
// 2. **Every refusal names the field.** "Invalid invite" tells nobody which of
//    seven fields to fix, and the server already does better than that — the
//    client mirror keeps the same wording so the preview and the real error agree.
// 3. **Copy is labelled by what it contains.** A full invite and an address-only
//    string are both one click apart, and conflating them would either leak a
//    bearer credential into a public channel or make a private handoff
//    needlessly manual.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Link2, QrCode, RefreshCw, ScanLine, ShieldAlert, Upload, X } from "lucide-react";
import { Badge, Btn, ErrorBox, Field, Notice, inputCls } from "@/components/ui";
import {
  buildInvite,
  pairPeer,
  redeemInvite,
  type Peer,
  type PeerInvite,
  type PeerNetworkStatus,
} from "@/lib/peer-network";
import {
  INVITE_PREFIX,
  InviteParseError,
  describeExpiry,
  looksLikeInvite,
  parseInvite,
  redeemabilityProblem,
  unwrapPastedText,
  type ParsedInvite,
} from "@/lib/invite";
import * as qr from "@/lib/qr-encode";
import type { QrMatrix } from "@/lib/qr-encode";
import { canDecodeQr } from "@/lib/qr-decode";
import { useCopyButton } from "@/lib/use-copy-button";
import { errMsg } from "@/lib/http";

type Tab = "share" | "join";

export interface PeerConnectPanelProps {
  status: PeerNetworkStatus | null;
  /** Called after a successful redeem so the parent re-reads the peer list. */
  onConnected: (peer: Peer | null) => void;
}

export function PeerConnectPanel({ status, onConnected }: PeerConnectPanelProps) {
  const [tab, setTab] = useState<Tab>("share");
  const enabled = status?.enabled === true;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1 rounded-xl bg-muted/60 p-1 w-fit">
        <TabButton active={tab === "share"} onClick={() => setTab("share")}>
          Share this Alpha
        </TabButton>
        <TabButton active={tab === "join"} onClick={() => setTab("join")}>
          Add a friend
        </TabButton>
      </div>

      {!enabled && (
        <Notice
          tone="warn"
          message="The Alpha peer network is off, so nothing here can connect. Set ALPHA_PEER_NETWORK_ENABLED=1 and restart the Gateway."
        />
      )}

      {tab === "share" ? (
        <ShareInvite enabled={enabled} />
      ) : (
        <JoinInvite enabled={enabled} onConnected={onConnected} />
      )}
    </div>
  );
}

function TabButton(props: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      aria-pressed={props.active}
      className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
        props.active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {props.children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Share                                                               */
/* ------------------------------------------------------------------ */

function ShareInvite({ enabled }: { enabled: boolean }) {
  const [invite, setInvite] = useState<PeerInvite | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const fullCopy = useCopyButton();
  const addressCopy = useCopyButton();

  const load = useCallback(
    async (includeSecret: boolean) => {
      setBusy(true);
      try {
        setInvite(await buildInvite({ includeSecret }));
        setError(null);
      } catch (cause) {
        setError(errMsg(cause));
        setInvite(null);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  // Minting consumes an invite number, so this runs once per mount of the panel
  // rather than on every render. A full invite and an address-only string are two
  // separate reads because they are two different secrets.
  useEffect(() => {
    if (enabled) void load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  if (!enabled) {
    return (
      <div className="rounded-xl border border-border/70 bg-card/50 px-3 py-2.5 text-xs text-muted-foreground">
        Connection sharing is unavailable while the peer network is disabled.
      </div>
    );
  }

  const matrix = useMemo(() => {
    if (!showQr || !invite?.invite) return null;
    try {
      return qr.encodeQr(invite.invite);
    } catch {
      // A code that cannot be rendered is reported by the button below, not by
      // throwing out of a render — an invite of ~400 bytes always fits, and a
      // failure here means the encoder and the codec disagree.
      return null;
    }
  }, [showQr, invite?.invite]);

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border/70 bg-card px-3 py-3 space-y-3">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <div className="min-w-0">
            <div className="text-sm font-semibold flex items-center gap-1.5">
              <Link2 className="size-3.5" />
              Your connection string
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              One string carries your address{invite?.includes_pairing_code ? " and pairing code" : ""}. Send it to your
              friend over any channel you trust.
            </p>
          </div>
          {invite?.includes_pairing_code ? (
            <Badge tone="amber" title="This string can pair a peer as you.">
              contains your pairing code
            </Badge>
          ) : (
            <Badge tone="green" title="No pairing code — safe to post publicly.">
              safe to share publicly
            </Badge>
          )}
        </div>

        {error ? (
          <ErrorBox message={error} onRetry={() => void load(true)} />
        ) : !invite ? (
          <div className="h-16 animate-pulse rounded-lg bg-muted/50" />
        ) : (
          <>
            <div className="rounded-lg bg-muted/60 px-2.5 py-2 font-mono text-[11px] break-all select-all max-h-24 overflow-y-auto">
              {invite.invite}
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              <Btn onClick={() => void fullCopy.copy(invite.invite)} disabled={fullCopy.busy} ariaLabel="Copy the full connection string">
                {fullCopy.copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                {fullCopy.copied ? "Copied" : "Copy invite"}
              </Btn>
              <Btn
                variant="ghost"
                onClick={async () => {
                  // Minting a separate address-only string rather than stripping
                  // `k=` client-side: the code should never travel to the browser
                  // in a form the UI then has to remember not to show.
                  await load(false);
                  setShowQr(false);
                }}
                disabled={busy}
              >
                <Copy className="size-3.5" />
                Copy address only
              </Btn>
              <Btn variant="ghost" onClick={() => void load(true)} disabled={busy} ariaLabel="Mint a fresh connection string">
                <RefreshCw className={`size-3.5 ${busy ? "animate-spin" : ""}`} />
                Renew
              </Btn>
              <Btn variant="ghost" onClick={() => setShowQr((value) => !value)} disabled={!invite.invite}>
                <QrCode className="size-3.5" />
                {showQr ? "Hide QR" : "Show QR"}
              </Btn>
            </div>

            {invite.includes_pairing_code && (
              <div className="flex items-center gap-2 flex-wrap">
                <Btn
                  onClick={async () => {
                    const ok = await fullCopy.copy(invite.invite);
                    setShowQr(true);
                    if (!ok) {
                      // The QR is showing either way, so the operator has a way
                      // forward even when the clipboard was blocked.
                      setShowQr(true);
                    }
                  }}
                  disabled={busy || fullCopy.busy}
                >
                  <QrCode className="size-3.5" />
                  Copy &amp; show QR
                </Btn>
              </div>
            )}

            {fullCopy.failed && (
              <Notice tone="warn" message="The browser blocked clipboard access. Select the text above and copy it manually." />
            )}
            {invite.includes_pairing_code && (
              <Notice
                tone="warn"
                message="Anyone holding this string can connect to this Alpha as a peer. Share it privately, and ask for a fresh one afterwards — it works only once."
              />
            )}
            {invite.note && <Notice tone="neutral" message={invite.note} />}
            {invite.expires_at !== null && (
              <div className="text-[11px] text-muted-foreground">
                {describeExpiry(invite.expires_at)} · single use
              </div>
            )}
          </>
        )}
      </div>

      {showQr && (
        <div className="rounded-xl border border-border/70 bg-card px-3 py-3">
          <div className="text-[11px] font-semibold mb-2 flex items-center gap-1.5">
            <QrCode className="size-3.5" />
            Scan to connect
          </div>
          {matrix ? (
            <QrView matrix={matrix} />
          ) : (
            <Notice tone="warn" message="This connection string is too long to render as a QR code. Copy the text above instead." />
          )}
          <p className="text-[11px] text-muted-foreground mt-2">
            Your friend pastes this text with <span className="font-medium">Add a friend</span>. It works once.
          </p>
        </div>
      )}
    </div>
  );
}

/** The module grid as one merged SVG path plus the mandatory quiet zone. */
function QrView({ matrix }: { matrix: QrMatrix }) {
  const size = qr.qrViewBoxSize(matrix);
  return (
    <div className="flex justify-center">
      <svg
        role="img"
        aria-label="QR code containing this Alpha's connection string"
        viewBox={`0 0 ${size} ${size}`}
        className="h-56 w-56 rounded-lg bg-white p-0"
        shapeRendering="crispEdges"
      >
        <rect width={size} height={size} fill="#ffffff" />
        {/* The path coordinates start at module (0,0); the quiet zone is the border
            drawn by the white rect, so the group shifts by exactly one zone. */}
        <g transform={`translate(${qr.QUIET_ZONE} ${qr.QUIET_ZONE})`}>
          <path d={qr.qrToSvgPath(matrix)} fill="#000000" />
        </g>
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Join                                                                */
/* ------------------------------------------------------------------ */

function JoinInvite({ enabled, onConnected }: { enabled: boolean; onConnected: (peer: Peer | null) => void }) {
  const [raw, setRaw] = useState("");
  const [parsed, setParsed] = useState<ParsedInvite | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [manualEndpoint, setManualEndpoint] = useState("");
  const [manualCode, setManualCode] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  /**
   * The pre-existing manual route, kept working alongside the invite flow.
   *
   * It matters because reading a QR code is not enabled yet: without this, a
   * deployment that cannot paste would have no way in at all. It goes through
   * the same `POST /pair` and the same admin-gated status, so it grants nothing
   * the invite path does not.
   */
  const pairManually = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const peer = await pairPeer({ endpoint: manualEndpoint.trim(), pairing_code: manualCode.trim() });
      setNotice(`Connected to ${peer.name || peer.agent_id}.`);
      setManualEndpoint("");
      setManualCode("");
      onConnected(peer);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  }, [manualEndpoint, manualCode, onConnected]);

  const problem = parsed ? redeemabilityProblem(parsed) : null;

  const inspect = useCallback((value: string) => {
    setRaw(value);
    if (!value.trim()) {
      setParsed(null);
      setParseError(null);
      return;
    }
    try {
      const next = parseInvite(value);
      setParsed(next);
      setParseError(null);
    } catch (cause) {
      setParsed(null);
      // A refusal names its field. Anything else is reported as itself rather
      // than flattened into "invalid", which would hide a bug in this mirror.
      setParseError(cause instanceof InviteParseError ? cause.message : `could not read that string: ${errMsg(cause)}`);
    }
  }, []);

  const connect = useCallback(async () => {
    if (!parsed) return;
    setBusy(true);
    setError(null);
    try {
      const result = await redeemInvite(parsed.pairing_code ? unwrapPastedText(raw) : unwrapPastedText(raw));
      setNotice(result.note);
      setRaw("");
      setParsed(null);
      onConnected(result.peer);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  }, [parsed, raw, onConnected]);

  /**
   * Route a paste by what it actually contains.
   *
   * An image is decoded elsewhere and its decoded string re-enters through
   * `inspect`; text that is not an invite falls through to the textarea so
   * ordinary typing is unaffected.
   */
  const onPaste = useCallback(
    (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
      const items = event.clipboardData?.items;
      if (items) {
        for (const item of Array.from(items)) {
          if (item.kind === "file" && item.type.startsWith("image/")) {
            event.preventDefault();
            const file = item.getAsFile();
            if (file) void decodeImageFile(file, inspect, setParseError);
            return;
          }
        }
      }
      const text = event.clipboardData?.getData("text") ?? "";
      if (looksLikeInvite(text)) {
        event.preventDefault();
        inspect(unwrapPastedText(text));
        return;
      }
      // Not a connection string: let the textarea have it.
    },
    [inspect],
  );

  const onFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      await decodeImageFile(file, inspect, setParseError);
    },
    [inspect],
  );

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border/70 bg-card px-3 py-3 space-y-3">
        <div className="flex items-start justify-between gap-2 flex-wrap">
          <div>
            <div className="text-sm font-semibold flex items-center gap-1.5">
              <ScanLine className="size-3.5" />
              Paste your friend's connection string
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              Paste the <span className="font-mono">{INVITE_PREFIX}…</span> string your friend copied, or type the
              address and code by hand below.
            </p>
          </div>

          <details className="rounded-lg border border-border/70 bg-muted/30 px-2.5 py-2">
            <summary className="cursor-pointer text-[11px] font-semibold">Enter the address and code by hand</summary>
            <div className="mt-2 space-y-2">
              <Field label="Gateway endpoint" hint="For example http://192.168.1.20:8001">
                <input
                  className={inputCls}
                  value={manualEndpoint}
                  onChange={(event) => setManualEndpoint(event.target.value)}
                  placeholder="http://192.168.1.20:8001"
                  spellCheck={false}
                />
              </Field>
              <Field label="Their pairing code">
                <input
                  className={inputCls}
                  type="password"
                  value={manualCode}
                  onChange={(event) => setManualCode(event.target.value)}
                  spellCheck={false}
                />
              </Field>
              <Btn
                variant="ghost"
                onClick={() => void pairManually()}
                disabled={!enabled || busy || !manualEndpoint.trim() || manualCode.trim().length < 16}
              >
                <Link2 className="size-3.5" />
                Pair with these details
              </Btn>
            </div>
          </details>
          <Btn
            variant="ghost"
            onClick={() => setScanning(true)}
            disabled={!enabled || scanning || !canDecodeQr()}
            title={
              canDecodeQr()
                ? "Scan a QR code with this device's camera"
                : "QR scanning is not available in this build yet — paste the connection string instead"
            }
          >
            <QrCode className="size-3.5" />
            {scanning ? "Scanning…" : "Scan QR"}
          </Btn>
        </div>

        <Field label="Connection string">
          <textarea
            className={`${inputCls} min-h-20 font-mono text-[11px]`}
            value={raw}
            onChange={(event) => inspect(event.target.value)}
            onPaste={onPaste}
            placeholder={`${INVITE_PREFIX}?v=1&a=…`}
            spellCheck={false}
          />
        </Field>

        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => void onFile(event.target.files?.[0])}
        />
        <Btn
          variant="ghost"
          onClick={() => fileRef.current?.click()}
          disabled={!enabled || !canDecodeQr()}
          title={
            canDecodeQr()
              ? "Read a connection string from a screenshot"
              : "QR scanning is not available in this build yet — paste the connection string instead"
          }
        >
          <Upload className="size-3.5" />
          Choose a screenshot
        </Btn>

        {!canDecodeQr() && (
          <Notice
            tone="neutral"
            message="Reading a QR code is not enabled in this build yet. Pasting the connection string works now, and the code you share displays normally."
          />
        )}

        {parseError && <Notice tone="warn" message={parseError} />}

        {parsed && (
          <div className="rounded-lg border border-border/70 bg-muted/40 px-2.5 py-2 space-y-1">
            <div className="text-[11px] font-semibold">About to connect to</div>
            <div className="text-xs font-medium">{parsed.name || parsed.agent_id}</div>
            <div className="text-[11px] text-muted-foreground font-mono break-all">{parsed.agent_id}</div>
            <div className="text-[11px] text-muted-foreground font-mono break-all">{parsed.url}</div>
            <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground">
              <span>{describeExpiry(parsed.expires_at)}</span>
              {parsed.epoch !== null && <span>· invite #{parsed.epoch}</span>}
              <span>· {parsed.pairing_code ? "carries a pairing code" : "address only"}</span>
            </div>
          </div>
        )}

        {problem && <Notice tone="warn" message={problem} />}

        {error ? (
          <ErrorBox message={error} onRetry={() => void connect()} />
        ) : notice ? (
          <Notice tone="success" message={notice} />
        ) : null}

        <div className="flex items-center gap-2">
          <Btn onClick={() => void connect()} disabled={!enabled || busy || !parsed || problem !== null}>
            {busy ? "Connecting…" : "Connect"}
          </Btn>
          {parsed && (
            <Btn variant="ghost" onClick={() => { setRaw(""); setParsed(null); setParseError(null); }} ariaLabel="Clear the pasted string">
              <X className="size-3.5" />
              Clear
            </Btn>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-border/70 bg-card/50 px-3 py-2.5 text-[11px] text-muted-foreground flex items-start gap-2">
        <ShieldAlert className="size-3.5 shrink-0 mt-0.5" />
        <span>
          Connecting registers a peer that can send messages into this Alpha. It does not let that peer run anything here
          or spend this installation&apos;s budget — those stay off until you grant them.
        </span>
      </div>

      {scanning && (
        <QrScanner
          onClose={() => setScanning(false)}
          onDecoded={(text) => {
            setScanning(false);
            inspect(text);
          }}
        />
      )}
    </div>
  );
}

/**
 * Decode a QR code from an image file, entirely in the browser.
 *
 * The file path exists *because* the camera route needs a permissions-policy
 * change: this one needs nothing, so it works on every deployment today. A build
 * that ships only the camera scanner would make QR connection unreachable behind
 * a header this project sets deliberately.
 */
async function decodeImageFile(
  file: File,
  inspect: (value: string) => void,
  setParseError: (message: string | null) => void,
): Promise<void> {
  try {
    const { decodeQrFromImageFile } = await import("@/lib/qr-decode");
    const text = await decodeQrFromImageFile(file);
    if (!text) {
      setParseError("No QR code was found in that image. A screenshot of the whole code, including its white border, works best.");
      return;
    }
    inspect(text);
  } catch (cause) {
    setParseError(`Could not read that image: ${errMsg(cause)}`);
  }
}

/**
 * Live camera scanning.
 *
 * Every failure here is a *permission or device* outcome and each is reported in
 * words: `getUserMedia` rejects for a denied permission, an insecure origin, or a
 * missing device, and those are different problems with different fixes. A single
 * generic "camera unavailable" would leave an operator on HTTPS with a working
 * camera convinced the feature is broken.
 */
function QrScanner(props: { onClose: () => void; onDecoded: (text: string) => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(true);

  useEffect(() => {
    let stream: MediaStream | null = null;
    let frame = 0;
    let cancelled = false;

    const start = async () => {
      try {
        if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
          setError(
            window.isSecureContext === false
              ? "This page is not served over HTTPS, so the browser will not grant camera access. Use “Choose a screenshot” instead."
              : "This browser does not expose camera access. Use “Choose a screenshot” instead.",
          );
          return;
        }
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (cancelled) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play();
        setStarting(false);

        const { decodeQrFromImageData } = await import("@/lib/qr-decode");
        const tick = () => {
          if (cancelled) return;
          const canvas = canvasRef.current;
          const context = canvas?.getContext("2d", { willReadFrequently: true });
          if (canvas && context && video.readyState >= 2 && video.videoWidth > 0) {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            try {
              const text = decodeQrFromImageData(context.getImageData(0, 0, canvas.width, canvas.height));
              if (text) {
                stream?.getTracks().forEach((track) => track.stop());
                props.onDecoded(text);
                return;
              }
            } catch {
              // A frame that does not decode is the normal case, not an error.
            }
          }
          frame = window.requestAnimationFrame(tick);
        };
        frame = window.requestAnimationFrame(tick);
      } catch (cause) {
        const name = cause instanceof DOMException ? cause.name : "";
        setError(
          name === "NotAllowedError"
            ? "Camera access was declined. Allow it in your browser's site settings, or use “Choose a screenshot” instead."
            : name === "NotFoundError" || name === "OverconstrainedError"
              ? "No camera was found on this device. Use “Choose a screenshot” instead."
              : name === "NotReadableError"
                ? "The camera is already in use by another application. Close it, or use “Choose a screenshot” instead."
                : `The camera could not be started: ${errMsg(cause)} Use “Choose a screenshot” instead.`,
        );
      }
    };

    void start();
    return () => {
      cancelled = true;
      if (frame) window.cancelAnimationFrame(frame);
      // Releasing the track on unmount is the difference between closing the
      // dialog and leaving a hardware indicator light on for the rest of the day.
      stream?.getTracks().forEach((track) => track.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="rounded-xl border border-border/70 bg-card px-3 py-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] font-semibold flex items-center gap-1.5">
          <ScanLine className="size-3.5" />
          Point the camera at your friend&apos;s QR code
        </div>
        <Btn variant="ghost" onClick={props.onClose} ariaLabel="Close the scanner">
          <X className="size-3.5" />
        </Btn>
      </div>
      <div className="relative rounded-lg overflow-hidden bg-black">
        <video ref={videoRef} playsInline muted className="w-full h-64 object-cover" />
        {starting && !error && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-white/80">
            Starting the camera…
          </div>
        )}
      </div>
      <canvas ref={canvasRef} className="hidden" />
      {error ? (
        <Notice tone="warn" message={error} />
      ) : (
        <p className="text-[11px] text-muted-foreground">
          Decoding happens in this browser tab. No image is uploaded anywhere.
        </p>
      )}
    </div>
  );
}