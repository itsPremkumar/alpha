// GENERATED FILE - DO NOT EDIT.
//
// Source of truth: the Pydantic models in the script bridge / gateway
// contract registry.  Regenerate with:
//
//     python -m alpha.wire_contracts.generate --write
//
// generator version: 1

/* eslint-disable */

// ---- module: scriptBridge ----
export interface ScriptBridgeResult {
  status: "ok" | "error";
  mode: "oneshot" | "kernel";
  stdout?: string;
  stderr?: string;
  returncode?: number | null;
  duration_seconds?: number;
  tool_calls?: number;
  denied?: number;
  refused?: number;
  errors?: number;
  dispatcher?: string;
  transport?: string;
  stdout_truncated?: boolean;
  stdout_full_text_path?: string | null;
  stderr_truncated?: boolean;
  stderr_full_text_path?: string | null;
  session_id?: string | null;
  kernel?: Record<string, unknown> | null;
  stats?: unknown | null;
  limits?: unknown | null;
  error?: Record<string, unknown> | null;
  degradations?: Array<string>;
}

// ---- module: streamJson ----
export interface StreamFrame {
  v: number;
  seq: number;
  type: string;
  run_id: string;
}

// ---- module: vault ----
export interface SecretHandle {
  handle: string;
  fingerprint: string;
  operation: string;
  target: string;
  owner: string;
  expires_at: number;
  label?: string;
}
export interface VaultUseResult {
  ok: boolean;
  handle_fingerprint: string;
  operation: string;
  target: string;
  result?: unknown;
  ledger_outcome: string;
}
