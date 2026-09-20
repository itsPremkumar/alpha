"""Tests for Polyglot CST, Symbol Dependency Graph, and Personalized PageRank Repo Map."""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from alpha.coding.structural_intelligence.polyglot_cst import (
    PolyglotCSTParser,
    SymbolKind,
)
from alpha.coding.structural_intelligence.symbol_dependency_graph import (
    EdgeType,
    SymbolDependencyGraph,
)
from alpha.coding.structural_intelligence.repo_map import (
    PersonalizedPageRank,
    RepoMapGenerator,
)
from alpha.tools.builtins.code_agentic_core import generate_personalized_repo_map


def test_polyglot_python_ast_parsing():
    code = """
import os
from math import sqrt

class MatrixCalculator(BaseCalculator):
    '''Computes matrix transformations.'''
    def __init__(self, size: int) -> None:
        self.size = size

    async def compute_determinant(self, matrix: list[list[float]]) -> float:
        self.validate(matrix)
        return sqrt(4.0)
"""
    parser = PolyglotCSTParser()
    parsed = parser.parse_source("services/calc.py", code)

    assert parsed.language == "python"
    sym_names = {s.name: s for s in parsed.symbols}
    assert "MatrixCalculator" in sym_names
    assert sym_names["MatrixCalculator"].kind == SymbolKind.CLASS
    assert "compute_determinant" in sym_names
    assert sym_names["compute_determinant"].kind == SymbolKind.METHOD
    assert "async" in sym_names["compute_determinant"].modifiers

    # Check inheritance
    assert ("services/calc.py:MatrixCalculator", "BaseCalculator") in parsed.inherits_edges

    # Check imports
    assert ("services/calc.py", "os") in parsed.imports_edges
    assert ("services/calc.py", "math.sqrt") in parsed.imports_edges


def test_polyglot_typescript_parsing():
    ts_code = """
import { Injectable } from '@nestjs/common';
import axios from 'axios';

export interface UserPayload {
  id: string;
  email: string;
}

export class AuthService extends BaseAuth implements IAuthenticator {
  async authenticate(token: string): Promise<boolean> {
    const valid = verifyToken(token);
    return valid;
  }
}
"""
    parser = PolyglotCSTParser()
    parsed = parser.parse_source("src/auth/service.ts", ts_code)

    assert parsed.language == "typescript"
    sym_names = {s.name for s in parsed.symbols}
    assert "UserPayload" in sym_names
    assert "AuthService" in sym_names
    assert "authenticate" in sym_names
    assert ("src/auth/service.ts", "@nestjs/common") in parsed.imports_edges
    assert ("src/auth/service.ts", "axios") in parsed.imports_edges
    assert ("src/auth/service.ts:AuthService", "BaseAuth") in parsed.inherits_edges


def test_polyglot_go_and_rust_parsing():
    go_code = """
package worker

import (
    "fmt"
    "sync"
)

type WorkerPool struct {
    size int
}

func (w *WorkerPool) Dispatch(task string) bool {
    return true
}
"""
    parser = PolyglotCSTParser()
    go_parsed = parser.parse_source("pkg/worker/pool.go", go_code)
    assert go_parsed.language == "go"
    go_syms = {s.name for s in go_parsed.symbols}
    assert "WorkerPool" in go_syms
    assert "Dispatch" in go_syms

    rust_code = """
use std::collections::HashMap;

pub struct CacheEngine {
    store: HashMap<String, String>,
}

impl Storage for CacheEngine {
    pub fn get_key(&self, k: &str) -> Option<&String> {
        self.store.get(k)
    }
}
"""
    rust_parsed = parser.parse_source("src/cache.rs", rust_code)
    assert rust_parsed.language == "rust"
    rust_syms = {s.name for s in rust_parsed.symbols}
    assert "CacheEngine" in rust_syms
    assert "get_key" in rust_syms
    assert ("src/cache.rs:CacheEngine", "Storage") in rust_parsed.inherits_edges


def test_symbol_dependency_graph_traversal():
    graph = SymbolDependencyGraph()

    file_a = """
class BaseService:
    def execute(self):
        pass
"""
    file_b = """
from file_a import BaseService

class UserService(BaseService):
    def handle_user(self):
        self.execute()
"""
    graph.parse_and_add_file("file_a.py", file_a)
    graph.parse_and_add_file("file_b.py", file_b)

    # Invariants
    inheritors = graph.get_inheritors("file_a.py:BaseService")
    assert "file_b.py:UserService" in inheritors

    bases = graph.get_bases("file_b.py:UserService")
    assert "file_a.py:BaseService" in bases

    syms_a = graph.get_symbols_in_file("file_a.py")
    assert any(s.name == "BaseService" for s in syms_a)


def test_personalized_pagerank_biasing():
    graph = SymbolDependencyGraph()
    # Build a small multi-node graph
    code_auth = "def login(): pass\ndef logout(): pass"
    code_api = "def handle_request(): login()"
    code_db = "def query_user(): pass"

    graph.parse_and_add_file("auth.py", code_auth)
    graph.parse_and_add_file("api.py", code_api)
    graph.parse_and_add_file("db.py", code_db)

    ppr = PersonalizedPageRank(damping=0.85)

    # Bias toward auth.py:login
    seed_node = "auth.py:login"
    ranks = ppr.compute(graph, seed_nodes=[seed_node])

    assert seed_node in ranks
    # The seeded node must have a higher rank than an unrelated db query
    assert ranks[seed_node] > ranks.get("db.py:query_user", 0.0)


def test_compact_repo_map_generator():
    graph = SymbolDependencyGraph()
    for i in range(10):
        code = f"""
class Service{i}:
    '''Service number {i} documentation.'''
    def method_alpha(self, x: int) -> int:
        return x * 2

    def method_beta(self, y: str) -> bool:
        return True
"""
        graph.parse_and_add_file(f"module_{i}.py", code)

    gen = RepoMapGenerator(token_budget=300)
    repo_map = gen.generate_repo_map(graph, active_files=["module_0.py"], query="Service0 method_alpha")

    assert "# Repository Structural Map" in repo_map
    assert "module_0.py:" in repo_map
    assert "class Service0" in repo_map
    # Check that it packs within conservative character bounds (~300 tokens * 4 = 1200 chars)
    assert len(repo_map) <= 1800


def test_generate_personalized_repo_map_tool():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        (tmp_path / "calc.py").write_text("class Calc:\n    def add(self, a, b):\n        return a + b\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("from calc import Calc\ndef run():\n    c = Calc()\n", encoding="utf-8")

        output = generate_personalized_repo_map.invoke({
            "root_path": str(tmp_path),
            "active_files": "calc.py",
            "query": "Calc add",
            "token_budget": 500,
        })

        assert "calc.py:" in output
        assert "class Calc" in output
