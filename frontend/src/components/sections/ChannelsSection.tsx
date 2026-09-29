"use client";

import React, { useEffect, useState } from "react";
import { channelStatus, restartChannel, listProviders, listConnections, connectProvider, disconnectConnection, larkStatus, type ChannelProviderCatalog } from "@/lib/channels";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Plug, PlugZap, Unplug } from "lucide-react";

export function ChannelsSection() {
  const [channels, setChannels] = useState<Array<{ name: string; enabled: boolean; connected: boolean; status: string }>>([]);
  const [providerCatalog, setProviderCatalog] = useState<ChannelProviderCatalog>({ enabled: null, providers: [] });
  const providers = providerCatalog.providers;
  const [connections, setConnections] = useState<Array<{ id: string; provider: string; label: string; status: string }>>([]);
  const [lark, setLark] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [connectInfo, setConnectInfo] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, p, con, l] = await Promise.all([channelStatus(), listProviders(), listConnections(), larkStatus()]);
      setChannels(c);
      setProviderCatalog(p);
      setConnections(con);
      setLark(l);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  // Every mutating control in this section is disabled while its own request is
  // in flight, keyed by action so one control's request never freezes the rest
  // of the page. The connect POST is the sharp one — it mints a pairing code
  // server-side, so an unguarded double-click left two connect attempts behind.
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const isBusy = (key: string) => busyAction !== null;
  const busyKey = (action: string, id: string) => `${action}:${id}`;

  const onConnect = async (providerId: string) => {
    if (busyAction) return;
    setBusyAction(busyKey("connect", providerId));
    try {
      const res = await connectProvider(providerId);
      const url = typeof res.url === "string" ? res.url : typeof res.auth_url === "string" ? res.auth_url : null;
      const code = typeof res.code === "string" ? res.code : typeof res.pairing_code === "string" ? res.pairing_code : null;
      setConnectInfo(url ? `Open this link to finish connecting: ${url}` : code ? `Enter this code in the app: ${code}` : "Follow the provider's instructions, then refresh this page.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyAction(null);
    }
  };

  const onRestart = async (name: string) => {
    if (busyAction) return;
    setBusyAction(busyKey("restart", name));
    try {
      const message = await restartChannel(name);
      flash(message);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyAction(null);
    }
  };

  const onDisconnect = async (connectionId: string) => {
    if (busyAction) return;
    setBusyAction(busyKey("unplug", connectionId));
    try {
      // Success is confirmed by the re-read, not painted optimistically: a 2xx
      // on the DELETE is followed by load() so the list reflects the server.
      await disconnectConnection(connectionId);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyAction(null);
    }
  };

  const runningChannels = channels.filter((c) => c.connected).length;

  return (
    <Section
      title="Channels & integrations"
      hint="Talk to the agent from Telegram, Slack, Discord, Feishu and more. Connect an app below — some need a code or a browser confirmation."
      actions={
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}
      {connectInfo && <Notice message={connectInfo} />}

      {loading ? (
        <SkeletonList rows={4} />
      ) : (
        <>
          <div className="rounded-2xl border border-border/60 bg-card p-4">
            {/* The heading counted EVERY channel the Gateway knows about, so a
                default install — 10 channels, all `enabled:false, running:false`
                — read "Running channels (10)". The count and the claim are now
                separate, and a zero-running roster says so. */}
            <p className="text-xs font-semibold mb-2">
              Channels ({channels.length}) — {runningChannels} running
            </p>
            {channels.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                The Gateway reports no channels at all. Connect a provider below.
              </p>
            ) : runningChannels === 0 ? (
              <p className="text-[11px] text-muted-foreground">
                None of these {channels.length} channels is running. Enable one in{" "}
                <code>config.yaml</code> to receive messages there.
              </p>
            ) : null}
            {channels.length > 0 && (
              <div className="space-y-1.5">
                {channels.map((c) => (
                  <div key={c.name} className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2">
                    <Plug className="size-3.5 text-primary" />
                    <span className="text-xs font-semibold flex-1">{c.name}</span>
                    <Badge tone={c.connected ? "green" : c.enabled ? "amber" : "gray"}>
                      {c.connected ? "connected" : c.enabled ? c.status || "enabled" : "off"}
                    </Badge>
                    <Btn
                      variant="ghost"
                      disabled={isBusy(busyKey("restart", c.name))}
                      onClick={() => onRestart(c.name)}
                    >
                      {busyAction === busyKey("restart", c.name) ? "Restarting…" : "Restart"}
                    </Btn>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <p className="text-xs font-semibold mb-2">Connect a chat app ({providers.length})</p>
            {/* `enabled: false` from GET /channels/providers is the reason the
                provider list is empty (the backend filters the catalog to the
                enabled ones), so an empty list while the switch is off means
                "switched off", not "nothing to connect". */}
            {providers.length === 0 && providerCatalog.enabled === false ? (
              <EmptyState
                title="Channel connections are switched off"
                hint="The Gateway reports channel_connections as disabled, which is why no chat apps are listed. Turn the subsystem on in config.yaml to connect one."
              />
            ) : providers.length === 0 ? (
              <EmptyState
                title="No providers listed"
                hint={
                  providerCatalog.enabled === null
                    ? "The Gateway did not report whether channel connections are enabled, and listed no connectable chat apps."
                    : "The server did not return connectable chat apps."
                }
              />
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {providers.map((p) => (
                  <div key={p.id} className="rounded-xl border border-border/60 p-3">
                    <div className="flex items-center gap-2">
                      <p className="text-xs font-semibold flex-1">{p.name || p.id}</p>
                      <Badge tone={p.configured ? "green" : "gray"}>{p.configured ? "linked" : "not linked"}</Badge>
                    </div>
                    {p.description && <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{p.description}</p>}
                    <div className="mt-2">
                      <Btn variant="ghost" onClick={() => onConnect(p.id)} disabled={isBusy(busyKey("connect", p.id))}>
                        <PlugZap className="size-3.5" />{" "}
                        {busyAction === busyKey("connect", p.id) ? "Connecting…" : p.configured ? "Reconnect" : "Connect"}
                      </Btn>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {connections.length > 0 && (
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-2">Active links ({connections.length})</p>
              <div className="space-y-1.5">
                {connections.map((c) => (
                  <div key={c.id} className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2">
                    <span className="text-xs font-medium flex-1 truncate">{c.label || c.provider}</span>
                    <Badge tone="blue">{c.status || c.provider}</Badge>
                    <button
                      type="button"
                      // A second DELETE for a link the server already removed 404s,
                      // so the unlink is guarded like the other mutations.
                      disabled={isBusy(busyKey("unplug", c.id))}
                      onClick={() => window.confirm("Remove this link?") && onDisconnect(c.id)}
                      className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-destructive disabled:opacity-40"
                      title="Remove link"
                    >
                      <Unplug className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {lark && (
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-1.5">Feishu / Lark</p>
              <pre className="text-[11px] font-mono whitespace-pre-wrap rounded-xl bg-muted/40 p-3">{JSON.stringify(lark, null, 2).slice(0, 2000)}</pre>
            </div>
          )}
        </>
      )}
    </Section>
  );
}
