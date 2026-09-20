"""Built-in Secure Credential Request Tool.

Allows agents to request missing sensitive credentials (API keys, tokens, passwords)
via an out-of-band user prompt. The secret is deposited directly into the
thread environment vault and never appears in the conversation history.
"""

from __future__ import annotations

from langchain.tools import tool

from alpha.security.credential_vault import get_credential_vault


@tool("request_secure_credential", parse_docstring=True)
def request_secure_credential(
    credential_key: str,
    description: str,
    reason: str,
    thread_id: str = "default",
) -> str:
    """Request a private credential or API token out-of-band from the operator.

    Use this tool whenever an operation requires an API key, database password, or auth token
    that is not already present in the execution environment. NEVER ask the user to type
    secrets directly in the chat window.

    Args:
        credential_key: The environment variable name (e.g. 'AWS_SECRET_ACCESS_KEY', 'GITHUB_TOKEN').
        description: A human-readable title describing the required credential.
        reason: Justification explaining why this credential is needed for the current task.
        thread_id: The active thread identifier.
    """
    vault = get_credential_vault()

    if vault.has_credential(thread_id, credential_key):
        return (
            f"Credential '{credential_key}' is already active in the secure environment vault. "
            f"You may proceed with execution using this environment variable."
        )

    vault.request_credential(
        thread_id=thread_id,
        key=credential_key,
        description=description,
        reason=reason,
    )

    return (
        f"[CREDENTIAL_REQUEST_PENDING]\n"
        f"An out-of-band secure credential prompt has been dispatched for '{credential_key}'.\n"
        f"Description: {description}\n"
        f"Reason: {reason}\n\n"
        f"The user will be prompted securely in the UI. The secret will be injected directly "
        f"into tool subprocesses without entering this conversation log."
    )
