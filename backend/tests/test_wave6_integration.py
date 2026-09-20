import json

from alpha.tools.builtins import (
    astra_security_manage,
    manage_mission_hierarchy,
    schedule_work_queue,
    trace_artifact_lineage,
)


def test_manage_mission_hierarchy_tool():
    # 1. Create Goal
    res_goal = manage_mission_hierarchy.invoke({
        "action": "create_goal",
        "name": "Platform Scaling",
        "description": "Scale platform to 1M daily queries",
    })
    data_goal = json.loads(res_goal)
    assert data_goal["status"] == "created"
    goal_id = data_goal["goal"]["id"]

    # 2. Create Mission
    res_mission = manage_mission_hierarchy.invoke({
        "action": "create_mission",
        "parent_id": goal_id,
        "name": "Database Sharding",
        "desired_outcome": "Zero-downtime read replicas",
        "constraints_csv": "No data loss, max 10ms latency",
    })
    data_mission = json.loads(res_mission)
    assert data_mission["status"] == "created"
    mission_id = data_mission["mission"]["id"]

    # 3. Create Task
    res_task = manage_mission_hierarchy.invoke({
        "action": "create_task",
        "parent_id": mission_id,
        "name": "Deploy Citus Cluster",
        "priority": "high",
    })
    data_task = json.loads(res_task)
    assert data_task["status"] == "created"
    task_id = data_task["task"]["id"]

    # 4. Check Progress
    res_prog = manage_mission_hierarchy.invoke({
        "action": "get_progress",
        "node_id": goal_id,
    })
    data_prog = json.loads(res_prog)
    assert data_prog["progress_percent"] == 0

    # 5. Get Markdown Tree
    res_tree = manage_mission_hierarchy.invoke({
        "action": "get_tree",
        "node_id": goal_id,
        "level_type": "markdown",
    })
    assert "[GOAL]" in res_tree
    assert "Platform Scaling" in res_tree
    assert "[MISSION]" in res_tree


def test_schedule_work_queue_tool():
    # 1. Add Task A
    res_a = schedule_work_queue.invoke({
        "action": "add_task",
        "title": "Migrate Data",
        "priority_level": "high",
    })
    data_a = json.loads(res_a)
    assert data_a["status"] == "enqueued"
    task_a_id = data_a["task"]["id"]

    # 2. Add Task B dependent on A
    res_b = schedule_work_queue.invoke({
        "action": "add_task",
        "title": "Validate Replication",
        "priority_level": "critical",
        "dependencies_csv": task_a_id,
    })
    data_b = json.loads(res_b)
    assert data_b["status"] == "enqueued"
    task_b_id = data_b["task"]["id"]

    # 3. Dispatch next (should be A)
    res_disp = schedule_work_queue.invoke({"action": "dispatch_next"})
    data_disp = json.loads(res_disp)
    assert data_disp["status"] == "dispatched"
    assert data_disp["task"]["id"] == task_a_id

    # 4. Complete A
    res_comp = schedule_work_queue.invoke({
        "action": "complete_task",
        "task_id": task_a_id,
        "result_str": "Migrated 500k records",
    })
    data_comp = json.loads(res_comp)
    assert data_comp["status"] == "completed"

    # 5. Check Ready Tasks (now B should be ready)
    res_ready = schedule_work_queue.invoke({"action": "get_ready"})
    data_ready = json.loads(res_ready)
    assert len(data_ready) >= 1
    ready_ids = [t["id"] for t in data_ready]
    assert task_b_id in ready_ids


def test_trace_artifact_lineage_tool():
    # 1. Register Source
    res_src = trace_artifact_lineage.invoke({
        "action": "register_artifact",
        "name": "raw_logs.txt",
        "content": "ERROR 500 in auth handler",
        "confidence_level": "verified",
    })
    data_src = json.loads(res_src)
    src_id = data_src["artifact"]["artifact_id"]

    # 2. Register Target
    res_tgt = trace_artifact_lineage.invoke({
        "action": "register_artifact",
        "name": "incident_report.md",
        "content": "# Incident Root Cause: auth handler 500",
        "confidence_level": "provisional",
    })
    data_tgt = json.loads(res_tgt)
    tgt_id = data_tgt["artifact"]["artifact_id"]

    # 3. Record Derivation
    res_edge = trace_artifact_lineage.invoke({
        "action": "record_derivation",
        "source_id": src_id,
        "target_id": tgt_id,
        "derivation_action": "synthesized_incident_report",
    })
    data_edge = json.loads(res_edge)
    assert data_edge["status"] == "derivation_recorded"

    # 4. Trace Upstream
    res_up = trace_artifact_lineage.invoke({
        "action": "get_upstream",
        "artifact_id": tgt_id,
    })
    data_up = json.loads(res_up)
    assert data_up["total_ancestors"] == 1
    assert data_up["root_sources"][0]["artifact_id"] == src_id

    # 5. Verify Integrity
    res_audit = trace_artifact_lineage.invoke({
        "action": "verify_integrity",
        "artifact_id": tgt_id,
    })
    data_audit = json.loads(res_audit)
    assert data_audit["valid"] is True


def test_astra_spatial_and_goal_pursuit_tool():
    # 1. Ingest visual frame with object
    res_frame = astra_security_manage.invoke({
        "action": "ingest_frame",
        "object_label": "security_key",
        "screen_context": "physical desk with USB hardware key",
        "bbox_json": json.dumps({"x": 0.4, "y": 0.6, "width": 0.1, "height": 0.1}),
        "raw_text": "YubiKey 5C NFC detected",
    })
    data_frame = json.loads(res_frame)
    assert data_frame["status"] == "frame_ingested"

    # 2. Query spatial memory
    res_query = astra_security_manage.invoke({
        "action": "query_spatial_memory",
        "object_label": "security_key",
    })
    data_query = json.loads(res_query)
    assert data_query["match_count"] >= 1
    assert data_query["most_recent_location"]["object"]["bbox"]["x"] == 0.4

    # 3. Initialize Goal Pursuit
    res_init = astra_security_manage.invoke({
        "action": "init_goal_pursuit",
        "goal_statement": "Authenticate with hardware key and deploy",
        "milestones_csv": "Locate security_key, Sign payload",
    })
    data_init = json.loads(res_init)
    assert data_init["status"] == "goal_initialized"

    # 4. Pursue goal step
    res_step = astra_security_manage.invoke({
        "action": "pursue_goal_step",
        "object_label": "security_key",
        "telemetry_action": "locate_key",
        "agent_statement": "Found hardware security key at normalized coordinates",
    })
    data_step = json.loads(res_step)
    assert data_step["milestone_satisfied"] is True
    assert len(data_step["highlights"]) >= 1
