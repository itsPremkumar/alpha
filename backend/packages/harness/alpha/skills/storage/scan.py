"""A cycle-safe, bounded SKILL.md directory walk.

``os.walk(root, followlinks=True)`` is what the skill storages have always used,
and it is unbounded in two ways a skill root can actually reach:

* **Symlink cycles / duplicate subtrees.** Every storage documents a *supported*
  configuration in which a skill package directory is a symlink to an external
  tree (``SkillStorage._is_external_skill_directory_symlink`` and its per-user
  sibling accept a one-level package-directory symlink), so ``followlinks=True``
  is deliberate. But ``os.walk`` resolves every symlink it meets and keeps no
  record of where it has been: a link pointing at one of its own ancestors is
  followed forever, and a link pointing at a *sibling* subtree makes the same
  physical package be yielded twice under two different relative paths (so the
  same skill lands in the registry and the sandbox projection twice, under
  container paths that both resolve to one directory). A cycle does not merely
  run long — each iteration enqueues a fresh path string, so the pending set
  grows without bound and the scan never finishes.

* **Breadth and depth.** Nothing caps how deep or how many directories are
  visited, so a tree with no ``SKILL.md`` anywhere (the package boundary then
  never stops the descent) is walked to completion on every ``load_skills()``
  call — and ``load_skills()`` is called on *every model call* by both skill
  middlewares, deliberately, so that an operator disable takes effect
  immediately.

This module keeps ``os.walk`` and adds exactly the three missing guards at the
one point where the walk can still be steered — the ``dir_names`` in-place
mutation the callers already use — so traversal order, the dot-directory prune,
the "a directory containing SKILL.md is a package boundary" rule, and therefore
every container path a healthy tree produces are all unchanged.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

from alpha.skills.types import SKILL_MD_FILE, SkillCategory

logger = logging.getLogger(__name__)

#: Deepest a skill package may sit below its category root. Real layouts are
#: flat or one namespace deep (``public/team/helper`` is depth 2) and the shipped
#: tree's deepest package is depth 1. A generous ceiling that a cycle still
#: cannot exhaust.
MAX_SKILL_SCAN_DEPTH = 8

#: Most directories one category root may contribute to a single scan. A
#: namespace with thousands of skill directories is already far past anything the
#: prompt or the sandbox projection can usefully hold, so this is a correctness
#: backstop rather than a policy limit.
MAX_SKILL_SCAN_DIRECTORIES = 10_000


class _BoundedWalk:
    """Per-category-root state for the cycle, depth, and breadth guards.

    One instance per scan of one category root; it is not reusable, because the
    visited set and the running counts are exactly the state being accumulated.
    """

    __slots__ = ("_root", "_visited", "_directories", "_excluded", "_exhausted")

    def __init__(self, root: Path, excluded: frozenset[str]) -> None:
        self._root = root
        self._excluded = excluded
        self._visited: set[tuple[int, int]] = set()
        self._directories = 0
        self._exhausted = False

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def _identity(self, path: Path) -> tuple[int, int] | None:
        """Resolved (device, inode) of *path*, or ``None`` if unresolvable.

        Identity, not the path string, is the cycle key: the whole point is that
        two different path strings can name one directory. A dangling symlink, a
        directory that vanished mid-scan, or a path the OS refuses to resolve
        returns ``None`` and is simply not descended into.
        """
        try:
            info = path.resolve().stat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino)

    def enter(self, current_root: Path | str, dir_names: list[str]) -> list[str]:
        """Register *current_root* and return the subdirectories to descend into.

        Called once per ``os.walk`` iteration, before the caller's own
        ``SKILL_MD_FILE`` check, so the guard applies to namespace directories
        that recurse as well as to packages. ``os.walk`` hands over its root as a
        ``str``, so it is coerced here rather than at every call site.
        """
        if self._exhausted:
            return []

        current_path = Path(current_root)
        marker = self._identity(current_path)
        if marker is None:
            # Unresolvable: do not descend anywhere below it either.
            return []
        if marker in self._visited:
            logger.debug("Skill scan: %s was already visited; not descending again", current_path)
            return []
        self._visited.add(marker)

        depth = len(current_path.relative_to(self._root).parts)
        if depth > MAX_SKILL_SCAN_DEPTH:
            logger.warning(
                "Skill scan of %s stopped descending at %s: depth %d exceeds the %d-level limit",
                self._root,
                current_path,
                depth,
                MAX_SKILL_SCAN_DEPTH,
            )
            self._exhausted = True
            return []

        self._directories += 1
        if self._directories > MAX_SKILL_SCAN_DIRECTORIES:
            logger.warning(
                "Skill scan of %s stopped after %d directories: the %d-directory limit was reached",
                self._root,
                self._directories - 1,
                MAX_SKILL_SCAN_DIRECTORIES,
            )
            self._exhausted = True
            return []

        # Prune, in two passes, because the two failure modes need different
        # information:
        #
        # 1. Anything already visited. This is what catches an *ancestor* cycle
        #    (``public/deep/loop -> public``): by the time ``loop`` is offered,
        #    ``public`` is in ``visited``, so it is not descended into again.
        # 2. Within this one batch, a symlink that resolves to a directory the
        #    same batch also reaches *as a real path* (``public/alias ->
        #    public/team``). Nothing is visited yet at this point, so pass 1
        #    cannot see it — and descending both scans one physical package
        #    twice, under two container paths that resolve to one directory.
        #    Prefer the real directory.
        #
        # Only a symlink is ever dropped by pass 2, and only when its target is
        # present in the same batch as a real directory, so the documented
        # "package directory is a symlink to an external tree" layout is
        # untouched: such a link resolves outside the batch and is kept.
        identities: dict[tuple[int, int], list[str]] = {}
        symlinks: set[str] = set()
        candidates: list[tuple[str, tuple[int, int]]] = []
        for name in sorted(dir_names):
            if name.startswith(".") or name in self._excluded:
                continue
            child = current_path / name
            marker = self._identity(child)
            if marker is None or marker in self._visited:
                # Unresolvable, or a link to somewhere the walk already covered.
                logger.debug("Skill scan: pruning already-visited or unresolvable %s", child)
                continue
            candidates.append((name, marker))
            identities.setdefault(marker, []).append(name)
            if child.is_symlink():
                symlinks.add(name)

        keep: list[str] = []
        for name, marker in candidates:
            if name in symlinks and any(sibling not in symlinks for sibling in identities[marker]):
                logger.debug("Skill scan: pruning %s, a symlink duplicating a sibling directory", name)
                continue
            keep.append(name)
        return keep


def iter_skill_md_files(
    category: SkillCategory,
    category_root: Path,
    *,
    exclude_dir_names: frozenset[str] = frozenset(),
) -> Iterator[tuple[SkillCategory, Path, Path]]:
    """Yield ``(category, category_root, skill_md_path)`` under *category_root*.

    Bounded and cycle-safe — see the module docstring for why the bare
    ``os.walk(followlinks=True)`` this replaces is neither. *exclude_dir_names*
    are pruned alongside dot-directories (the per-user storages pass
    ``.history``).
    """
    if not category_root.is_dir():
        return

    walk = _BoundedWalk(category_root, exclude_dir_names)
    for current_root, dir_names, file_names in os.walk(category_root, followlinks=True):
        dir_names[:] = walk.enter(current_root, dir_names)
        if SKILL_MD_FILE not in file_names:
            continue
        # A directory containing SKILL.md is a package boundary: a nested
        # SKILL.md is that package's support data (eval fixtures), not a
        # runtime skill. A namespace directory without SKILL.md still recurses.
        dir_names.clear()
        yield category, category_root, Path(current_root) / SKILL_MD_FILE

    if walk.exhausted:
        logger.warning(
            "Skill scan of %s was bounded: some skills in this tree were not considered. "
            "Check for a symlink cycle, a symlink to a sibling subtree, or an unexpectedly large skill directory.",
            category_root,
        )


__all__ = ["MAX_SKILL_SCAN_DEPTH", "MAX_SKILL_SCAN_DIRECTORIES", "iter_skill_md_files"]
