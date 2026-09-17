"""Tests for wiki vault + mem0oss stub + fullmemory composite ($0 LTM)."""

import tempfile
from pathlib import Path

from agent_workspace.agents.memory.backends.fullmemory.config import FullMemoryConfig
from agent_workspace.agents.memory.backends.mem0oss.config import Mem0OssConfig
from agent_workspace.agents.memory.backends.mem0oss.mem0oss_manager import Mem0OssManager
from agent_workspace.memory.wiki_vault import WikiVault, ensure_user_vault


def test_wiki_vault_init_digest_search_and_episodes():
    with tempfile.TemporaryDirectory() as tmp:
        vault = WikiVault(Path(tmp) / "vault")
        created = vault.init()
        assert len(created) >= 11  # purpose + 3 system + 7 wiki
        assert (vault.root / "purpose.md").exists()
        assert "Goals" in vault.get_purpose()
        digest = vault.digest()
        assert "Persona" in digest or "Hot" in digest
        vault.append_episode("User prefers TypeScript", session_id="s1")
        assert (vault.root / "episodes").exists()
        hits = vault.search("TypeScript preferences")
        assert isinstance(hits, list)
        vault.append_log("fixed login bug")
        assert vault.commit("test commit") in (True, False)  # git optional


def test_ensure_user_vault_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        alice = ensure_user_vault(tmp, "alice@example.com")
        bob = ensure_user_vault(tmp, "bob")
        assert alice.root != bob.root
        assert (alice.root / "wiki" / "hot.md").exists()


def test_mem0oss_config_defaults_tolerate():
    config = Mem0OssConfig.from_backend_config({})
    assert config.llm_provider == "ollama"
    assert config.startup_policy == "tolerate"


def test_mem0oss_stub_add_search_context_no_keys():
    mgr = Mem0OssManager(backend_config={"startup_policy": "tolerate"}, mode="middleware")
    mgr.add("th_1", [{"role": "user", "content": "I prefer aisle seats"}], user_id="u1")
    mgr.add("th_1", [{"role": "user", "content": "I prefer aisle seats"}], user_id="u1")  # dedup
    context = mgr.get_context("u1")
    assert "aisle" in context
    hits = mgr.search("aisle seats", user_id="u1")
    assert hits and "aisle" in hits[0]["content"]
    assert mgr.warm() in (True, None)
    assert mgr.shutdown_flush(1.0) is True


def test_fullmemory_config_rejects_unknown_keys():
    try:
        FullMemoryConfig.from_backend_config({"bogus_key": 1})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown key")


def test_fullmemory_composite_no_keys():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        mgr_kwargs = {"backend_config": {"storage_path": tmp}, "mode": "middleware"}
        from agent_workspace.agents.memory.backends.fullmemory.fullmemory_manager import FullMemoryManager

        mgr = FullMemoryManager(**mgr_kwargs)
        try:
            mgr.add("th_1", [{"role": "user", "content": "Remember: deploy on Fridays is banned"}], user_id="u9")
            context = mgr.get_context("u9")
            assert isinstance(context, str) and len(context) > 0  # deermem and/or stub facts
            hits = mgr.search("deploy Fridays", user_id="u9")
            assert isinstance(hits, list)
            data = mgr.get_memory(user_id="u9")
            assert "deermem" in data or "deermem_error" in data
        finally:
            try:
                mgr.shutdown_flush(5.0)
            except Exception:
                pass


def test_wiki_sources_traceability():
    with tempfile.TemporaryDirectory() as tmp:
        vault = WikiVault(Path(tmp) / "vault")
        vault.init()
        page = vault.root / "wiki" / "concepts" / "auth.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("---\ndescription: Auth notes.\n---\n\n# Auth\n\nJWT.\n", encoding="utf-8")
        assert vault.add_source("wiki/concepts/auth.md", "stripe-api.pdf") is True
        assert vault.add_source("wiki/concepts/auth.md", "stripe-api.pdf") is True  # idempotent
        assert vault.get_sources("wiki/concepts/auth.md") == ["stripe-api.pdf"]
        assert vault.add_source("wiki/concepts/auth.md", "") is False


def test_wiki_ingest_cache_skips_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        vault = WikiVault(Path(tmp) / "vault")
        vault.init()
        assert vault.should_ingest("doc.pdf", "v1 content") is True
        vault.mark_ingested("doc.pdf", "v1 content")
        assert vault.should_ingest("doc.pdf", "v1 content") is False
        assert vault.should_ingest("doc.pdf", "v2 content") is True


def test_wiki_cascade_delete_prunes_and_cleans():
    with tempfile.TemporaryDirectory() as tmp:
        vault = WikiVault(Path(tmp) / "vault")
        vault.init()
        summary = vault.root / "wiki" / "sources" / "old-api.md"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text("---\ndescription: Old API.\nsources:\n  - old-api\n---\n\n# Old\n", encoding="utf-8")
        shared = vault.root / "wiki" / "concepts" / "auth.md"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(
            "---\ndescription: Auth.\nsources:\n  - old-api\n  - new-api\n---\n\n# Auth\n\nSee [[old-api]].\n",
            encoding="utf-8",
        )
        report = vault.delete_source("old-api")
        assert "wiki/sources/old-api.md" in report["removed_pages"]
        assert "wiki/concepts/auth.md" in report["pruned_pages"]
        assert vault.get_sources("wiki/concepts/auth.md") == ["new-api"]
        assert "[[old-api]]" not in (vault.root / "wiki" / "concepts" / "auth.md").read_text(encoding="utf-8")
        assert vault.delete_source("") == {"removed_pages": [], "pruned_pages": [], "links_cleaned": 0}


def test_wiki_review_queue_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        vault = WikiVault(Path(tmp) / "vault")
        vault.init()
        rid = vault.flag_review("Research OAuth gaps", "deep_research", queries=["oauth mfa"], detail="sparse area")
        assert vault.flag_review("Skip this", "skip") != rid
        assert len(vault.list_reviews()) == 2
        assert vault.resolve_review(rid, action_taken="ran research") is True
        assert len(vault.list_reviews(status="unresolved")) == 1
        assert len(vault.list_reviews(status="resolved")) == 1
        assert len(vault.list_reviews(status="all")) == 2
        assert vault.resolve_review("missing-id") is False
        try:
            vault.flag_review("Bad action", "delete_everything")
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for unconstrained action")
