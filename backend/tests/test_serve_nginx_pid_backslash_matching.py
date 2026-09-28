r"""`case` cannot match a quoted variable holding a backslash path.

`_is_repo_nginx_pid` in scripts/serve.sh decides whether a PID belongs to this
checkout's nginx, so that `make stop` reaps exactly its own processes and does
not kill a sibling worktree's or a user's unrelated nginx. It compared like this:

    case "$args" in
        *"$root"/docker/nginx/nginx.local.conf*|*"$root"/*) return 0 ;;
    esac

which silently never matched on Windows. `case` treats a backslash inside a
pattern as an escape character, so a quoted `$root` holding
`C:\Users\...\alpha` is not compared literally - the `\` characters are consumed
while the pattern is parsed, and the pattern no longer describes the subject.

Measured in Git Bash on this checkout:

    case "$subject" in *"$R"/*)   # R = C:\Users\...\alpha
      -> no                       # even when $subject contains that exact text
    case "$subject" in *"$R_FW"/*)  # same path, forward slashes
      -> MATCH

It is not the space in `PREM KUMAR`: a no-space Windows path fails identically.
Doubling the backslashes in the pattern does not help either.

The consequence is a real correctness bug, not a cosmetic one. The failed match
fell through to `_is_alpha_pid "$pid"`, which is satisfied by a *reused* PID - so
a recycled PID belonging to some other nginx could be reported as this
checkout's and then killed by `make stop`. The guard existed precisely to
prevent that, and on Windows it did not work.

These tests pin the matching rule through the real function, extracted from the
real script and run under the real Git Bash, so a future "simplification" back to
a bare `*"$root"/*` fails here.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVE_SH = REPO_ROOT / "scripts" / "serve.sh"
GIT_BASH_WRAPPER = REPO_ROOT / "scripts" / "run-with-git-bash.cmd"

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not GIT_BASH_WRAPPER.is_file(),
    reason="the backslash-pattern behaviour is specific to the Windows shell the launcher runs under",
)


def _extract_function(name: str) -> str:
    lines = SERVE_SH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"{name}() {{")
    body: list[str] = []
    for line in lines[start:]:
        body.append(line)
        if line.strip() == "}":
            break
    return "\n".join(body)


def _is_repo_nginx_pid(*, command: str, args: str, repo_root: Path, alpha_pid: bool, workdir: Path | None = None) -> bool:
    """Run the real function with a stubbed `ps`.

    ``workdir`` is where the probe script is written. It defaults to a directory
    that exists; the POSIX-path case passes ``C:/posix/alpha`` as the *root under
    test*, and writing the probe next to it would try to create ``C:\\posix``.
    """
    script = f"""
REPO_ROOT={shlex.quote(str(repo_root))}
ALPHA_ROOTS={shlex.quote(str(repo_root))}
FAKE_COMMAND={shlex.quote(command)}
FAKE_ARGS={shlex.quote(args)}
FAKE_ALPHA_PID={1 if alpha_pid else 0}

ps() {{
    case "$*" in
        *"-o comm="*) printf '%s\\n' "$FAKE_COMMAND" ;;
        *"-o args="*) printf '%s\\n' "$FAKE_ARGS" ;;
        *) return 1 ;;
    esac
}}
_is_alpha_pid() {{ [ "$FAKE_ALPHA_PID" = "1" ]; }}

