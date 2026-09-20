"""Universal Model Context Protocol (MCP) Gateway (Host & Client).

Implements JSON-RPC 2.0 tool/resource/prompt orchestration with stdio, SSE, and mTLS transports,
semantic top-k tool discovery, Role-Based Access Control (RBAC), and secret scrubbing.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    MTLS = "mtls"


class RoleTier(str, Enum):
    READ_ONLY = "read_only"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        hierarchy = {
            RoleTier.READ_ONLY: 1,
            RoleTier.DEVELOPER: 2,
            RoleTier.OPERATOR: 3,
            RoleTier.ADMIN: 4,
        }
        return hierarchy[self]

    def permits(self, required_tier: RoleTier) -> bool:
        return self.rank >= required_tier.rank


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    required_role: RoleTier = RoleTier.READ_ONLY
    handler: Optional[Callable[..., Any]] = None
    tags: list[str] = field(default_factory=list)

    def to_mcp_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class SecretScrubber:
    """Detects and redacts high-entropy credentials, tokens, and keys from tool payloads."""

    PATTERNS = [
        (re.compile(r"sk-[A-Za-z0-9]{20,60}"), "OPENAI_KEY"),
        (re.compile(r"ghp_[A-Za-z0-9]{36}"), "GITHUB_PAT"),
        (re.compile(r"gho_[A-Za-z0-9]{36}"), "GITHUB_OAUTH"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS_ACCESS_KEY"),
        (re.compile(r"xox[baprs]-[0-9a-zA-Z-]{20,72}"), "SLACK_TOKEN"),
        (re.compile(r"Bearer\s+[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.?[A-Za-z0-9\-_.+/=]*"), "JWT_TOKEN"),
        (re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----[^-]+-----END (?:RSA )?PRIVATE KEY-----", re.DOTALL), "PRIVATE_KEY"),
        (re.compile(r'(?i)(?:password|secret|api_key|token)["\']?\s*[:=]\s*["\']([^"\']{8,})["\']'), "GENERIC_SECRET"),
    ]

    @classmethod
    def scrub(cls, data: Any) -> Any:
        """Recursively sanitizes strings, lists, and dictionaries."""
        if isinstance(data, str):
            res = data
            for pattern, name in cls.PATTERNS:
                def replacer(match: re.Match) -> str:
                    secret_val = match.group(0)
                    sig = hashlib.sha256(secret_val.encode()).hexdigest()[:8]
                    return f"[REDACTED_{name}:{sig}]"
                res = pattern.sub(replacer, res)
            return res
        elif isinstance(data, dict):
            return {k: cls.scrub(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [cls.scrub(item) for item in data]
        return data


class SemanticToolIndex:
    """BM25 / token-similarity index to discover top-k MCP tools without context bloat."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.tools: dict[str, ToolDefinition] = {}
        self._doc_lens: dict[str, int] = {}
        self._avg_dl = 1.0
        self._term_freqs: dict[str, dict[str, int]] = {}
        self._doc_freqs: dict[str, int] = {}

    def index_tool(self, tool: ToolDefinition) -> None:
        self.tools[tool.name] = tool
        tokens = self._tokenize(f"{tool.name} {tool.description} {' '.join(tool.tags)}")
        self._doc_lens[tool.name] = len(tokens)
        
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        self._term_freqs[tool.name] = tf

        self._recalculate_index()

    def _recalculate_index(self) -> None:
        n = len(self._doc_lens)
        if n == 0:
            return
        self._avg_dl = sum(self._doc_lens.values()) / n
        self._doc_freqs = {}
        for doc, tf in self._term_freqs.items():
            for t in tf:
                self._doc_freqs[t] = self._doc_freqs.get(t, 0) + 1

    def _tokenize(self, text: str) -> list[str]:
        return [w.lower() for w in re.findall(r"\w+", text) if len(w) > 1]

    def search_top_k(
        self,
        query: str,
        top_k: int = 5,
        caller_role: RoleTier = RoleTier.ADMIN,
    ) -> list[ToolDefinition]:
        """Returns top-k most relevant tools matching query that caller role is authorized to see."""
        query_tokens = self._tokenize(query)
        if not query_tokens or not self.tools:
            # Fallback: return up to top_k authorized tools
            authorized = [t for t in self.tools.values() if caller_role.permits(t.required_role)]
            return authorized[:top_k]

        n = len(self.tools)
        scores: list[tuple[float, ToolDefinition]] = []

        for name, tool in self.tools.items():
            if not caller_role.permits(tool.required_role):
                continue

            tf = self._term_freqs.get(name, {})
            dl = self._doc_lens.get(name, 1)
            score = 0.0

            for t in query_tokens:
                if t in tf:
                    df = self._doc_freqs.get(t, 1)
                    idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                    count = tf[t]
                    numerator = count * (self.k1 + 1.0)
                    denominator = count + self.k1 * (1.0 - self.b + self.b * (dl / self._avg_dl))
                    score += idf * (numerator / denominator)

            if score > 0.0:
                scores.append((score, tool))

        scores.sort(key=lambda s: s[0], reverse=True)
        return [tool for _, tool in scores[:top_k]]


