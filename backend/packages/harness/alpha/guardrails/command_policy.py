"""Command-content policy for the ``bash`` tool.

``AllowlistProvider`` (:mod:`alpha.guardrails.builtin`) gates on **tool name**.
It answers "may the agent call ``bash``?" and nothing about *what* it is about to
run. Once host bash is enabled (``is_host_bash_allowed``) and
``validate_local_bash_command_paths`` accepts the paths, the command string
itself is unreviewed: ``git push --force``, ``curl … | bash``, and ``rm -rf /``
all reach the host. This provider closes that gap.

It implements the existing :class:`~alpha.guardrails.provider.GuardrailProvider`
protocol, so it is configured through the normal ``guardrails.provider.use``
class path and rides the same :class:`~alpha.guardrails.middleware.GuardrailMiddleware`
that already records an authorization outcome and a run-journal event. It does
not add a parallel authorization system.

**A gate is not a sandbox.** This is a guardrail and a UX layer. It bounds what
the agent does by accident, and gives the model an actionable message. It is not
a containment boundary — an allowed command can still do anything its user can.
Containment is the sandbox provider's job (``LocalSandbox`` versus AIO/E2B/BoxLite).

**Four verdicts, not two.** ``ALLOW`` / ``ASK`` / ``DENY`` / ``DEFER``. ``DEFER``
means "unclassified" and is the safe default: it is neither a silent allow nor a
brick wall. Because :class:`GuardrailDecision` is a binary ``allow`` flag, ``ASK``
and ``DEFER`` currently both surface as a block — but with **distinct reason
codes** and the real verdict preserved in ``decision.metadata['verdict']``, so an
approval UI can consume it later without a breaking change. That limitation is
deliberate and documented rather than papered over.

**Structural role classification.** The gate does not ask "is ``head`` safe?". It
asks what role the command plays in the expression it appears in:

    gh pr list | head -20   ->  gh=standalone, head=pipe_filter   => allow
    head /etc/passwd        ->  head=standalone                   => defer

One rule (``executables=["head"]``, ``roles=["pipe_filter","substitution"]``)
covers both correctly. See ``alpha.guardrails.command_policy`` research notes in
``AGENT_TOOLING_IMPLEMENTATION_PLAN.md``.

**No glob selectors.** Rules match on normalized executables plus token
predicates (``has_any`` / ``has_all`` / ``unless``) and roles. This deliberately
avoids prefix-glob rule syntax, where ``git diff*`` silently also matches
``git diff-index`` and the space in ``git diff *`` is load-bearing. Token
predicates have no such ambiguity.

**Parse failures never allow.** :func:`decompose` returns a ``problems`` list; any
problem forces ``DEFER``. The tokenizer is stdlib-only and conservative: it
returns ``ALLOW`` only for a command it fully decomposed. Unsupported or
adversarial input degrades to "ask a human", never to "run it".
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, override

from pydantic import BaseModel, Field

from alpha.guardrails.provider import GuardrailDecision, GuardrailReason, GuardrailRequest

logger = logging.getLogger(__name__)

Verdict = Literal["allow", "ask", "deny", "defer"]

#: A fragment's structural role in the expression it appeared in. Ordered
#: most-permissive-first is *not* intended; precedence is deny > ask > allow.
ROLE_STANDALONE = "standalone"
ROLE_PIPE_FILTER = "pipe_filter"
ROLE_SUBSTITUTION = "substitution"

_ROLES = (ROLE_STANDALONE, ROLE_PIPE_FILTER, ROLE_SUBSTITUTION)

#: Executables that exist to run *other* code. An allowlist entry for one of these
#: is meaningless, so they are forced to DEFER regardless of configuration. This
#: is the single most important list in the module: `bash -c "…"`, `xargs sh -c`,
#: and `env FOO=1 cmd` all bypass any gate that only inspects the outer argv.
INTERPRETERS = frozenset(
    {
        "ash",
        "bash",
        "busybox",
        "cmd",
        "cmd.exe",
        "command",
        "csh",
        "dash",
        "doas",
        "env",
        "eval",
        "exec",
        "fish",
        "ksh",
        "node",
        "perl",
        "php",
        "powershell",
        "powershell.exe",
        "pwsh",
        "python",
        "python3",
        "ruby",
        "sh",
        "source",
        "sudo",
        "tcsh",
        "xargs",
        "zsh",
    }
)

#: Flags that turn an otherwise-inert interpreter into an arbitrary-code runner.
#: ``python script.py`` is auditable; ``python -c "…"`` is not.
_INLINE_EXEC_FLAGS = frozenset({"-c", "-e", "-ec", "-EncodedCommand", "-Command", "-CommandWithArgs"})

#: Safe read-only POSIX/coreutils surface. Anything that writes or executes
#: project code is deliberately absent.
_READ_ONLY_EXECUTABLES = frozenset(
    {
        "awk",
        "basename",
        "cat",
        "cksum",
        "column",
        "comm",
        "date",
        "df",
        "diff",
        "dirname",
        "du",
        "echo",
        "env",
        "expr",
        "false",
        "file",
        "find",
        "fold",
        "grep",
        "hostname",
        "id",
        "jq",
        "less",
        "ls",
        "md5sum",
        "more",
        "nl",
        "od",
        "printenv",
        "printf",
        "pwd",
        "readlink",
        "realpath",
        "rg",
        "sed",
        "seq",
        "sha1sum",
        "sha256sum",
        "stat",
        "test",
        "true",
        "type",
        "uname",
        "uptime",
        "which",
        "whoami",
        "yq",
    }
)

#: Output-shaping filters. They are only allowed as a pipeline stage or inside a
#: substitution, never as the command itself. This is the structural-role rule
#: doing real work: `gh pr list | head -20` is fine, while `head /etc/passwd` is
#: not auto-approved. Nothing is lost by the restriction — reading a file
#: directly is `cat`/`grep`, which are allowlisted above.
_PIPELINE_ONLY_EXECUTABLES = ("cut", "head", "sort", "tail", "tee", "tr", "uniq", "wc")

#: `git` subcommands that cannot mutate the repository, the index, or a ref.
_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "diff-tree",
        "grep",
        "log",
        "ls-files",
        "ls-remote",
        "name-rev",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "show-ref",
        "status",
        "var",
        "version",
        "whatchanged",
    }
)

#: `git` subcommands that mutate the working tree, index, or a ref. Ask, not deny.
_GIT_WRITE_SUBCOMMANDS = frozenset(
    {
        "add",
        "am",
        "apply",
        "branch",
        "checkout",
        "cherry-pick",
        "clean",
        "commit",
        "fetch",
        "filter-branch",
        "gc",
        "merge",
        "mv",
        "notes",
        "pull",
        "push",
        "rebase",
        "repack",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "submodule",
        "switch",
        "tag",
        "worktree",
    }
)

#: Executables that execute project code. `read-only` defers these to a human
#: because running a build or a test can run arbitrary repository code.
_PROJECT_CODE_EXECUTABLES = frozenset(
    {
        "bun",
        "cargo",
        "deno",
        "docker",
        "dotnet",
        "go",
        "gradle",
        "jest",
        "just",
        "kubectl",
        "make",
        "mvn",
        "npm",
        "npx",
        "pip",
        "pip3",
        "pnpm",
        "poetry",
        "pytest",
        "rake",
        "tox",
        "uv",
        "venv",
        "virtualenv",
        "yarn",
    }
)

#: Hard-deny set. Kept deliberately tiny. A hard deny cannot be overridden by an
#: operator or a project overlay, so anything a real workflow might legitimately
#: need belongs in the ask tier instead. Reaching for this list too eagerly is
#: how a guardrail gets switched off entirely.
_HARD_DENY = (
    ("mkfs", "creates a filesystem; not a recoverable operation"),
    ("fdisk", "modifies partition tables"),
    ("shutdown", "halts the host"),
    ("reboot", "halts the host"),
    ("halt", "halts the host"),
    ("poweroff", "halts the host"),
)


@dataclass(frozen=True)
class CommandFragment:
    """One simple command, its structural role, and any redirections it performs."""

    argv: tuple[str, ...]
    role: str
    redirections: tuple[tuple[str, str], ...] = ()

    @property
    def executable(self) -> str:
        """Basename of argv[0], lowercased, with a Windows ``.exe`` suffix removed.

        Normalizing here is what makes ``/bin/rm``, ``rm``, and ``rm.exe`` one
        rule rather than three.
        """
        if not self.argv:
            return ""
        head = self.argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        return head[:-4] if head.endswith(".exe") else head

    @property
    def has_output_redirection(self) -> bool:
        return any(direction.startswith(">") for direction, _ in self.redirections)


@dataclass
class Decomposition:
    """Result of splitting a command string into fragments."""

    fragments: list[CommandFragment] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems


# --------------------------------------------------------------------------- #
# Decomposition
# --------------------------------------------------------------------------- #

_MAX_SUBSTITUTION_DEPTH = 6


def _split_substitutions(text: str, result: Decomposition, depth: int) -> str:
    """Replace ``$(…)`` and backtick spans with placeholders, recursing into them.

    Substitutions are extracted *before* operator splitting because each one is
    self-contained: whatever runs inside it is a command in its own right and
    must be judged with role ``substitution``, not silently absorbed into the
    surrounding fragment.
    """
    if depth > _MAX_SUBSTITUTION_DEPTH:
        result.problems.append("substitution nesting exceeded the supported depth")
        return text

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]

        if char == "\\" and index + 1 < length:
            out.append(text[index : index + 2])
            index += 2
            continue

        if char in "'\"":
            quote = char
            end = _scan_quoted(text, index)
            if end < 0:
                result.problems.append(f"unterminated {quote} quote")
                out.append(text[index:])
                break
            out.append(text[index:end])
            index = end
            continue

        if char == "`":
            end = text.find("`", index + 1)
            if end < 0:
                result.problems.append("unterminated backtick substitution")
                out.append(text[index:])
                break
            _absorb(text[index + 1 : end], result, depth + 1)
            out.append("\x00SUB\x00")
            index = end + 1
            continue

        if char == "$" and index + 1 < length and text[index + 1] == "(":
            end = _scan_balanced(text, index + 1)
            if end < 0:
                result.problems.append("unterminated $( ) substitution")
                out.append(text[index:])
                break
            _absorb(text[index + 2 : end - 1], result, depth + 1)
            out.append("\x00SUB\x00")
            index = end
            continue

        if char == "$" and index + 1 < length and text[index + 1] == "{":
            end = text.find("}", index + 2)
            if end < 0:
                out.append(text[index:])
                break
            out.append(text[index : end + 1])
            index = end + 1
            continue

        out.append(char)
        index += 1

    return "".join(out)


def _absorb(inner: str, result: Decomposition, depth: int) -> None:
    """Decompose a substitution body into fragments with role ``substitution``."""
    if not inner.strip():
        return
    _split_chain(_split_substitutions(inner, result, depth), result, first_role=ROLE_SUBSTITUTION)


def _scan_quoted(text: str, start: int) -> int:
    """Return the index just past the closing quote, or -1 when unterminated."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\" and quote == '"' and index + 1 < len(text):
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return -1


