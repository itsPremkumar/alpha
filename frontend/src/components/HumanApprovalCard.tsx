"use client";

import React, { useState } from "react";
import { ShieldAlert, Check, X, Terminal } from "lucide-react";
import { HumanApproval } from "@/types/chat";

interface HumanApprovalCardProps {
  approval: HumanApproval;
  onDecision: (approved: boolean) => void;
}

export function HumanApprovalCard({ approval, onDecision }: HumanApprovalCardProps) {
  const [decided, setDecided] = useState<boolean | null>(null);

  const handleAction = (approved: boolean) => {
    setDecided(approved);
    onDecision(approved);
  };

  return (
    <div className="my-3 p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/5 text-xs space-y-2.5">
      <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400 font-semibold">
        <ShieldAlert className="size-4" />
        <span>Permission Required: {approval.toolName}</span>
      </div>

      <p className="text-muted-foreground leading-relaxed">
        {approval.prompt || `The agent requires your authorization to execute the sensitive tool ${approval.toolName}.`}
      </p>

      {approval.args && Object.keys(approval.args).length > 0 && (
        <div className="p-2 rounded-lg bg-background/80 border border-border/80 font-mono text-[11px] overflow-x-auto max-h-36">
          <div className="flex items-center gap-1.5 text-muted-foreground mb-1">
            <Terminal className="size-3" />
            <span>Parameters</span>
          </div>
          <pre>{JSON.stringify(approval.args, null, 2)}</pre>
        </div>
      )}

      {decided === null ? (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => handleAction(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 text-white font-medium hover:bg-emerald-700 transition-colors"
          >
            <Check className="size-3.5" />
            <span>Approve & Execute</span>
          </button>
          <button
            type="button"
            onClick={() => handleAction(false)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-destructive text-destructive-foreground font-medium hover:opacity-90 transition-opacity"
          >
            <X className="size-3.5" />
            <span>Reject</span>
          </button>
        </div>
      ) : (
        <div className={`text-xs font-medium ${decided ? "text-emerald-500" : "text-destructive"}`}>
          {decided ? "✓ Authorized by user" : "✗ Rejected by user"}
        </div>
      )}
    </div>
  );
}
