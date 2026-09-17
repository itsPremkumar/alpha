"""ShellASTParser: Parses complex shell command lines into structured AST hierarchies."""

from __future__ import annotations

import re
import shlex

from agent_workspace.security.shell_ast.ast_nodes import (
    ASTNode,
    CommandNode,
    CompoundNode,
    PipelineNode,
    RedirectionNode,
    SubshellNode,
)


class ShellASTParser:
    """Parses shell command strings into abstract syntax trees."""

    def parse(self, command_line: str) -> ASTNode:
        """Parse a full shell command string into an ASTNode."""
        cleaned = command_line.strip()
        if not cleaned:
            return CommandNode(command="")

        # 1. Check for compound operators at top level (;, &&, ||) outside of quotes and subshells
        compound_split = self._split_top_level_operator(cleaned, [";", "&&", "||"])
        if compound_split:
            left_str, op, right_str = compound_split
            left_node = self.parse(left_str)
            right_node = self.parse(right_str)
            return CompoundNode(operator=op, left=left_node, right=right_node)

        # 2. Check for pipeline operators (|) at top level
        pipe_parts = self._split_top_level_pipes(cleaned)
        if len(pipe_parts) > 1:
            stages = [self._parse_simple_command(part.strip()) for part in pipe_parts if part.strip()]
            return PipelineNode(stages=stages)

        # 3. Simple command (with potential subshells or redirections)
        return self._parse_simple_command(cleaned)

    def _split_top_level_operator(
        self, text: str, operators: list[str]
    ) -> tuple[str, str, str] | None:
        """Split string by operators (&&, ||, ;) if they occur at the top level (outside quotes and subshells)."""
        in_single = False
        in_double = False
        subshell_depth = 0
        i = 0
        n = len(text)

        while i < n:
            c = text[i]

            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if c == "$" and i + 1 < n and text[i + 1] == "(":
                    subshell_depth += 1
                    i += 1
                elif c == ")" and subshell_depth > 0:
                    subshell_depth -= 1
                elif subshell_depth == 0:
                    for op in operators:
                        op_len = len(op)
                        if text[i : i + op_len] == op:
                            left = text[:i].strip()
                            right = text[i + op_len :].strip()
                            if left and right:
                                return left, op, right
            i += 1
        return None

    def _split_top_level_pipes(self, text: str) -> list[str]:
        """Split text by '|' (outside quotes, subshells, and not part of '||')."""
        stages: list[str] = []
        last_idx = 0
        in_single = False
        in_double = False
        subshell_depth = 0
        i = 0
        n = len(text)

        while i < n:
            c = text[i]
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif not in_single and not in_double:
                if c == "$" and i + 1 < n and text[i + 1] == "(":
                    subshell_depth += 1
                    i += 1
                elif c == ")" and subshell_depth > 0:
                    subshell_depth -= 1
                elif subshell_depth == 0 and c == "|":
                    # ensure not '||'
                    is_double_or = (i + 1 < n and text[i + 1] == "|") or (i > 0 and text[i - 1] == "|")
                    if not is_double_or:
                        stages.append(text[last_idx:i].strip())
                        last_idx = i + 1
            i += 1

        stages.append(text[last_idx:].strip())
        return stages

    def _parse_simple_command(self, cmd_str: str) -> CommandNode:
        """Parse a single command token stream, extracting env vars, redirects, and subshells."""
        cmd_str = cmd_str.strip()
        tokens = self._tokenize(cmd_str)

        command_name = ""
        args: list[str] = []
        env_vars = {}
        redirections: list[RedirectionNode] = []
        subshells: list[ASTNode] = []

        # Find any embedded subshells $(...) or `...`
        for sub_content in self._extract_subshell_contents(cmd_str):
            subshells.append(SubshellNode(body=self.parse(sub_content)))

        i = 0
        while i < len(tokens):
            tok = tokens[i]

            # Redirection check
            if tok in {">", ">>", "<", "2>&1", "1>&2", "2>", "&>"}:
                target = tokens[i + 1] if i + 1 < len(tokens) else ""
                redirections.append(RedirectionNode(operator=tok, target=target))
                i += 2
                continue

            # Environment variable prefix before command: FOO=bar
            if not command_name and "=" in tok and not tok.startswith("-"):
                k, v = tok.split("=", 1)
                env_vars[k] = v
                i += 1
                continue

            if not command_name:
                command_name = tok
            else:
                args.append(tok)
            i += 1

        return CommandNode(
            command=command_name,
            args=args,
            env_vars=env_vars,
            redirections=redirections,
            subshells=subshells,
        )

    def _tokenize(self, s: str) -> list[str]:
        """Safe lexical tokenization."""
        try:
            return shlex.split(s, posix=True)
        except ValueError:
            # Fallback simple whitespace split for unclosed quotes
            return s.split()

    def _extract_subshell_contents(self, text: str) -> list[str]:
        """Extract expressions inside $(...) or `...`."""
        subshells = []
        # Match $(...)
        pattern_dollar = re.compile(r"\$\((.*?)\)")
        for m in pattern_dollar.finditer(text):
            subshells.append(m.group(1))

        # Match backticks `...`
        pattern_backtick = re.compile(r"`(.*?)`")
        for m in pattern_backtick.finditer(text):
            subshells.append(m.group(1))

        return subshells