def _scan_balanced(text: str, open_index: int) -> int:
    """Return the index just past the ``)`` closing the ``(`` at ``open_index``."""
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char in "'\"":
            end = _scan_quoted(text, index)
            if end < 0:
                return -1
            index = end
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return -1


def _split_chain(text: str, result: Decomposition, *, first_role: str = ROLE_STANDALONE) -> None:
    """Split on ``|``/``||`/``&&``/``;``/``&``/newline and emit each pipeline.

    ``first_role`` seeds the role of the first segment so a caller can mark a
    whole chain as a substitution rather than a top-level command.
    """
    segment: list[str] = []
    role = first_role
    index = 0
    length = len(text)

    def flush() -> None:
        nonlocal role
        if segment:
            _tokenize("".join(segment), role, result)
        segment.clear()
        role = ROLE_STANDALONE

    while index < length:
        char = text[index]

        if char in "'\"":
            end = _scan_quoted(text, index)
            if end < 0:
                result.problems.append(f"unterminated {char} quote")
                segment.append(text[index:])
                break
            segment.append(text[index:end])
            index = end
            continue

        two = text[index : index + 2]
        if two in ("&&", "||", "|&"):
            flush()
            index += 2
            continue
        if char == "|":
            flush()
            role = ROLE_PIPE_FILTER
            index += 1
            continue
        if char in ";\n":
            flush()
            index += 1
            continue
        if char == "&":
            flush()
            index += 1
            continue
        if char == "\x00":
            segment.append(char)
            index += 1
            continue

        segment.append(char)
        index += 1

    flush()


