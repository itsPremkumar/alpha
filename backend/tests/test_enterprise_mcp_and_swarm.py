"""Tests for Universal MCP Gateway, Multi-Agent SDLC, and Federated Swarm Engine."""

from __future__ import annotations

import json
import time
import pytest

from alpha.mcp.gateway import (
    RoleTier,
    SecretScrubber,
    TransportType,
    UniversalMCPGateway,
)
from alpha.workflow.sdlc_engine import DocumentGatedSDLCEngine, SDLCStage
from alpha.blackboard.federated_blackboard import FederatedBlackboard, PheromoneType
from alpha.swarm.cnp_auction import (
    ContractNetAuctionEngine,
    SwarmWorkerAgent,
    TaskAnnouncement,
)


def test_secret_scrubber():
    raw_text = "API Key: sk-1234567890abcdef1234567890abcdef, GitHub: ghp_111122223333444455556666777788889999"
    scrubbed = SecretScrubber.scrub(raw_text)

    assert "sk-1234567890abcdef" not in scrubbed
    assert "[REDACTED_OPENAI_KEY:" in scrubbed
    assert "ghp_11112222" not in scrubbed
    assert "[REDACTED_GITHUB_PAT:" in scrubbed

    # Test dictionary scrubbing
    payload = {
        "headers": {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThis"},
        "aws_key": "AKIAIOSFODNN7EXAMPLE",
        "clean_field": "Hello World",
    }
    scrubbed_dict = SecretScrubber.scrub(payload)
    assert "[REDACTED_JWT_TOKEN:" in scrubbed_dict["headers"]["Authorization"]
    assert "[REDACTED_AWS_ACCESS_KEY:" in scrubbed_dict["aws_key"]
    assert scrubbed_dict["clean_field"] == "Hello World"


def test_mcp_gateway_semantic_discovery_and_rbac():
    gw = UniversalMCPGateway(transport=TransportType.STDIO)

    # Register tools
    gw.register_tool(
        name="read_file",
        description="Reads contents of a file from disk",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=lambda path: f"content of {path}",
        required_role=RoleTier.READ_ONLY,
        tags=["file", "read", "filesystem"],
    )
    gw.register_tool(
        name="execute_terminal_command",
        description="Executes arbitrary shell command on host",
        input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        handler=lambda cmd: f"output of {cmd}",
        required_role=RoleTier.OPERATOR,
        tags=["shell", "terminal", "bash", "execute"],
    )

    # 1. Semantic top-k tool discovery
    req_list = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/list",
        "params": {"query": "filesystem read file", "top_k": 1},
    }
    res_list = gw.handle_jsonrpc_request(req_list, caller_role=RoleTier.ADMIN)
    tools = res_list["result"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "read_file"

    # 2. RBAC Enforcement: READ_ONLY caller cannot invoke OPERATOR tool
    call_bad_role = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "tools/call",
        "params": {"name": "execute_terminal_command", "arguments": {"cmd": "ls"}},
    }
    res_bad = gw.handle_jsonrpc_request(call_bad_role, caller_role=RoleTier.READ_ONLY)
    assert "error" in res_bad
    assert "Forbidden" in res_bad["error"]["message"]

    # 3. RBAC Enforcement: OPERATOR caller CAN invoke tool
    res_good = gw.handle_jsonrpc_request(call_bad_role, caller_role=RoleTier.OPERATOR)
    assert "result" in res_good
    assert res_good["result"]["isError"] is False


def test_document_gated_multi_agent_sdlc():
    engine = DocumentGatedSDLCEngine()
    result = engine.run_pipeline(
        requirements="Build an autonomous caching microservice with TTL eviction.",
        project_name="MicroCache",
    )

    assert result.success is True
    assert result.final_stage == SDLCStage.COMPLETED
    assert "prd" in result.artifacts
    assert "architecture" in result.artifacts
    assert "project_plan" in result.artifacts
    assert "implementation" in result.artifacts
    assert "qa_report" in result.artifacts

    # Verify gates
    assert result.artifacts["prd"].is_valid is True
    assert result.artifacts["architecture"].is_valid is True
    assert result.artifacts["qa_report"].is_valid is True


