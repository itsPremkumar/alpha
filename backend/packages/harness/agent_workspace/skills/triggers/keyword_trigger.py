"""KeywordTrigger: Fires when user goal or prompt contains specific keywords or regex patterns."""

from __future__ import annotations

import re

from agent_workspace.skills.triggers.models import BaseTrigger, TriggerContext


class KeywordTrigger(BaseTrigger):
    """Triggers when query text matches configured keywords or regex patterns."""

    trigger_type: str = "keyword"

    def __init__(
        self,
        keywords: list[str] | None = None,
        regex_patterns: list[str] | None = None,
        case_sensitive: bool = False,
    ):
        self.keywords = keywords or []
        self.case_sensitive = case_sensitive
        self.regex_patterns = []
        if regex_patterns:
            flags = 0 if case_sensitive else re.IGNORECASE
            for pat in regex_patterns:
                self.regex_patterns.append(re.compile(pat, flags))

    def should_trigger(self, context: TriggerContext) -> bool:
        text = context.query
        if not text:
            return False

        eval_text = text if self.case_sensitive else text.lower()

        # 1. Keyword check
        for kw in self.keywords:
            eval_kw = kw if self.case_sensitive else kw.lower()
            if eval_kw in eval_text:
                return True

        # 2. Regex check
        for pattern in self.regex_patterns:
            if pattern.search(text):
                return True

        return False