_REDIRECT_TOKENS = frozenset({">", ">>", "<", "2>", "2>>", "&>", "&>>", "1>", "1>>", "<<<"})

# Longest-first, so `>>` wins over `>` and `2>>` over `2>`.
_REDIRECT_OPS_BY_LENGTH = tuple(sorted(_REDIRECT_TOKENS, key=len, reverse=True))


def _tokenize(text: str, role: str, result: Decomposition) -> None:
    """Turn one pipeline segment into argv plus redirections."""
    argv: list[str] = []
    redirections: list[tuple[str, str]] = []
    buffer: list[str] | None = None

    def end_token() -> None:
        nonlocal buffer
        if buffer is not None:
            argv.append("".join(buffer))
            buffer = None

    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char in "'\"":
            end = _scan_quoted(text, index)
            if end < 0:
                result.problems.append(f"unterminated {char} quote")
                end_token()
                break
            if buffer is None:
                buffer = []
            buffer.append(text[index + 1 : end - 1])
            index = end
            continue

        if char == "\\" and index + 1 < length:
            # A backslash is treated as a LITERAL character, not an escape.
            #
            # This is a deliberate trade-off. Alpha's host shells are Git
            # Bash/MSYS, PowerShell, and cmd, and the model routinely writes
            # Windows paths like `C:\Users\me\file.txt`. POSIX escape semantics
            # would silently turn that into `C:Usersmefile.txt` — a different
            # string than the one the shell receives, which is exactly the kind
            # of parse/act divergence a security gate must not have.
            #
            # The direction of the error is safe: an obfuscated command such as
            # `ba\sh -c '…'` no longer normalizes to `bash`, so it matches no
            # rule and lands on `defer` rather than being allowed. Interpreting
            # the character literally can only ever make the policy *less*
            # willing to allow, never more.
            if buffer is None:
                buffer = []
            buffer.append(char)
            buffer.append(text[index + 1])
            index += 2
            continue

        if char.isspace():
            end_token()
            index += 1
            continue

        # A substitution placeholder is one opaque argument; it was already
        # decomposed into its own fragments with role `substitution`.
        if char == "\x00":
            end = text.find("\x00", index + 1)
            if end < 0:
                result.problems.append("unterminated substitution placeholder")
                end_token()
                break
            end_token()
            argv.append(text[index : end + 1])
            index = end + 1
            continue

        # A redirection operator is only recognised at a token boundary, so a
        # filename containing '>' inside a word is not mistaken for one.
        if buffer is None:
            direction = next((op for op in _REDIRECT_OPS_BY_LENGTH if text.startswith(op, index)), None)
            if direction is not None:
                target, index = _read_redirection_target(text, index + len(direction), result)
                redirections.append((direction, target))
                continue

        if buffer is None:
            buffer = []
        buffer.append(char)
        index += 1

    end_token()

    if not argv:
        return

    result.fragments.append(CommandFragment(tuple(argv), role, tuple(redirections)))


