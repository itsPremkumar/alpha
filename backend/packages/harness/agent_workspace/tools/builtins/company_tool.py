"""Built-in LangChain tool for Autonomous AI Company & Perpetual Organization OS."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.company.organization import get_autonomous_company_engine


@tool("company_os", parse_docstring=True)
def company_tool(
    action: str,
    prompt: str = "",
    archetype: str = "",
    org_id: str = "",
    directive: str = "",
    responsibility_id: str = "",
    kpi_id: str = "",
    new_metric_value: float = 0.0,
    signals_json: str = "[]",
    title: str = "",
    spec: str = "",
    run_id: str = "",
    stage_content: str = "",
    channel_id: str = "",
    sender_bot: str = "",
    message: str = "",
    bot_name: str = "",
    pulse_status: str = "present",
    member_bots_json: str = "[]",
    task_id: str = "",
    task_status: str = "",
) -> str:
    """Manage autonomous AI companies, perpetual collectives, work discovery, and self-improvement.

    Args:
        action: Management action ('bootstrap', 'archetypes', 'status', 'discover_work', 'evaluate_kpis',
            'replan_strategy', 'executive_digest', 'transfer_responsibility', 'retrospective',
            'evolution_journal', 'swarm_bots', 'hermes_bots', 'production_submit', 'production_advance',
            'kanban_sync', 'kanban_tasks', 'kanban_update', 'kanban_log', 'kanban_events', 'kanban_check_in',
            'group_channels', 'group_post', 'group_create', 'group_history',
            'attendance_pulse', 'attendance_check', 'roll_call', 'pause', 'resume').
        prompt: Vision or prompt describing the company or collective to bootstrap.
        archetype: 'company', 'open_source', 'security_soc', 'research_lab', 'custom'.
        org_id: Organization identifier.
        directive: Strategic pivot directive from the human owner.
        responsibility_id: Target responsibility identifier for failover handoffs.
        kpi_id: Target KPI identifier for updating metrics.
        new_metric_value: Metric value when updating a KPI.
        signals_json: JSON string representing incoming telemetry or issue signals.
        title: Feature title for production line or channel name.
        spec: Feature requirements specification text.
        run_id: Production line run identifier.
        stage_content: Content artifact for advancing production stage.
        channel_id: Group channel identifier (e.g. 'all-hands', 'dept-engineering').
        sender_bot: Sending agent identity.
        message: Content of message to post with optional @mentions.
        bot_name: Target bot name for attendance pulse or inspection.
        pulse_status: Heartbeat status ('present', 'busy', 'idle').
        member_bots_json: JSON array of member bot names for creating a new sub-group.
        task_id: Target Kanban task identifier.
        task_status: New status for Kanban task ('ready', 'in_progress', 'review', 'done').
    """
    engine = get_autonomous_company_engine()

    try:
        if action == "archetypes":
            return json.dumps(engine.list_archetypes(), indent=2)

        elif action == "bootstrap":
            if not prompt:
                return "Error: 'prompt' is required to bootstrap an autonomous organization."
            state = engine.bootstrap_company(prompt=prompt, archetype=archetype or None)
            return json.dumps(
                {
                    "status": "company_bootstrapped",
                    "org_id": state.org_id,
                    "name": state.name,
                    "archetype": state.archetype.value,
                    "state": state.state.value,
                    "departments_count": len(state.departments),
                    "responsibilities_count": len(state.responsibilities),
                    "projects_count": len(state.projects),
                    "active_bots_count": state.active_bots_count,
                    "sleeping_bots_count": state.sleeping_bots_count,
                },
                indent=2,
            )

        elif action == "status":
            if not org_id:
                # Return first registered company if org_id omitted
                companies = engine.list_companies()
                if not companies:
                    return "Error: No organizations found. Bootstrap an organization first."
                state = companies[0]
            else:
                state = engine.get_company(org_id)
                if not state:
                    return f"Error: Organization '{org_id}' not found."

            return json.dumps(state.model_dump(), indent=2)

        elif action == "discover_work":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            disc = engine.get_discovery_engine(target_org)
            try:
                signals = json.loads(signals_json) if signals_json else []
            except Exception:
                signals = []
            if not signals:
                signals = [
                    {"title": "Upgrade outdated cryptographic dependencies", "category": "security", "impact": 0.8, "urgency": 0.7},
                    {"title": "Optimize slow database join on user invoices", "category": "optimization", "impact": 0.7, "urgency": 0.6},
                ]
            items, should_sleep = disc.discover_from_sources(signals)
            return json.dumps(
                {
                    "discovered_items": [i.model_dump() for i in items],
                    "workers_should_sleep": should_sleep,
                },
                indent=2,
            )

        elif action == "evaluate_kpis":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            kpi_eng = engine.get_kpi_engine(target_org)
            if kpi_id:
                kpi, triggered = kpi_eng.update_metric(kpi_id, new_metric_value)
                return json.dumps(
                    {
                        "updated_kpi": kpi.model_dump(),
                        "triggered_corrective_task": triggered,
                    },
                    indent=2,
                )
            else:
                return json.dumps([k.model_dump() for k in kpi_eng.list_kpis()], indent=2)

        elif action == "replan_strategy":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org or not directive:
                return "Error: 'org_id' and 'directive' are required for replan_strategy."
            report = engine.replan_strategy(target_org, directive)
            return json.dumps(report.model_dump(), indent=2)

        elif action == "executive_digest":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            digest = engine.get_executive_digest(target_org)
            return json.dumps(digest.model_dump(), indent=2)

        elif action == "transfer_responsibility":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org or not responsibility_id:
                return "Error: 'org_id' and 'responsibility_id' are required for transfer."
            resp_eng = engine.get_responsibility_engine(target_org)
            binding = resp_eng.trigger_failover(responsibility_id, reason="Manual agent handoff")
            return json.dumps(binding.model_dump(), indent=2)

        elif action in ("retrospective", "self_improve"):
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            record = engine.run_retrospective(target_org)
            return json.dumps(record.model_dump(), indent=2)

        elif action == "evolution_journal":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            state = engine.get_company(target_org)
            if not state:
                return f"Error: Organization '{target_org}' not found."
            return json.dumps([e.model_dump() for e in state.evolution_journal], indent=2)

        elif action in ("swarm_bots", "specialist_bots", "hermes_bots"):
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                # If no company, list raw local bots
                bridge = engine.get_hermes_bridge()
                local_bots = bridge.discover_local_bots()
                return json.dumps(
                    {
                        "hermes_installed": bridge.is_hermes_installed,
                        "discovered_bots_count": len(local_bots),
                        "bot_names": local_bots,
                    },
                    indent=2,
                )
            sync_res = engine.sync_hermes_bots(target_org)
            return json.dumps(sync_res, indent=2)

        elif action == "production_submit":
            feature_title = title or "New Architecture Feature"
            feature_spec = spec or prompt or "Requirements specification for autonomous module"
            prod_line = engine.get_production_line()
            run = prod_line.submit_feature(feature_title, feature_spec)
            return json.dumps(run.model_dump(), indent=2)

        elif action == "production_advance":
            if not run_id:
                return "Error: 'run_id' is required for production_advance."
            prod_line = engine.get_production_line()
            run, artifact = prod_line.advance_stage(run_id, content=stage_content)
            return json.dumps(
                {
                    "run": run.model_dump(),
                    "created_artifact": artifact.model_dump(),
                },
                indent=2,
            )

        elif action == "kanban_sync":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            sync_res = engine.sync_company_to_hermes_kanban(target_org)
            return json.dumps(sync_res, indent=2)

        elif action == "kanban_tasks":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            tasks = engine.list_kanban_tasks(
                org_id=target_org,
                status=task_status or None,
                assignee=bot_name or None,
            )
            return json.dumps(tasks, indent=2)

        elif action == "kanban_update":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org or not task_id:
                return "Error: 'org_id' and 'task_id' are required to update a Kanban task."
            res = engine.update_kanban_task(
                org_id=target_org,
                task_id=task_id,
                new_status=task_status or "in_progress",
                bot_name=bot_name or sender_bot or "bot-worker",
                log_message=message or spec,
            )
            return json.dumps(res, indent=2)

        elif action == "kanban_log":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org or not task_id:
                return "Error: 'org_id' and 'task_id' are required to add a Kanban log."
            event = engine.add_kanban_log(
                org_id=target_org,
                task_id=task_id,
                bot_name=bot_name or sender_bot or "bot-worker",
                message=message or "Task progress update",
            )
            return json.dumps(event, indent=2)

        elif action == "kanban_events":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            events = engine.list_kanban_logs(
                org_id=target_org,
                task_id=task_id or None,
            )
            return json.dumps(events, indent=2)

        elif action == "kanban_check_in":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            target_bot = bot_name or sender_bot or "bot-worker"
            res = engine.agent_kanban_check_in(
                org_id=target_org,
                bot_name=target_bot,
                current_task_id=task_id or None,
                progress_notes=message or spec,
                new_status=task_status or None,
            )
            return json.dumps(res, indent=2)

        elif action == "group_channels":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            chat = engine.get_chat_engine(target_org)
            return json.dumps([c.model_dump() for c in chat.list_channels()], indent=2)

        elif action == "group_post":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            target_chan = channel_id or "all-hands"
            sender = sender_bot or "agent-worker"
            msg = engine.post_group_message(target_org, target_chan, sender, message or "Status update")
            return json.dumps(msg.model_dump(), indent=2)

        elif action == "group_create":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            if not channel_id:
                return "Error: 'channel_id' is required to create a group channel."
            try:
                member_list = json.loads(member_bots_json) if member_bots_json else []
            except Exception:
                member_list = []
            chan_name = title or f"#{channel_id}"
            chan = engine.create_subgroup(target_org, channel_id, chan_name, member_list, description=spec)
            return json.dumps(chan.model_dump(), indent=2)

        elif action == "group_history":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            target_chan = channel_id or "all-hands"
            chat = engine.get_chat_engine(target_org)
            history = chat.get_channel_history(target_chan)
            return json.dumps([m.model_dump() for m in history], indent=2)

        elif action == "attendance_pulse":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            target_bot = bot_name or sender_bot or "bot-worker"
            hb = engine.record_bot_pulse(target_org, target_bot, status=pulse_status or "present")
            return json.dumps(hb.model_dump(), indent=2)

        elif action == "attendance_check":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            res = engine.check_attendance_and_heal(target_org)
            return json.dumps(res, indent=2)

        elif action == "roll_call":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            if not target_org:
                return "Error: Organization not found."
            digest = engine.get_roll_call_digest(target_org)
            return digest


        elif action == "pause":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            res = engine.pause_company(target_org)
            return json.dumps({"status": "company_paused", "org_id": target_org, "state": res.state.value}, indent=2)

        elif action == "resume":
            target_org = org_id or (engine.list_companies()[0].org_id if engine.list_companies() else "")
            res = engine.resume_company(target_org)
            return json.dumps({"status": "company_resumed", "org_id": target_org, "state": res.state.value}, indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing company_os tool: {exc}"
