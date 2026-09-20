"""Organizational Hierarchy, Departments, and Dynamic Org Generation Engine (Master Inventory #17-#21, #142).

Provides organization chart tree building, department aggregation, and goal-driven dynamic team generation.
"""

from __future__ import annotations

import logging
from typing import Any

from alpha.bots.profile import BotProfile
from alpha.bots.registry import BotRegistry, get_bot_registry
from alpha.bots.templates import BOT_TEMPLATES, DEPARTMENTS

logger = logging.getLogger(__name__)


def get_organization_chart(registry: BotRegistry | None = None) -> dict[str, Any]:
    """Build a complete organizational hierarchy tree and departmental breakdown."""
    reg = registry or get_bot_registry()
    # Retired bots must not appear in the org chart.
    all_bots = reg.list_bots(include_archived=False)
    bots_by_name = {b.name.lower(): b for b in all_bots}

    # Department aggregation
    departments_map: dict[str, list[dict[str, Any]]] = {d: [] for d in DEPARTMENTS}
    for bot in all_bots:
        dept = (bot.department or "engineering").lower()
        if dept not in departments_map:
            departments_map[dept] = []
        departments_map[dept].append(
            {
                "name": bot.name,
                "display_name": bot.display_name,
                "role": bot.role,
                "avatar": bot.avatar,
                "status": bot.status,
                "reports_to": bot.reports_to,
                "responsibilities": bot.responsibilities,
            }
        )

    # Build hierarchy tree
    # Roots: bots with no reports_to or whose manager does not exist in roster
    children_map: dict[str, list[str]] = {}
    for bot in all_bots:
        manager = (bot.reports_to or "").lower().strip()
        if manager and manager in bots_by_name:
            children_map.setdefault(manager, []).append(bot.name)

    def _build_node(bot: BotProfile) -> dict[str, Any]:
        subs = children_map.get(bot.name.lower(), [])
        return {
            "name": bot.name,
            "display_name": bot.display_name,
            "role": bot.role,
            "avatar": bot.avatar,
            "department": bot.department,
            "status": bot.status,
            "reputation_score": bot.reputation_score,
            "subordinates": [_build_node(bots_by_name[s]) for s in subs if s in bots_by_name],
        }

    # Find roots (CEO or top-level bots)
    roots = []
    for bot in all_bots:
        manager = (bot.reports_to or "").lower().strip()
        if not manager or manager not in bots_by_name:
            roots.append(_build_node(bot))

    # Build flat graph representation (nodes & edges) for UI graph visualizers
    nodes = [
        {
            "id": b.name,
            "label": b.display_name,
            "role": b.role,
            "department": b.department,
            "avatar": b.avatar,
            "status": b.status,
        }
        for b in all_bots
    ]
    edges = [{"from": b.reports_to.lower(), "to": b.name.lower(), "relation": "reports_to"} for b in all_bots if b.reports_to and b.reports_to.lower() in bots_by_name]

    return {
        "departments": departments_map,
        "tree": roots,
        "graph": {"nodes": nodes, "edges": edges},
        "total_bots": len(all_bots),
    }