def _read_redirection_target(text: str, start: int, result: Decomposition) -> tuple[str, int]:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        result.problems.append("redirection with no target")
        return "", index
    if text[index] in "'\"":
        end = _scan_quoted(text, index)
        if end < 0:
            result.problems.append("unterminated quote in redirection target")
            return "", len(text)
        return text[index + 1 : end - 1], end
    end = index
    while end < len(text) and not text[end].isspace() and text[end] not in ";\n|&":
        end += 1
    return text[index:end], end


def decompose(command: str) -> Decomposition:
    """Split a shell command string into structurally-classified fragments.

    Conservative by construction: anything the tokenizer cannot fully account for
    is recorded in :attr:`Decomposition.problems`, and a non-empty problem list
    must force a non-allow verdict. It never raises on malformed input.
    """
    result = Decomposition()
    if not command or not command.strip():
        return result
    stripped = _split_substitutions(command, result, 0)
    _split_chain(stripped, result)
    return result


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


class CommandRule(BaseModel):
    """One policy rule.

    Selector fields are ANDed. ``has_any``/``has_all``/``unless`` match against
    the fragment's argument tokens with quoting already removed, so a commit
    message is a single opaque token and never inspected.
    """

    executables: list[str] = Field(default_factory=list, description="Normalized executable names this rule applies to.")
    roles: list[str] = Field(default_factory=list, description="Structural roles this rule applies to; empty means any role.")
    has_any: list[str] = Field(default_factory=list, description="Match when at least one of these tokens is present.")
    has_all: list[str] = Field(default_factory=list, description="Match only when all of these tokens are present.")
    unless: list[str] = Field(default_factory=list, description="Suppress the match when any of these tokens is present.")
    verdict: Verdict = Field(description="Verdict contributed when this rule matches.")
    reason: str = Field(default="", description="Operator-facing explanation, surfaced to the model on a non-allow verdict.")


