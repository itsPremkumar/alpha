"use client";

import React, { useEffect, useState } from "react";
import { listAgents, createAgent, updateAgent, deleteAgent, fetchUserProfile, saveUserProfile, CustomAgent } from "@/lib/agents";
import { Section, EmptyState, ErrorBox, Notice, Btn, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Trash2, RefreshCw } from "lucide-react";

export function AgentsSection(props: { enabled: boolean }) {
  const [agents, setAgents] = useState<CustomAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", description: "", model: "", soul: "" });
  const [showForm, setShowForm] = useState(false);
  const [profile, setProfile] = useState("");
  const [profileLoaded, setProfileLoaded] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [a, p] = await Promise.all([listAgents(), fetchUserProfile()]);
      setAgents(a);
      if (p !== null) {
        setProfile(p);
        setProfileLoaded(true);
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (props.enabled) load();
    else setLoading(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.enabled]);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const onCreate = async () => {
    if (!form.name.trim()) {
      setError("Give the agent a name (letters, digits, hyphens).");
      return;
    }
    try {
      await createAgent({ name: form.name.trim().toLowerCase(), description: form.description.trim(), model: form.model.trim() || undefined, soul: form.soul.trim() || undefined });
      setForm({ name: "", description: "", model: "", soul: "" });
      setShowForm(false);
      flash("Agent created.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  if (!props.enabled) {
    return (
      <Section title="Custom agents" hint="Build your own persistent personas with their own instructions.">
        <EmptyState
          title="Agent management is switched off"
          hint="Ask your administrator to set agents_api.enabled=true in config.yaml and restart the server — then this page unlocks."
        />
      </Section>
    );
  }

  return (
    <Section
      title="Custom agents"
      hint="Your own persistent personas — each with its own description, model and personality notes (soul). Chat with them by name."
      actions={
        <>
          <Btn variant="ghost" onClick={load}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <Btn onClick={() => setShowForm((v) => !v)}>
            <Plus className="size-3.5" /> New agent
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      {showForm && (
        <div className="rounded-2xl border border-primary/30 bg-card p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Name" hint="Lowercase, hyphens ok.">
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="my-assistant" className={`${inputCls} font-mono`} />
            </Field>
            <Field label="Description">
              <input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="What is it for?" className={inputCls} />
            </Field>
            <Field label="Model (optional)" hint="Leave blank for default.">
              <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="default" className={`${inputCls} font-mono`} />
            </Field>
          </div>
          <Field label="Personality notes (soul)" hint="How should it behave? Tone, rules, style.">
            <textarea value={form.soul} onChange={(e) => setForm({ ...form, soul: e.target.value })} rows={3} className={inputCls} placeholder="You are concise, friendly…" />
          </Field>
          <div className="flex gap-2">
            <Btn onClick={onCreate}>Create agent</Btn>
            <Btn variant="ghost" onClick={() => setShowForm(false)}>Cancel</Btn>
          </div>
        </div>
      )}

      {loading ? (
        <SkeletonList rows={3} />
      ) : agents.length === 0 ? (
        <EmptyState title="No custom agents" hint="Create your first one above." />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {agents.map((a) => (
            <AgentCard key={a.name} agent={a} onChanged={load} onError={setError} />
          ))}
        </div>
      )}

      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2">
        <p className="text-xs font-semibold">About you (USER.md)</p>
        <p className="text-[11px] text-muted-foreground">One profile injected into every custom agent — background, preferences, how to address you.</p>
        <textarea value={profile} onChange={(e) => { setProfile(e.target.value); setProfileLoaded(true); }} rows={4} placeholder="I am… I prefer…" className={inputCls} />
        <Btn onClick={() => saveUserProfile(profile).then(() => flash("Profile saved.")).catch((e) => setError(errMsg(e)))} disabled={!profileLoaded && !profile}>
          Save profile
        </Btn>
      </div>
    </Section>
  );
}

function AgentCard(props: { agent: CustomAgent; onChanged: () => void; onError: (m: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [desc, setDesc] = useState(props.agent.description);
  const [soul, setSoul] = useState(props.agent.soul);

  const save = async () => {
    try {
      await updateAgent(props.agent.name, { description: desc, soul });
      setEditing(false);
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    }
  };

  return (
    <div className="rounded-xl border border-border/60 bg-card p-3.5">
      <p className="text-sm font-semibold font-mono">{props.agent.display_name || props.agent.name}</p>
      {!editing ? (
        <>
          <p className="text-[11px] text-muted-foreground mt-1">{props.agent.description || "No description."}</p>
          {props.agent.model && <p className="text-[10px] font-mono text-muted-foreground mt-1">model: {props.agent.model}</p>}
          {props.agent.soul && <p className="text-[11px] text-muted-foreground mt-1.5 line-clamp-3 whitespace-pre-wrap">{props.agent.soul}</p>}
          <div className="flex gap-2 mt-2.5">
            <Btn variant="ghost" onClick={() => { setDesc(props.agent.description); setSoul(props.agent.soul); setEditing(true); }}>Edit</Btn>
            <Btn variant="danger" onClick={() => window.confirm(`Delete agent "${props.agent.name}" and all its files?`) && deleteAgent(props.agent.name).then(props.onChanged).catch((e) => props.onError(errMsg(e)))}>
              <Trash2 className="size-3.5" /> Delete
            </Btn>
          </div>
        </>
      ) : (
        <div className="space-y-2 mt-2">
          <Field label="Description">
            <input value={desc} onChange={(e) => setDesc(e.target.value)} className={inputCls} />
          </Field>
          <Field label="Personality notes (soul)">
            <textarea value={soul} onChange={(e) => setSoul(e.target.value)} rows={3} className={inputCls} />
          </Field>
          <div className="flex gap-2">
            <Btn onClick={save}>Save</Btn>
            <Btn variant="ghost" onClick={() => setEditing(false)}>Cancel</Btn>
          </div>
        </div>
      )}
    </div>
  );
}