def generate_organization_for_goal(
    goal_description: str,
    *,
    registry: BotRegistry | None = None,
    auto_provision: bool = False,
) -> dict[str, Any]:
    """Dynamically formulate the optimal team composition and hierarchy from a goal (Inventory #21).

    Analyzes goal keywords to recommend the ideal department mix, specialist roles,
    and reporting lines, optionally auto-provisioning missing bots from templates.
    """
    g = goal_description.lower()

    recommended_slugs: list[str] = []

    # Detection heuristics
    is_company = any(k in g for k in ("company", "startup", "business", "enterprise", "organization", "launch product"))
    is_coding = any(k in g for k in ("code", "develop", "software", "api", "backend", "frontend", "fullstack", "bug", "refactor", "app", "system"))
    is_research = any(k in g for k in ("research", "analyze", "investigate", "market", "paper", "data", "report", "benchmark"))
    is_ops = any(k in g for k in ("ops", "sre", "infra", "deploy", "docker", "ci/cd", "security", "vulnerability", "kubernetes", "incident"))
    is_mobile = any(k in g for k in ("mobile", "ios", "android", "app store", "react native", "flutter"))
    is_support = any(k in g for k in ("support", "helpdesk", "help desk", "ticket", "customer", "onboarding", "refund"))
    is_data = any(k in g for k in ("dashboard", "analytics", "metrics", "etl", "warehouse", "spreadsheet", "csv", "kpi"))
    is_writing = any(k in g for k in ("document", "blog", "content", "copy", "newsletter", "copywrite", "write-up", "writeup"))
    is_design = any(k in g for k in ("design", "mockup", "wireframe", "ux", "user flow", "prototype", "redesign"))
    is_legal = any(k in g for k in ("contract", "legal", "compliance", "policy review", "terms of service", "nda"))
    is_finance = any(k in g for k in ("invoice", "budget", "expense", "finance", "spend", "payroll", "accounting"))
    is_hiring = any(k in g for k in ("hire", "hiring", "recruit", "interview", "candidate", "onboard employee"))
    is_marketing = any(k in g for k in ("seo", "campaign", "brand", "launch marketing", "social media", "advertis"))
    is_management = any(k in g for k in ("manage the team", "team lead", "engineering management", "1:1", "performance review", "retrospective", "team health"))
    is_solution = any(k in g for k in ("proposal", "rfp", "vendor selection", "client architect", "integration project", "solution design"))
    is_prompt = any(k in g for k in ("prompt engineer", "prompt design", "eval harness", "llm behavior", "system prompt", "prompt evaluation"))
    is_mcp = any(k in g for k in ("mcp server", "mcp integration", "connect external tool", "tool integration", "model context protocol"))
    is_dataeng = any(k in g for k in ("pipeline", "elt", "airflow", "orchestrat", "data quality", "data warehouse build"))

    if is_company:
        # Full autonomous organization
        recommended_slugs = [
            "ceo",
            "cto",
            "architect",
            "coder",
            "frontend",
            "qa",
            "product-manager",
            "researcher",
            "sre",
            "marketing",
            "support",
        ]
    elif is_coding and is_research:
        recommended_slugs = ["architect", "coder", "researcher", "reviewer", "tester", "product-manager"]
    elif is_mobile:
        recommended_slugs = ["mobile-dev", "architect", "tester", "reviewer"]
    elif is_support:
        recommended_slugs = ["support", "customer-success", "product-manager", "technical-writer"]
    elif is_dataeng:
        recommended_slugs = ["data-engineer", "data-analyst", "architect", "reviewer"]
    elif is_data:
        recommended_slugs = ["data-analyst", "data-scientist", "architect", "reviewer"]
    elif is_writing:
        recommended_slugs = ["technical-writer", "content-writer", "marketing", "reviewer"]
    elif is_design:
        recommended_slugs = ["designer", "frontend", "architect", "reviewer"]
    elif is_legal:
        recommended_slugs = ["legal-reviewer", "product-manager"]
    elif is_finance:
        recommended_slugs = ["finance-analyst", "data-analyst", "product-manager"]
    elif is_hiring:
        recommended_slugs = ["hr-recruiter", "product-manager", "technical-writer"]
    elif is_marketing:
        recommended_slugs = ["marketing", "content-writer", "seo-specialist", "designer"]
    elif is_management:
        recommended_slugs = ["engineering-manager", "project-manager", "architect"]
    elif is_solution:
        recommended_slugs = ["solution-architect", "architect", "product-manager"]
    elif is_prompt:
        recommended_slugs = ["prompt-engineer", "architect", "reviewer"]
    elif is_mcp:
        recommended_slugs = ["mcp-specialist", "architect", "devops"]
    elif is_coding:
        recommended_slugs = ["architect", "coder", "reviewer", "tester"]
        if "ui" in g or "frontend" in g or "web" in g:
            recommended_slugs.append("frontend")
        if "data" in g or "database" in g or "sql" in g:
            recommended_slugs.append("data-analyst")
    elif is_research:
        recommended_slugs = ["researcher", "data-analyst", "product-manager", "technical-writer"]
    elif is_ops:
        recommended_slugs = ["cto", "sre", "devops", "security"]
    else:
        # Universal agile pod
        recommended_slugs = ["architect", "coder", "reviewer", "researcher", "tester"]

    # Deduplicate while preserving order
    seen = set()
    slugs = []
    for s in recommended_slugs:
        if s not in seen and s in BOT_TEMPLATES:
            seen.add(s)
            slugs.append(s)

    # Format recommendations with template metadata
    recommendations = []
    for slug in slugs:
        spec = BOT_TEMPLATES[slug]
        recommendations.append(
            {
                "slug": slug,
                "display_name": spec["display"],
                "role": spec["role"],
                "department": spec.get("department", "engineering"),
                "reports_to": spec.get("reports_to"),
                "responsibilities": spec.get("responsibilities", []),
                "capabilities": spec.get("capabilities", []),
                "avatar": spec.get("avatar", ""),
            }
        )

    provisioned = []
    if auto_provision:
        reg = registry or get_bot_registry()
        for rec in recommendations:
            bot = reg.get_or_create(
                rec["slug"],
                template=rec["slug"],
                department=rec["department"],
                reports_to=rec["reports_to"],
                responsibilities=rec["responsibilities"],
                capabilities=rec["capabilities"],
            )
            provisioned.append(bot.name)

    return {
        "goal": goal_description,
        "recommended_team_size": len(recommendations),
        "recommended_roles": recommendations,
        "auto_provisioned": provisioned,
    }