def _base_rules() -> list[CommandRule]:
    """Rules shared by every profile: the genuinely read-only surface."""
    return [
        CommandRule(executables=sorted(_READ_ONLY_EXECUTABLES), verdict="allow"),
        CommandRule(executables=list(_PIPELINE_ONLY_EXECUTABLES), roles=[ROLE_PIPE_FILTER, ROLE_SUBSTITUTION], verdict="allow"),
        CommandRule(executables=["git"], has_any=sorted(_GIT_READ_SUBCOMMANDS), verdict="allow"),
        # Classified as `ask` rather than left to `defer` even under read-only:
        # the policy knows exactly what a repository mutation is, and an
        # accurate "ask" reason is more actionable to the model than a generic
        # "unclassified".
        CommandRule(executables=["git"], has_any=sorted(_GIT_WRITE_SUBCOMMANDS), verdict="ask", reason="mutates the repository"),
        CommandRule(
            executables=["git"],
            has_all=["push", "--force"],
            unless=["--force-with-lease"],
            verdict="ask",
            reason="rewrites remote history; prefer --force-with-lease",
        ),
        CommandRule(executables=sorted(_PROJECT_CODE_EXECUTABLES), verdict="ask", reason="builds or executes project code"),
    ]


#: `gh` subcommands that only read. `gh api` is deliberately absent: it can
#: issue a DELETE with the same spelling as a GET, so it defers unless an
#: operator writes a narrower rule.
_GH_READ_SUBCOMMANDS: dict[str, tuple[str, ...]] = {
    "pr": ("list", "view", "status", "diff", "checks"),
    "issue": ("list", "view", "status"),
    "repo": ("view", "list"),
    "run": ("list", "view"),
    "workflow": ("list", "view"),
    "cache": ("list",),
}


def _gh_rules() -> list[CommandRule]:
    return [CommandRule(executables=["gh"], has_all=[group, verb], verdict="allow") for group, verbs in _GH_READ_SUBCOMMANDS.items() for verb in verbs]


READ_ONLY_PROFILE: tuple[CommandRule, ...] = tuple(_base_rules()) + tuple(_gh_rules())

#: Currently identical to `read-only`. The distinction is deliberate and
#: forward-looking: widening `repo-write` means *permitting* more, never
#: restricting, so an operator can move up a profile without a surprise
#: tightening. Kept as a separate name so configuration does not have to be
#: rewritten when the profiles finally diverge.
REPO_WRITE_PROFILE: tuple[CommandRule, ...] = READ_ONLY_PROFILE

PROFILES: dict[str, tuple[CommandRule, ...]] = {
    "read-only": READ_ONLY_PROFILE,
    "repo-write": REPO_WRITE_PROFILE,
}

