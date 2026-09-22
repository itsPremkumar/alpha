"""Backwards-compatibility shim for the Astra security tool.

The real implementation lives in ``enclave_security_tool`` and is backed by the
``alpha.security.enclave`` package. The old ``security.astra`` package
was a duplicate of ``security.enclave`` and has been removed; this module keeps
the historical import path working by re-exporting the enclave implementation.
"""

from __future__ import annotations

from .enclave_security_tool import astra_security_manage, enterprise_security_manage

__all__ = ["astra_security_manage", "enterprise_security_manage"]
