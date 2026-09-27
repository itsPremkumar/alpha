/**
 * War Room API client: the network layer only.
 *
 * The types and the honesty rules live in ./war-room-model, which imports
 * nothing, so their tests run without a bundler. This file adds the reads.
 *
 * Read-mostly by design: the model-facing `war_room` tool is what opens a room,
 * because opening one spends tokens and a human should usually decide that.
 * These calls exist so a person can watch what happened and judge it afterwards.
 *
 * A failed read throws. It is never turned into an empty list, because "no runs
 * exist" and "the read failed" are different facts and a dashboard must not
 * conflate them.
 */

import { get, send } from "@/lib/http";
import type { Analytics, RunListItem, Transcript, TriggerDecision, TriggerPolicy, WarRoomRun } from "./war-room-model";

export * from "./war-room-model";

export async function fetchWarRooms(opts: { room?: string; limit?: number } = {}): Promise<RunListItem[]> {
  const params = new URLSearchParams();
  if (opts.room) params.set("room", opts.room);
  if (opts.limit) params.set("limit", String(opts.limit));
  const query = params.toString();
  const data = await get<{ ok: boolean; runs: RunListItem[] }>(`/api/war-rooms${query ? `?${query}` : ""}`);
  return data.runs ?? [];
}

export async function fetchWarRoom(runId: string, room?: string): Promise<WarRoomRun> {
  const query = room ? `?room=${encodeURIComponent(room)}` : "";
  const data = await get<{ ok: boolean; run: WarRoomRun }>(`/api/war-rooms/${encodeURIComponent(runId)}${query}`);
  return data.run;
}

export async function fetchWarRoomTranscript(runId: string, room?: string, limit?: number): Promise<Transcript> {
  const params = new URLSearchParams();
  if (room) params.set("room", room);
  if (limit) params.set("limit", String(limit));
  const query = params.toString();
  return get<Transcript>(`/api/war-rooms/${encodeURIComponent(runId)}/transcript${query ? `?${query}` : ""}`);
}

export async function fetchWarRoomAnalytics(room?: string): Promise<Analytics> {
  const query = room ? `?room=${encodeURIComponent(room)}` : "";
  return get<Analytics>(`/api/war-rooms/analytics${query}`);
}

export async function fetchTriggerPolicy(): Promise<TriggerPolicy> {
  const data = await get<{ ok: boolean; policy: TriggerPolicy }>("/api/war-rooms/trigger-policy");
  return data.policy;
}

export async function evaluateWarRoom(topic: string, simulateEnabled = true): Promise<TriggerDecision> {
  const data = await send<{ ok: boolean; decision: TriggerDecision }>("/api/war-rooms/evaluate", "POST", {
    topic,
    simulate_enabled: simulateEnabled,
  });
  return data.decision;
}