#: deny > ask > allow > (unmatched). The unmatched sentinel must rank *below*
#: allow, otherwise the initial "no rule matched" state would outrank every
#: allow rule and nothing could ever be permitted.
_SEVERITY = {"allow": 0, "defer": 1, "ask": 2, "deny": 3}
_UNMATCHED = -1


@dataclass(frozen=True)
class FragmentVerdict:
    verdict: str
    reason: str = ""
    rule: str = ""


def _matches(rule: CommandRule, fragment: CommandFragment) -> bool:
    if rule.executables and fragment.executable not in rule.executables:
        return False
    if rule.roles and fragment.role not in rule.roles:
        return False
    tokens = {token for token in fragment.argv[1:] if token}
    if rule.has_all and not set(rule.has_all).issubset(tokens):
        return False
    if rule.has_any and not set(rule.has_any) & tokens:
        return False
    if rule.unless and set(rule.unless) & tokens:
        return False
    return True


def _judge_fragment(fragment: CommandFragment, rules: tuple[CommandRule, ...]) -> FragmentVerdict:
    executable = fragment.executable

    for name, reason in _HARD_DENY:
        # Match `mkfs` against `mkfs.ext4`/`mkfs.xfs` without also matching an
        # unrelated tool that merely starts with the same characters.
        if executable == name or executable.startswith(f"{name}."):
            return FragmentVerdict("deny", reason, f"hard-deny:{name}")

    # Interpreters and inline-code runners can never be allowed by configuration.
    # Checked before rule evaluation so no allow rule can outrank it.
    if executable in INTERPRETERS:
        return FragmentVerdict("defer", f"'{executable}' executes arbitrary code and is never auto-allowed", "interpreter")
    if any(token in _INLINE_EXEC_FLAGS for token in fragment.argv[1:2]):
        return FragmentVerdict("defer", "inline code execution is never auto-allowed", "interpreter-payload")

    best = FragmentVerdict("defer", "no rule classified this command", "default")
    best_severity = _UNMATCHED
    for index, rule in enumerate(rules):
        if not _matches(rule, fragment):
            continue
        severity = _SEVERITY[rule.verdict]
        # `>=` so the LAST rule of equal severity wins. That is how the
        # specific rule refines the general one: the generic `git push` ask is
        # listed before the `push --force` ask, and the latter supplies the
        # actionable `--force-with-lease` reason instead of "mutates the
        # repository". Verdict *equality* still cannot change the outcome, so
        # reordering rules that disagree remains safe.
        if severity >= best_severity:
            best_severity = severity
            best = FragmentVerdict(rule.verdict, rule.reason, f"rule[{index}]")
    return best


@dataclass(frozen=True)
class PolicyOutcome:
    """Whole-command verdict plus the per-fragment detail that produced it."""

    verdict: str
    reason: str = ""
    rule: str = ""
    fragments: tuple[tuple[str, str, str], ...] = ()

    @property
    def allow(self) -> bool:
        return self.verdict == "allow"


def evaluate(command: str, rules: tuple[CommandRule, ...]) -> PolicyOutcome:
    """Evaluate a full command string against ``rules``.

    Composition across fragments: any deny wins, else any ask, else all allow,
    else defer. Rule *order* never changes the verdict, only the cited reason.
    """
    decomposition = decompose(command)
    if not decomposition.fragments:
        return PolicyOutcome("defer", "command is empty or contains no executable", "empty")

    if not decomposition.complete:
        detail = "; ".join(sorted(set(decomposition.problems)))
        return PolicyOutcome(
            "defer",
            f"command could not be fully parsed, so it is not auto-approved: {detail}. Split it into simpler commands, or avoid constructs the policy cannot verify.",
            "unparseable",
        )

    judged = [(_judge_fragment(fragment, rules), fragment) for fragment in decomposition.fragments]
    summary = tuple((fragment.executable, verdict.verdict, verdict.rule) for verdict, fragment in judged)

    worst = max(judged, key=lambda pair: _SEVERITY[pair[0].verdict])
    verdict = worst[0].verdict
    if verdict == "allow":
        return PolicyOutcome("allow", "", "all-fragments-allowed", summary)

    if verdict == "defer" and all(entry.verdict == "defer" for entry, _ in judged):
        names = ", ".join(sorted({fragment.executable or "?" for _, fragment in judged}))
        return PolicyOutcome(
            "defer",
            f"no policy rule classifies this command ({names}). Rewrite it using an allowlisted command, or ask the operator to extend the command policy.",
            worst[0].rule,
            summary,
        )

    return PolicyOutcome(verdict, worst[0].reason, worst[0].rule, summary)