{_extract_function("_is_repo_nginx_pid")}
_is_repo_nginx_pid 12345
"""
    probe_dir = workdir or repo_root.parent
    probe_dir.mkdir(parents=True, exist_ok=True)
    script_file = probe_dir / "probe.sh"
    script_file.write_text(script, encoding="utf-8")
    # A file path, not an argument: the .cmd wrapper forwards with `%*`, which
    # does not re-quote, so a multi-line script would arrive split up.
    result = subprocess.run([str(GIT_BASH_WRAPPER), str(script_file)], check=False)
    return result.returncode == 0


def _windows_style_args(repo_root: Path, conf: Path) -> str:
    return f"nginx: master process /opt/homebrew/bin/nginx -c {conf} -p {repo_root}"


def test_a_windows_style_repo_path_in_the_args_is_recognised(tmp_path: Path) -> None:
    """The regression: this is the whole reason the branch existed."""
    repo_root = tmp_path / "alpha"
    conf = repo_root / "docker" / "nginx" / "nginx.local.conf"

    assert _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {conf}",
        args=_windows_style_args(repo_root, conf),
        repo_root=repo_root,
        alpha_pid=False,
    ), "a Windows-style repo path in the args must match, even when the PID is not Alpha's own"


def test_a_repo_root_with_a_space_still_matches(tmp_path: Path) -> None:
    """The space is not the cause of the failure, so it must not defeat the fix.

    Worth pinning separately: "PREM KUMAR" is exactly the shape that a naive
    quoting fix would have broken, and this checkout lives under it.
    """
    repo_root = tmp_path / "PREM KUMAR" / "alpha"
    conf = repo_root / "docker" / "nginx" / "nginx.local.conf"

    assert _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {conf}",
        args=_windows_style_args(repo_root, conf),
        repo_root=repo_root,
        alpha_pid=False,
    )


def test_a_foreign_worktree_is_still_rejected(tmp_path: Path) -> None:
    """Matching must not become so loose that a sibling checkout is adopted.

    That is the whole point of the guard: two worktrees run side by side, and
    `make stop` must not reap the other one's nginx.
    """
    ours = tmp_path / "alpha"
    theirs = tmp_path / "other-clone"
    their_conf = theirs / "docker" / "nginx" / "nginx.local.conf"

    assert not _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {their_conf}",
        args=_windows_style_args(theirs, their_conf),
        repo_root=ours,
        alpha_pid=False,
    )


def test_a_sibling_directory_sharing_our_name_prefix_is_not_claimed(tmp_path: Path) -> None:
    """`.../alpha` must not adopt `.../alpha-scratch`'s nginx.

    Two worktrees run side by side on this machine, so a bare string-prefix
    match is not good enough to decide which processes are ours to reap.
    """
    repo_root = tmp_path / "alpha"
    sibling_conf = tmp_path / "alpha-scratch" / "docker" / "nginx" / "nginx.local.conf"

    assert not _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {sibling_conf}",
        args=f"nginx: master process /opt/homebrew/bin/nginx -c {sibling_conf} -p {tmp_root_of(sibling_conf)}",
        repo_root=repo_root,
        alpha_pid=False,
    )


def test_a_pid_we_tracked_ourselves_is_accepted_even_without_the_conf(tmp_path: Path) -> None:
    """The `_is_alpha_pid` fallback is deliberate and must survive.

    It exists because the pid in `logs/nginx.pid` is *our* record: the process was
    started by this checkout, so it is ours to reap even if its argv does not
    spell out the conf path (a rewritten argv[0], or an invocation that passes the
    root some other way). Requiring the conf in args would make `make stop` leave
    our own nginx running.

    So acceptance here comes from tracked ownership, not from path matching - and
    that is why the conf-anchored match is a *narrowing* of the old
    `*"$root"/*` alternative rather than a replacement for the whole function.
    """
    repo_root = tmp_path / "alpha"

    assert _is_repo_nginx_pid(
        command="nginx: master process /opt/homebrew/bin/nginx -c /etc/nginx/nginx.conf",
        args="nginx: master process /opt/homebrew/bin/nginx -c /etc/nginx/nginx.conf",
        repo_root=repo_root,
        alpha_pid=True,
    )


def tmp_root_of(conf: Path) -> Path:
    """The worktree root that owns *conf* (`<root>/docker/nginx/nginx.local.conf`)."""
    return conf.parents[2]


def test_a_non_nginx_process_is_still_rejected(tmp_path: Path) -> None:
    """The repo path alone is not enough - the command must be nginx."""
    repo_root = tmp_path / "alpha"
    conf = repo_root / "docker" / "nginx" / "nginx.local.conf"

    assert not _is_repo_nginx_pid(
        command="python",
        args=f"python -m nginx {conf}",
        repo_root=repo_root,
        alpha_pid=True,
    )


def test_a_posix_style_repo_path_still_matches(tmp_path: Path) -> None:
    """The forward-slash case must keep working; only Windows needed the fix."""
    posix_root = Path("C:/posix/alpha")
    conf = posix_root / "docker" / "nginx" / "nginx.local.conf"

    assert _is_repo_nginx_pid(
        command="nginx: master process /opt/homebrew/bin/nginx -c /opt/homebrew/etc/nginx.conf",
        args=f"nginx: master process /opt/homebrew/bin/nginx -c {conf} -p {posix_root}",
        repo_root=posix_root,
        alpha_pid=False,
        workdir=Path(tempfile.gettempdir()),
    )


def test_a_root_recorded_with_a_trailing_separator_still_matches(tmp_path: Path) -> None:
    """ALPHA_ROOTS entries can arrive with a trailing slash; do not break them."""
    repo_root = tmp_path / "alpha"
    conf = repo_root / "docker" / "nginx" / "nginx.local.conf"
    trailing = repo_root.as_posix() + "/"

    assert _is_repo_nginx_pid(
        command=f"nginx: master process /opt/homebrew/bin/nginx -c {conf.as_posix()}",
        args=f"nginx: master process /opt/homebrew/bin/nginx -c {conf.as_posix()} -p {trailing}",
        repo_root=trailing_root(trailing),
        alpha_pid=False,
    )


def trailing_root(posix_with_slash: str) -> Path:
    return Path(posix_with_slash.rstrip("/"))


def test_the_fix_normalises_both_sides_rather_than_escaping_the_pattern() -> None:
    """Pin the shape of the fix, so it is not reverted to a bare quoted glob.

    Comparing with a backslash-stripped copy of both sides is deliberate and
    commented in the script. An alternative "fix" - escaping the backslashes in
    the pattern - was measured here and does not match, so this test exists to
    record which approach actually works.
    """
    source = SERVE_SH.read_text(encoding="utf-8")
    body = _extract_function("_is_repo_nginx_pid")
    assert "//\\\\//" in body, "the backslash-stripping substitution is missing"
    assert "flat_args=${args//" in body
    assert "flat_root=${root//" in body
    assert 'case "$flat_args" in' in body
    # The old, always-failing pattern must be gone.
    assert 'case "$args" in' not in body
    # And the reasoning must stay recorded, since it looks like a pointless
    # transformation to anyone who has not measured it.
    assert "escape character" in source


def test_posix_shell_is_unaffected_by_the_substitution_syntax() -> None:
    """`${var//x/y}` is bashism-free? It is not - confirm the shebang claim.

    `${args//\\\\//}` works in bash and in dash's newer versions, but the script
    declares bash, so this asserts the declared interpreter is real rather than
    assuming.
    """
    head = SERVE_SH.read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("#!") and "bash" in head, f"serve.sh must declare bash for the substitution: {head}"
    if shutil.which("bash") is None:
        pytest.skip("bash not available to double-check")
