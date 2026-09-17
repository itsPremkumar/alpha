"use client";

import React, { useEffect, useState } from "react";
import { listSkills, setSkillEnabled, reloadSkills, installSkill, listProposals, approveProposal, rejectProposal, Skill, SkillProposal } from "@/lib/skills";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, SkeletonList, Field, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Search, RefreshCw, Download } from "lucide-react";

export function SkillsSection() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [proposals, setProposals] = useState<SkillProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [installName, setInstallName] = useState("");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, p] = await Promise.all([listSkills(), listProposals()]);
      setSkills(s);
      setProposals(p);
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

  const toggle = async (s: Skill) => {
    try {
      const updated = await setSkillEnabled(s.name, !s.enabled);
      setSkills((prev) => prev.map((x) => (x.name === s.name ? updated : x)));
      flash(`${s.name} ${updated.enabled ? "enabled" : "disabled"}.`);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const filtered = skills.filter((s) =>
    `${s.name} ${s.description}`.toLowerCase().includes(search.toLowerCase())
  );
  const enabledCount = skills.filter((s) => s.enabled).length;

  return (
    <Section
      title="Skills"
      hint="Skills are abilities the agent can use — web research, presentations, spreadsheets and more. Switch off anything you don't need."
      actions={
        <Btn variant="ghost" onClick={() => reloadSkills().then((m) => flash(m)).catch((e) => setError(errMsg(e)))}>
          <RefreshCw className="size-3.5" /> Reload
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      {proposals.length > 0 && (
        <div className="rounded-2xl border border-amber-500/40 bg-amber-500/5 p-4 space-y-2">
          <p className="text-xs font-semibold">Review requested ({proposals.length}) — the agent proposes new skills, you approve them.</p>
          {proposals.map((p) => (
            <div key={p.id} className="flex items-center gap-2 flex-wrap rounded-xl bg-card border border-border/60 px-3 py-2">
              <div className="flex-1 min-w-40">
                <p className="text-xs font-semibold">{p.name}</p>
                <p className="text-[11px] text-muted-foreground">{p.description}</p>
              </div>
              <Badge tone="amber">{p.status}</Badge>
              <Btn onClick={() => approveProposal(p.id).then(load).catch((e) => setError(errMsg(e)))}>Approve</Btn>
              <Btn variant="ghost" onClick={() => rejectProposal(p.id).then(load).catch((e) => setError(errMsg(e)))}>Reject</Btn>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative flex-1">
          <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search skills…" className={`${inputCls} pl-8`} aria-label="Search skills" />
        </div>
        <span className="text-[11px] text-muted-foreground self-center">{enabledCount} of {skills.length} enabled</span>
      </div>

      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <Field label="Install a skill by name" hint="The name of a skill pack, e.g. a team-shared skill.">
          <div className="flex gap-2">
            <input value={installName} onChange={(e) => setInstallName(e.target.value)} placeholder="skill-name…" className={inputCls} aria-label="Skill name to install" />
            <Btn onClick={() => installSkill(installName.trim()).then((m) => { flash(m); setInstallName(""); load(); }).catch((e) => setError(errMsg(e)))} disabled={!installName.trim()}>
              <Download className="size-3.5" /> Install
            </Btn>
          </div>
        </Field>
      </div>

      {loading ? (
        <SkeletonList rows={5} />
      ) : filtered.length === 0 ? (
        <EmptyState title="No skills found" hint={skills.length === 0 ? "The server reported no skills." : "Try a different search."} />
      ) : (
        <div className="space-y-2">
          {filtered.map((s) => (
            <div key={s.name} className="rounded-xl border border-border/60 bg-card px-4 py-2.5 flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold font-mono truncate">{s.name}</p>
                {s.description && <p className="text-[11px] text-muted-foreground line-clamp-2">{s.description}</p>}
                <div className="flex gap-1.5 mt-1">
                  {s.source && <Badge tone="gray">{s.source}</Badge>}
                  {s.version && <Badge tone="gray">v{s.version}</Badge>}
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={s.enabled}
                aria-label={`${s.enabled ? "Disable" : "Enable"} skill ${s.name}`}
                onClick={() => toggle(s)}
                className={`relative w-10 h-6 rounded-full transition-colors shrink-0 ${s.enabled ? "bg-primary" : "bg-muted"}`}
              >
                <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${s.enabled ? "left-[18px]" : "left-0.5"}`} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