_REASON_CODES = {
    "deny": "oap.command_denied",
    "ask": "oap.command_needs_approval",
    "defer": "oap.command_unclassified",
}


class CommandPolicyProvider:
    """A :class:`~alpha.guardrails.provider.GuardrailProvider` for command content.

    Non-``bash`` tools are not inspected: the ``bash`` tool is the only Alpha tool
    that takes a raw command string, so gating on its name keeps this provider
    from re-deciding decisions the tool-name allowlist already made.
    """

    name = "command_policy"
    policy_id = "alpha.guardrails.command_policy"
    policy_version = "1.0.0"

    def __init__(
        self,
        *,
        profile: str = "read-only",
        rules: list[CommandRule] | None = None,
        guarded_tools: list[str] | None = None,
    ):
        if rules is None:
            if profile not in PROFILES:
                raise ValueError(f"unknown command policy profile: {profile!r} (expected one of {sorted(PROFILES)})")
            self._rules = PROFILES[profile]
        else:
            self._rules = tuple(rules)
        # None means "the bash tool only"; an explicit list overrides it. The
        # distinction matters: a truthiness test would collapse [] into None and
        # silently re-enable gating that the operator meant to disable.
        self._guarded = {"bash"} if guarded_tools is None else set(guarded_tools)
        self._profile = profile if rules is None else "custom"

    @override
    def release_policy_parameters(self) -> dict[str, object]:
        return {
            "profile": self._profile,
            "guarded_tools": sorted(self._guarded),
            "rule_count": len(self._rules),
        }

    def _applies(self, tool_name: str) -> bool:
        return tool_name in self._guarded

    @staticmethod
    def _command_of(tool_input: dict[str, Any]) -> str | None:
        value = tool_input.get("command")
        return value if isinstance(value, str) else None

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        if not self._applies(request.tool_name):
            return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.not_guarded", message="tool is not command-gated")], policy_id=self.policy_id)

        command = self._command_of(request.tool_input)
        if command is None:
            # No command string means the model called the tool with unexpected
            # arguments. Refuse rather than wave it through.
            return GuardrailDecision(
                allow=False,
                reasons=[GuardrailReason(code="oap.command_missing", message="guarded tool called without a 'command' string; refusing to guess intent")],
                policy_id=self.policy_id,
                metadata={"verdict": "deny", "rule": "command-missing"},
            )

        outcome = evaluate(command, self._rules)
        metadata: dict[str, Any] = {
            "verdict": outcome.verdict,
            "rule": outcome.rule,
            "fragments": [list(entry) for entry in outcome.fragments],
            "profile": self._profile,
        }
        if outcome.allow:
            return GuardrailDecision(allow=True, reasons=[GuardrailReason(code="oap.command_allowed", message="all command fragments are allowlisted")], policy_id=self.policy_id, metadata=metadata)

        code = _REASON_CODES.get(outcome.verdict, "oap.command_unclassified")
        return GuardrailDecision(
            allow=False,
            reasons=[GuardrailReason(code=code, message=outcome.reason or "command is not permitted by policy")],
            policy_id=self.policy_id,
            metadata=metadata,
        )

    @override
    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        return self.evaluate(request)


def matches_executable(pattern: str, executable: str) -> bool:
    """Expose the matcher for tests and future glob-based selectors.

    Unused by the current rule model on purpose — see the module docstring for
    why rules avoid glob selectors.
    """
    return fnmatch.fnmatch(executable, pattern)
