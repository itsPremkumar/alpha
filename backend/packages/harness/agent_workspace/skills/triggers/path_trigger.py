"""PathTrigger: Fires when touched or targeted files match glob patterns."""

from __future__ import annotations

import fnmatch
from pathlib import PurePath

from agent_workspace.skills.triggers.models import BaseTrigger, TriggerContext


class PathTrigger(BaseTrigger):
    """Triggers when any file path in context matches any glob pattern."""

    trigger_type: str = "path"

    def __init__(self, patterns: str | list[str]):
        if isinstance(patterns, str):
            self.patterns = [patterns]
        else:
            self.patterns = list(patterns)

    def should_trigger(self, context: TriggerContext) -> bool:
        if not context.file_paths:
            return False

        for path_str in context.file_paths:
            norm_path = path_str.replace("\\", "/")
            # Test each pattern
            for pat in self.patterns:
                norm_pat = pat.replace("\\", "/")
                # Check fnmatch directly or match against PurePath
                if fnmatch.fnmatch(norm_path, norm_pat) or fnmatch.fnmatch(norm_path.split("/")[-1], norm_pat):
                    return True
                # If pattern starts with **/, also test the suffix pattern (e.g. *.graphql matching schema.graphql)
                if norm_pat.startswith("**/"):
                    suffix_pat = norm_pat[3:]
                    if fnmatch.fnmatch(norm_path, suffix_pat) or fnmatch.fnmatch(norm_path.split("/")[-1], suffix_pat):
                        return True
                try:
                    if PurePath(norm_path).match(norm_pat):
                        return True
                except Exception:
                    pass

        return False