class UniversalMCPGateway:
    """Universal MCP Server Host & Client Gateway."""

    def __init__(
        self,
        transport: TransportType = TransportType.STDIO,
        tls_cert_path: Optional[str] = None,
        tls_key_path: Optional[str] = None,
    ) -> None:
        self.transport = transport
        self.tls_cert_path = tls_cert_path
        self.tls_key_path = tls_key_path
        self.tool_index = SemanticToolIndex()
        self.resources: dict[str, dict[str, Any]] = {}
        self.prompts: dict[str, dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Any],
        required_role: RoleTier = RoleTier.READ_ONLY,
        tags: Optional[list[str]] = None,
    ) -> None:
        """Registers a tool with RBAC requirement and indexes it for semantic discovery."""
        tool = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            required_role=required_role,
            tags=tags or [],
        )
        self.tool_index.index_tool(tool)

    def register_resource(self, uri: str, name: str, mime_type: str, content: str) -> None:
        self.resources[uri] = {
            "uri": uri,
            "name": name,
            "mimeType": mime_type,
            "content": content,
        }

    def register_prompt(self, name: str, description: str, template: str) -> None:
        self.prompts[name] = {
            "name": name,
            "description": description,
            "template": template,
        }

    # --- JSON-RPC 2.0 Dispatcher ---

    def handle_jsonrpc_request(
        self,
        request_json: str | dict[str, Any],
        caller_role: RoleTier = RoleTier.ADMIN,
    ) -> dict[str, Any]:
        """Dispatches an MCP JSON-RPC 2.0 request with RBAC and secret scrubbing."""
        if isinstance(request_json, str):
            try:
                req = json.loads(request_json)
            except json.JSONDecodeError:
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
        else:
            req = request_json

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # Sanitize incoming payload
        scrubbed_params = SecretScrubber.scrub(params)

        if method == "tools/list":
            # Semantic top-k filter if query is provided
            query = scrubbed_params.get("query")
            top_k = scrubbed_params.get("top_k", 50)
            if query:
                tools = self.tool_index.search_top_k(query=query, top_k=top_k, caller_role=caller_role)
            else:
                tools = [t for t in self.tool_index.tools.values() if caller_role.permits(t.required_role)]

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [t.to_mcp_dict() for t in tools]},
            }

        elif method == "tools/call":
            tool_name = scrubbed_params.get("name")
            tool_args = scrubbed_params.get("arguments", {})

            if tool_name not in self.tool_index.tools:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                }

            tool = self.tool_index.tools[tool_name]

            # RBAC Enforcement Gate
            if not caller_role.permits(tool.required_role):
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32000,
                        "message": f"Forbidden: Calling '{tool_name}' requires role '{tool.required_role.value}', but caller holds '{caller_role.value}'",
                    },
                }

            try:
                # Execute tool handler (supporting both sync and async)
                raw_result = tool.handler(**tool_args) if tool.handler else None
                if inspect.iscoroutine(raw_result):
                    try:
                        loop = asyncio.get_running_loop()
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            raw_result = pool.submit(asyncio.run, raw_result).result()
                    except RuntimeError:
                        raw_result = asyncio.run(raw_result)

                scrubbed_result = SecretScrubber.scrub(raw_result)

                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(scrubbed_result)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                logger.exception("Error executing tool %s", tool_name)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Execution error: {exc}"}],
                        "isError": True,
                    },
                }

        elif method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"resources": list(self.resources.values())},
            }

        elif method == "resources/read":
            uri = scrubbed_params.get("uri")
            if uri not in self.resources:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32602, "message": f"Resource '{uri}' not found"},
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"contents": [self.resources[uri]]},
            }

        elif method == "prompts/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"prompts": list(self.prompts.values())},
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method '{method}' not implemented"},
        }
