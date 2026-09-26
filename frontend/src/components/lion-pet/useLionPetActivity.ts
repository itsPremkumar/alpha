"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LionPetState } from "./lion-pet-model";

const DEFAULT_MESSAGES: Record<LionPetState, string> = {
  idle: "The desk is quiet. I'm here when you need me.",
  thinking: "Paw-sing the problem...",
  working: "I'm on it. Roaring quietly.",
  waiting: "I found a decision point for you.",
  success: "Task complete. Nice work, team.",
  error: "That path needs another look. I kept the draft safe.",
  sleeping: "Resting my paws for a moment.",
};

type UseLionPetActivityOptions = {
  isLoading: boolean;
  hasApproval: boolean;
};

export function useLionPetActivity({ isLoading, hasApproval }: UseLionPetActivityOptions) {
  const [state, setState] = useState<LionPetState>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const update = useCallback((nextState: LionPetState, nextMessage?: string, resetAfterMs = 0) => {
    setState(nextState);
    setMessage(nextMessage || DEFAULT_MESSAGES[nextState]);
    if (resetTimerRef.current) {
      clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
    }
    if (resetAfterMs > 0) {
      resetTimerRef.current = setTimeout(() => {
        setState("idle");
        setMessage(null);
        resetTimerRef.current = null;
      }, resetAfterMs);
    }
  }, []);

  useEffect(() => {
    if (!isLoading && hasApproval) update("waiting");
  }, [hasApproval, isLoading, update]);

  useEffect(() => () => {
    if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
  }, []);

  return { state, message, update };
}
