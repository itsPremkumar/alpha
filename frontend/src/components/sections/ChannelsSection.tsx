"use client";

import React, { useEffect, useState } from "react";
import { channelStatus, restartChannel, listProviders, listConnections, connectProvider, disconnectConnection, larkStatus } from "@/lib/channels";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Plug, PlugZap, Unplug } from "lucide-react";

export function ChannelsSection() {
  const [channels, setChannels] = useState<Array<{ name: string; enabled: boolean; connected: boolean; status: string }>>([]);
  const [providers, setProviders] = useState<Array<{ id: string; name: string; description: string; configured: boolean }>>([]);
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
      setProviders(p);
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

  const onConnect = async (providerId: string) => {
    try {
      const res = await connectProvider(providerId);
      const url = typeof res.url === "string" ? res.url : typeof res.auth_url === "string" ? res.auth_url : null;
      const code = typeof res.code === "string" ? res.code : typeof res.pairing_code === "string" ? res.pairing_code : null;
      setConnectInfo(url ? `Open this link to finish connecting: ${url}` : code ? `Enter this code in the app: ${code}` : "Follow the provider's instructions, then refresh this page.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

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
            <p className="text-xs font-semibold mb-2">Running channels ({channels.length})</p>
            {channels.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No channels running. Connect a provider below.</p>
            ) : (
              <div className="space-y-1.5">
                {channels.map((c) => (
                  <div key={c.name} className="flex items-center gap-2 rounded-xl bg-muted/40 px-3 py-2">
                    <Plug className="size-3.5 text-primary" />
                    <span className="text-xs font-semibold flex-1">{c.name}</span>
                    <Badge tone={c.connected ? "green" : c.enabled ? "amber" : "gray"}>
                      {c.connected ? "connected" : c.enabled ? c.status || "enabled" : "off"}
                    </Badge>
                    <Btn variant="ghost" onClick={() => restartChannel(c.name).then((m) => flash(m)).catch((e) => setError(errMsg(e)))}>
                      Restart
                    </Btn>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-border/60 bg-card p-4">
            <p className="text-xs font-semibold mb-2">Connect a chat app ({providers.length})</p>
            {providers.length === 0 ? (
              <EmptyState title="No providers listed" hint="The server did not return connectable chat apps." />
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
                      <Btn variant="ghost" onClick={() => onConnect(p.id)}>
                        <PlugZap className="size-3.5" /> {p.configured ? "Reconnect" : "Connect"}
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
                      onClick={() => window.confirm("Remove this link?") && disconnectConnection(c.id).then(load).catch((e) => setError(errMsg(e)))}
                      className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-destructive"
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
