"""Cron blueprint catalog: natural-language schedule templates.

Each blueprint expands to a cron expression plus a prompt skeleton, so daily
reports, nightly backups, and weekly audits are one selection — not cron
syntax homework.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CronBlueprint:
    blueprint_id: str
    title: str
    description: str
    cron_expression: str
    prompt_template: str
    placeholders: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["placeholders"] = list(self.placeholders)
        return data

    def render(self, **values: str) -> dict[str, str]:
        prompt = self.prompt_template
        for key in self.placeholders:
            prompt = prompt.replace("{" + key + "}", str(values.get(key, f"<{key}>")))
        return {"name": self.title, "cron_expression": self.cron_expression, "command_or_prompt": prompt}


BLUEPRINTS: tuple[CronBlueprint, ...] = (
    CronBlueprint(
        blueprint_id="daily-report",
        title="Daily status report",
        description="Summarize yesterday's runs, blockers, and today's plan.",
        cron_expression="0 9 * * *",
        prompt_template="Write the daily status report for {project}. Cover: completed work, open blockers with owners, today's plan. Keep it under 300 words.",
        placeholders=("project",),
    ),
    CronBlueprint(
        blueprint_id="nightly-backup",
        title="Nightly workspace backup",
        description="Verify workspace snapshots and artifact manifests are intact.",
        cron_expression="0 2 * * *",
        prompt_template="Verify the {project} workspace: list artifacts changed in the last 24h, confirm manifests are consistent, and report anything missing or corrupt.",
        placeholders=("project",),
    ),
    CronBlueprint(
        blueprint_id="weekly-audit",
        title="Weekly security audit",
        description="Review tool approvals, skill installs, and failed runs.",
        cron_expression="0 10 * * 1",
        prompt_template="Audit {project} for the past week: policy denials, new skill installs, failed runs with causes, and any quarantined items needing review.",
        placeholders=("project",),
    ),
    CronBlueprint(
        blueprint_id="inbox-digest",
        title="Bot inbox digest",
        description="Summarize unread inter-bot messages for review.",
        cron_expression="*/30 * * * *",
        prompt_template="Summarize unread bot inbox messages for {bot}: sender, one-line gist, and whether each needs a reply. Flag anything older than 24h.",
        placeholders=("bot",),
    ),
    CronBlueprint(
        blueprint_id="skill-curator",
        title="Weekly skill curation",
        description="Run the skill curator maintenance pass.",
        cron_expression="0 3 * * 0",
        prompt_template="Run the skill curator maintenance pass: report staled/archived skills and any consolidation proposals awaiting review.",
        placeholders=(),
    ),
)


def list_blueprints() -> list[dict[str, Any]]:
    return [b.to_dict() for b in BLUEPRINTS]


def get_blueprint(blueprint_id: str) -> CronBlueprint | None:
    for blueprint in BLUEPRINTS:
        if blueprint.blueprint_id == blueprint_id:
            return blueprint
    return None
