"""Enterprise Security Enclave, Spatio-Temporal Memory, and Goal Pursuit tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.security.enclave import (
    BoundaryViolationError,
    BoundingBox,
    CheckpointCrypto,
    CheckpointIntegrityError,
    CredentialRedactor,
    DeceptionWatchdog,
    EnclaveGoalHarness,
    ScopedCredentialVault,
    SpatialObject,
    SpatioTemporalCache,
    TaskBoundaryPolicy,
    TrajectoryFlightRecorder,
)

_BOUNDARY = TaskBoundaryPolicy()
_CRYPTO = CheckpointCrypto()
_FLIGHT_RECORDER = TrajectoryFlightRecorder()
_CREDENTIAL_VAULT = ScopedCredentialVault()
_REDACTOR = CredentialRedactor()
_SPATIAL_CACHE = SpatioTemporalCache()
_ACTIVE_GOAL_HARNESS: EnclaveGoalHarness | None = None


@tool("enterprise_security_manage", parse_docstring=True)
def enterprise_security_manage(
    action: str,
    path: str = "",
    url_or_host: str = "",
    payload_json: str = "{}",
    encrypted_blob: str = "",
    actor: str = "agent",
    telemetry_action: str = "",
    service_name: str = "",
    scopes_csv: str = "read",
    raw_text: str = "",
    agent_statement: str = "",
    tool_output: str = "",
    exit_code: int = 0,
    object_label: str = "",
    screen_context: str = "",
    goal_statement: str = "",
    milestones_csv: str = "",
    bbox_json: str = "{}",
) -> str:
    """Manage enterprise security controls, enclave boundaries, spatio-temporal memory, and autonomous goal pursuit.

    Args:
        action: 'check_path', 'check_network', 'encrypt_checkpoint', 'decrypt_checkpoint', 'log_telemetry', 'verify_telemetry', 'issue_credential', 'redact_text', 'audit_deception', 'ingest_frame', 'query_spatial_memory', 'init_goal_pursuit', 'pursue_goal_step', 'get_goal_status'.
        path: File/directory path to check against boundary enclaves.
        url_or_host: Target domain/URL to check against outbound whitelist.
        payload_json: JSON data to encrypt for checkpoint or log for telemetry.
        encrypted_blob: Base64 AES-GCM ciphertext to decrypt.
        actor: Name of actor for telemetry recording.
        telemetry_action: Action name for telemetry recording.
        service_name: Service name for scoped credential lease.
        scopes_csv: Comma-separated scopes for credential lease (e.g. 'repo:read,build').
        raw_text: Text to scrub secrets from.
        agent_statement: Agent's claim to audit for deception.
        tool_output: Actual command/tool output to verify against agent claim.
        exit_code: Exit code of command.
        object_label: Name of object to index or query in spatial memory (e.g. 'glasses', 'error_dialog').
        screen_context: Description of UI/screen context perceived.
        goal_statement: High-level goal statement for autonomous goal pursuit.
        milestones_csv: Comma-separated milestone descriptions.
        bbox_json: JSON object with keys x, y, width, height for spatial grounding.
    """
    global _ACTIVE_GOAL_HARNESS

    if action == "check_path":
        try:
            resolved = _BOUNDARY.validate_path(path)
            return json.dumps({"status": "allowed", "resolved_path": resolved}, indent=2)
        except BoundaryViolationError as e:
            return json.dumps({"status": "blocked", "error": str(e)}, indent=2)

    elif action == "check_network":
        try:
            allowed = _BOUNDARY.validate_network_target(url_or_host)
            return json.dumps({"status": "allowed" if allowed else "unlisted_domain", "target": url_or_host}, indent=2)
        except BoundaryViolationError as e:
            return json.dumps({"status": "blocked", "error": str(e)}, indent=2)

    elif action == "encrypt_checkpoint":
        try:
            data = json.loads(payload_json)
        except Exception:
            data = {"raw": payload_json}
        b64 = _CRYPTO.encrypt_json(data)
        return json.dumps({"status": "encrypted", "cipher_b64": b64}, indent=2)

    elif action == "decrypt_checkpoint":
        try:
            obj = _CRYPTO.decrypt_json(encrypted_blob)
            return json.dumps({"status": "decrypted", "data": obj}, indent=2)
        except CheckpointIntegrityError as e:
            return json.dumps({"status": "corrupted_or_tampered", "error": str(e)}, indent=2)

    elif action == "log_telemetry":
        try:
            details = json.loads(payload_json) if payload_json else {}
        except Exception:
            details = {}
        entry = _FLIGHT_RECORDER.record_step(actor=actor, action=telemetry_action, details=details)
        return json.dumps({"status": "logged", "entry": entry.to_dict()}, indent=2)

    elif action == "verify_telemetry":
        valid, bad_idx = _FLIGHT_RECORDER.verify_integrity()
        return json.dumps({
            "chain_valid": valid,
            "corrupted_step": bad_idx,
            "total_steps": len(_FLIGHT_RECORDER.entries),
        }, indent=2)

    elif action == "issue_credential":
        scopes = [s.strip() for s in scopes_csv.split(",") if s.strip()]
        lease = _CREDENTIAL_VAULT.issue_lease(service_name=service_name, scopes=scopes)
        return json.dumps({"status": "issued", "lease": lease.to_dict()}, indent=2)

    elif action == "redact_text":
        clean = _REDACTOR.redact(raw_text)
        return json.dumps({"redacted_text": clean}, indent=2)

    elif action == "audit_deception":
        deceptive, msg = DeceptionWatchdog.audit_step(
            agent_statement=agent_statement,
            tool_action="audit",
            tool_output=tool_output,
            exit_code=exit_code,
        )
        return json.dumps({
            "is_deceptive": deceptive,
            "diagnostic": msg,
        }, indent=2)

    elif action == "ingest_frame":
        objects: list[SpatialObject] = []
        if object_label:
            bbox = BoundingBox()
            if bbox_json:
                try:
                    b_dict = json.loads(bbox_json)
                    bbox = BoundingBox(
                        x=float(b_dict.get("x", 0.0)),
                        y=float(b_dict.get("y", 0.0)),
                        width=float(b_dict.get("width", 0.0)),
                        height=float(b_dict.get("height", 0.0)),
                    )
                except Exception:
                    pass
            objects.append(SpatialObject(label=object_label, bbox=bbox))

        frame = _SPATIAL_CACHE.ingest_frame(
            objects=objects,
            screen_context=screen_context,
            ambient_text=raw_text,
        )
        return json.dumps({"status": "frame_ingested", "frame": frame.to_dict()}, indent=2)

    elif action == "query_spatial_memory":
        matches = _SPATIAL_CACHE.find_object_history(object_label)
        latest = _SPATIAL_CACHE.get_most_recent_location(object_label)
        return json.dumps({
            "query_label": object_label,
            "match_count": len(matches),
            "most_recent_location": latest,
            "history": matches,
        }, indent=2)

    elif action == "init_goal_pursuit":
        _ACTIVE_GOAL_HARNESS = EnclaveGoalHarness(
            goal_statement=goal_statement or "Autonomous Goal",
            spatial_cache=_SPATIAL_CACHE,
        )
        if milestones_csv:
            for m in milestones_csv.split(","):
                m_str = m.strip()
                if m_str:
                    _ACTIVE_GOAL_HARNESS.add_milestone(title=m_str, description=m_str)

        return json.dumps({
            "status": "goal_initialized",
            "goal": _ACTIVE_GOAL_HARNESS.to_dict(),
        }, indent=2)

    elif action == "pursue_goal_step":
        if _ACTIVE_GOAL_HARNESS is None:
            _ACTIVE_GOAL_HARNESS = EnclaveGoalHarness(
                goal_statement=goal_statement or "Autonomous Task",
                spatial_cache=_SPATIAL_CACHE,
            )
            _ACTIVE_GOAL_HARNESS.add_milestone(title=telemetry_action or "Execute Step", description="Step execution")

        objects: list[SpatialObject] = []
        if object_label:
            bbox = BoundingBox()
            if bbox_json:
                try:
                    b_dict = json.loads(bbox_json)
                    bbox = BoundingBox(
                        x=float(b_dict.get("x", 0.0)),
                        y=float(b_dict.get("y", 0.0)),
                        width=float(b_dict.get("width", 0.0)),
                        height=float(b_dict.get("height", 0.0)),
                    )
                except Exception:
                    pass
            objects.append(SpatialObject(label=object_label, bbox=bbox))

        result = _ACTIVE_GOAL_HARNESS.pursue_step(
            action_name=telemetry_action or "pursue_action",
            step_input={"statement": agent_statement},
            tool_result=tool_output,
            observed_objects=objects if objects else None,
            screen_context=screen_context,
        )
        return json.dumps(result, indent=2)

    elif action == "get_goal_status":
        if _ACTIVE_GOAL_HARNESS is None:
            return json.dumps({"status": "no_active_goal"}, indent=2)
        return json.dumps(_ACTIVE_GOAL_HARNESS.to_dict(), indent=2)

    else:
        return json.dumps({
            "error": f"Unknown action '{action}'."
        }, indent=2)


# Transparent alias for backwards compatibility
astra_security_manage = enterprise_security_manage
