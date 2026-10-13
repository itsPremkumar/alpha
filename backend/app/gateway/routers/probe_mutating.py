"""Probe all mutating routes with no auth header, report HTTP codes."""

import json
import subprocess

GATEWAY = "http://127.0.0.1:8001"

# (method, path_template, body) -- body None = no body; {} = empty json
CASES = [
    # groups
    ("POST", "/api/groups", {"name": "probe-room"}),
    ("POST", "/api/groups", {"name": "dead"}),
    ("POST", "/api/groups/{name}/subgroups", {"name": "probe-room", "topic": ""}),
    ("POST", "/api/groups/{name}/members", {"name": "probe-room", "bot_name": "coder"}),
    ("DELETE", "/api/groups/{name}/members/{bot_name}", {"name": "probe-room", "bot_name": "coder"}),
    ("POST", "/api/groups/{name}/rules", {"name": "probe-room", "field": "role", "op": "eq", "value": "dev"}),
    ("DELETE", "/api/groups/{name}/rules/{rule_id}", {"name": "probe-room", "rule_id": "x"}),
    ("POST", "/api/groups/{name}/messages", {"name": "probe-room", "content": "hi"}),
    ("PATCH", "/api/groups/{name}/messages/{msg_id}", {"name": "probe-room", "content": "hi2"}),
    ("DELETE", "/api/groups/{name}/messages/{msg_id}", {"name": "probe-room", "msg_id": "x"}),
    ("POST", "/api/groups/{name}/move", {"name": "probe-room"}),
    ("POST", "/api/groups/{name}/merge", {"name": "probe-room"}),  # placeholders for template rendering
    # runs
    ("POST", "/api/runs", {}),
    ("POST", "/api/runs", {"input": "hi"}),
    ("PUT", "/api/runs", {}),
    # threads
    ("POST", "/api/threads", {}),
    ("PUT", "/api/threads", {}),
    ("DELETE", "/api/threads", {}),
    ("POST", "/api/threads/{thread_id}", {"name": "x"}),
    ("PATCH", "/api/threads/{thread_id}", {"name": "x"}),
    ("DELETE", "/api/threads/{thread_id}", {}),
    ("POST", "/api/threads/{thread_id}/move", {}),
    ("POST", "/api/threads/{thread_id}/undo", {}),
    ("POST", "/api/threads/{thread_id}/compact", {}),
    ("POST", "/api/threads/{thread_id}/goal", {}),
    ("PUT", "/api/threads/{thread_id}/goal", {}),
    ("DELETE", "/api/threads/{thread_id}/goal", {}),
    ("POST", "/api/threads/{thread_id}/state", {}),
    ("POST", "/api/threads/{thread_id}/history", {}),
    # bots
    ("POST", "/api/bots", {}),
    ("PUT", "/api/bots/{name}", {"name": "x"}),
    ("DELETE", "/api/bots/{name}", {"name": "x"}),
    # channels
    ("POST", "/api/channels", {}),
    ("POST", "/api/channels", {"provider": "slack"}),
    ("DELETE", "/api/channels", {}),
    ("PUT", "/api/channels/{id}", {"id": "x"}),
    # bots sub
    ("POST", "/api/bots/{name}/ensure", {"name": "x"}),
    ("POST", "/api/bots/{name}/pause", {"name": "x"}),
    ("POST", "/api/bots/{name}/resume", {"name": "x"}),
    ("POST", "/api/bots/{name}/heartbeat", {"name": "x"}),
    ("DELETE", "/api/bots/{name}/model-config", {"name": "x"}),
    # apex
    ("POST", "/api/apex", {}),
    ("DELETE", "/api/apex", {}),
    # commands
    ("POST", "/api/commands", {}),
    # projects
    ("POST", "/api/projects", {}),
    ("PATCH", "/api/projects/{id}", {"id": "x"}),
    ("DELETE", "/api/projects/{id}", {"id": "x"}),
    ("PUT", "/api/projects/{id}", {"id": "x"}),
    # agents (subagents)
    ("POST", "/api/agents", {}),
    ("PUT", "/api/agents/{name}", {"name": "x"}),
    ("DELETE", "/api/agents/{name}", {"name": "x"}),
    # skills
    ("POST", "/api/skills", {}),
    ("PUT", "/api/skills/{name}", {"name": "x"}),
    ("DELETE", "/api/skills/{name}", {"name": "x"}),
    # swarms
    ("POST", "/api/swarms", {}),
    ("POST", "/api/swarms/{id}", {"id": "x"}),
    # jobs
    ("POST", "/api/jobs", {}),
    # scheduled tasks
    ("POST", "/api/scheduled-tasks", {}),
    ("PATCH", "/api/scheduled-tasks/{id}", {"id": "x"}),
    ("DELETE", "/api/scheduled-tasks/{id}", {"id": "x"}),
    # credentials
    ("POST", "/api/credentials", {}),
    ("DELETE", "/api/credentials/{id}", {"id": "x"}),
    # integrations
    ("POST", "/api/integrations", {}),
    # missions
    ("POST", "/api/missions", {}),
    # workflows
    ("POST", "/api/workflows", {}),
    # memory
    ("POST", "/api/memory", {}),
    ("DELETE", "/api/memory/{id}", {"id": "x"}),
    # feedback
    ("PUT", "/api/threads", {}),
    ("POST", "/api/threads", {}),
    ("DELETE", "/api/threads", {}),
    # companies
    ("POST", "/api/companies", {}),
    ("PATCH", "/api/companies/{id}", {"id": "x"}),
    ("DELETE", "/api/companies/{id}", {"id": "x"}),
    # evidence
    ("POST", "/api/evidence", {}),
    # policy
    ("POST", "/api/policy", {}),
    ("DELETE", "/api/policy", {}),
    # group coordination
    ("POST", "/api/groups/{name}/goals", {"name": "probe-room"}),
    ("PATCH", "/api/groups/{name}/goals/{gid}", {"name": "probe-room", "gid": "x"}),
    ("POST", "/api/groups/{name}/coordination", {"name": "probe-room"}),
    # uploads
    ("POST", "/api/uploads", {}),
    # side effects
    ("POST", "/api/side-effects", {}),
    # checkpoints
    ("POST", "/api/checkpoints", {}),
    # vision (multimodal)
    ("POST", "/api/multimodal", {}),
    # subagent control
    ("POST", "/api/subagents/control", {}),
    # subgoals/policy
    ("POST", "/api/deliberation", {}),
    # rooms / notifications
    ("POST", "/api/groups/{name}/messages/{msg_id}/pin", {"name": "probe-room", "msg_id": "x"}),
    ("DELETE", "/api/groups/{name}/messages/{msg_id}/pin", {"name": "probe-room", "msg_id": "x"}),
    ("POST", "/api/groups/{name}/typing", {"name": "probe-room"}),
    ("POST", "/api/groups/{name}/reconcile", {"name": "probe-room"}),
    ("POST", "/api/groups/{name}/activity/heartbeat", {"name": "probe-room"}),
    ("POST", "/api/groups/{name}/claims", {"name": "probe-room"}),
    ("DELETE", "/api/groups/{name}/claims/{claim_id}", {"name": "probe-room", "claim_id": "x"}),
    ("POST", "/api/groups/{name}/project-link", {"name": "probe-room"}),
]


def send(method, path, body):
    url = GATEWAY + path
    data = None
    if body is not None:
        data = json.dumps(body)
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", "6", "-X", method, url, "-H", "Content-Type: application/json", "-w", "\n__HTTP_CODE:%{http_code}__", "-o", "/tmp/curl_body"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # simpler: use curl with -d and capture
        cmd = ["curl", "-s", "-m", "6", "-X", method, url, "-o", "/tmp/curl_body"]
        if data is not None:
            cmd += ["-d", data]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        code = r.stdout.rsplit("__HTTP_CODE:", 1)[-1].strip() if "__HTTP_CODE:" in r.stdout else "?"
        body_text = open("/tmp/curl_body", encoding="utf-8", errors="replace").read()
        return code, body_text[:160]
    except Exception as e:
        return "ERR", str(e)[:160]


def main():
    for method, path, body in CASES:
        code, txt = send(method, path, body)
        print(f"{method:6s} {path:58s} -> {code}  {txt}")


if __name__ == "__main__":
    main()
