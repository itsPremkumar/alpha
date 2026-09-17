from __future__ import annotations

from .context_data import ContextHandle, ContextStore


class ContextTransformer:
    """
    Programmatic transformations over ContextHandles.
    Allows agents to filter, slice, project, and chunk massive contexts
    without loading the full text into the LLM prompt.
    """

    def __init__(self, store: ContextStore) -> None:
        self.store = store

    def grep(
        self,
        handle_id: str,
        query: str,
        case_sensitive: bool = False,
        context_lines: int = 0,
    ) -> ContextHandle | None:
        content = self.store.get_content(handle_id)
        parent_handle = self.store.get_handle(handle_id)
        if content is None or parent_handle is None:
            return None

        lines = content.splitlines()
        matched_indices = set()

        for idx, line in enumerate(lines):
            target = line if case_sensitive else line.lower()
            q = query if case_sensitive else query.lower()
            if q in target:
                for c in range(max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)):
                    matched_indices.add(c)

        matched_lines = [f"{i+1}: {lines[i]}" for i in sorted(matched_indices)]
        new_content = "\n".join(matched_lines)

        return self.store.register(
            content=new_content,
            label=f"grep_{query[:12]}_of_{parent_handle.label}",
            metadata={"source_handle": handle_id, "query": query, "matches_count": len(matched_indices)},
            parent_handle_id=handle_id,
        )

    def slice_lines(
        self,
        handle_id: str,
        start_line: int,
        end_line: int,
    ) -> ContextHandle | None:
        content = self.store.get_content(handle_id)
        parent_handle = self.store.get_handle(handle_id)
        if content is None or parent_handle is None:
            return None

        lines = content.splitlines()
        # 1-indexed slice
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        sliced_lines = lines[start_idx:end_idx]
        new_content = "\n".join(sliced_lines)

        return self.store.register(
            content=new_content,
            label=f"slice_{start_line}_{end_line}_of_{parent_handle.label}",
            metadata={"source_handle": handle_id, "start_line": start_line, "end_line": end_line},
            parent_handle_id=handle_id,
        )

    def chunk(
        self,
        handle_id: str,
        lines_per_chunk: int = 200,
    ) -> list[ContextHandle]:
        content = self.store.get_content(handle_id)
        parent_handle = self.store.get_handle(handle_id)
        if content is None or parent_handle is None:
            return []

        lines = content.splitlines()
        chunk_handles: list[ContextHandle] = []

        for i in range(0, len(lines), lines_per_chunk):
            chunk_slice = lines[i : i + lines_per_chunk]
            chunk_text = "\n".join(chunk_slice)
            c_handle = self.store.register(
                content=chunk_text,
                label=f"{parent_handle.label}_chunk_{len(chunk_handles) + 1}",
                metadata={
                    "source_handle": handle_id,
                    "chunk_index": len(chunk_handles),
                    "start_line": i + 1,
                    "end_line": min(len(lines), i + lines_per_chunk),
                },
                parent_handle_id=handle_id,
            )
            chunk_handles.append(c_handle)

        return chunk_handles