def test_federated_blackboard_and_stigmergy():
    board = FederatedBlackboard()

    # 1. Topic sharded writes & reads
    board.write_entry("cluster:backend", "db_schema", {"tables": ["users", "orders"]}, agent_id="agent-1")
    val = board.read_entry("cluster:backend", "db_schema")
    assert val == {"tables": ["users", "orders"]}

    # 2. Concurrency leases
    lease_acquired = board.claim_lease("resource:users_table", agent_id="worker-A", duration_seconds=10.0)
    assert lease_acquired is True

    # Another agent cannot claim the same active lease
    lease_rejected = board.claim_lease("resource:users_table", agent_id="worker-B", duration_seconds=10.0)
    assert lease_rejected is False

    # Owner releases lease
    board.release_lease("resource:users_table", agent_id="worker-A")
    lease_reacquired = board.claim_lease("resource:users_table", agent_id="worker-B", duration_seconds=10.0)
    assert lease_reacquired is True

    # 3. Digital Stigmergy (Pheromone decay)
    board.deposit_trace("resource:hot_endpoint", PheromoneType.FAULT_HOTSPOT, intensity=5.0, decay_rate=1.0)
    curr_intensity = board.get_intensity("resource:hot_endpoint", PheromoneType.FAULT_HOTSPOT)
    assert curr_intensity > 0.0

    heatmap = board.get_stigmergic_heatmap()
    assert "resource:hot_endpoint" in heatmap


def test_contract_net_protocol_swarm_auction():
    board = FederatedBlackboard()
    auction = ContractNetAuctionEngine(blackboard=board)

    # Register 3 specialized swarm workers
    worker_python = SwarmWorkerAgent("worker-py-1", capabilities=["python", "backend", "fastapi"])
    worker_ts = SwarmWorkerAgent("worker-ts-1", capabilities=["typescript", "frontend", "react"])
    worker_busy = SwarmWorkerAgent("worker-py-2", capabilities=["python", "backend"], base_load=0.9)

    auction.register_workers_batch([worker_python, worker_ts, worker_busy])

    # Announce backend task
    task = TaskAnnouncement(
        task_id="task-42",
        topic="backend",
        description="Implement user session token validator",
        domain_tags=["python", "backend"],
        token_budget=2000,
    )

    award = auction.conduct_auction(task)
    assert award is not None
    # worker_python has high relevance and low load -> wins auction over busy worker and TS worker
    assert award.contractor_agent_id == "worker-py-1"
    assert award.lease_acquired is True

    # Execute task and verify blackboard result
    res = auction.execute_and_report(task, award)
    assert res["status"] == "completed"

    stored_res = board.read_entry("cluster:backend", "result:task-42")
    assert stored_res is not None
    assert stored_res["status"] == "completed"


def test_cnp_auction_empty_domain_tags():
    board = FederatedBlackboard()
    auction = ContractNetAuctionEngine(blackboard=board)
    worker = SwarmWorkerAgent("general-worker", capabilities=["general"])
    auction.register_worker(worker)

    task = TaskAnnouncement(
        task_id="task-empty-tags",
        topic="general",
        description="General maintenance task with no tags",
        domain_tags=[],
        token_budget=1000,
    )

    # Should not raise ZeroDivisionError
    award = auction.conduct_auction(task)
    assert award is not None
    assert award.contractor_agent_id == "general-worker"


def test_mcp_gateway_async_tool_handler():
    gw = UniversalMCPGateway(transport=TransportType.STDIO)

    async def async_fetch_info(item_id: str) -> dict:
        return {"id": item_id, "status": "active"}

    gw.register_tool(
        name="fetch_info",
        description="Async info fetcher",
        input_schema={"type": "object", "properties": {"item_id": {"type": "string"}}},
        handler=async_fetch_info,
        required_role=RoleTier.READ_ONLY,
    )

    req = {
        "jsonrpc": "2.0",
        "id": "async-1",
        "method": "tools/call",
        "params": {"name": "fetch_info", "arguments": {"item_id": "item-123"}},
    }
    res = gw.handle_jsonrpc_request(req, caller_role=RoleTier.DEVELOPER)
    assert "result" in res
    assert res["result"]["isError"] is False
    assert "item-123" in res["result"]["content"][0]["text"]
