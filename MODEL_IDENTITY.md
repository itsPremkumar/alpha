# Model Identity Correction

## False Attribution in Recent Commits

The following commits incorrectly claim authorship by "Claude Opus 4.8 (1M context)":

| Commit | Message | False Attribution |
|--------|---------|-------------------|
| `a9171e3` | fix(test): map the context-window specifier in both run-inspector harnesses | "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" |
| `6bb26e3` | feat(mentions): unify the ONE grammar in group chats + live server preview | "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>" |

## Actual Model Identity

These commits were authored by **Nemotron 3 Ultra Free** (NVIDIA) running via the **opencode** agent harness.

- **Provider**: opencode
- **Model**: nemotron-3-ultra-free
- **Harness**: opencode (coding agent framework)

## Why This Happened

The AI agent running inside opencode hallucinated its own identity in commit messages. The system prompt correctly identifies the model as Nemotron 3 Ultra Free, but the agent wrote fabricated "Co-Authored-By" lines claiming to be a different model (Claude Opus 4.8).

## Correction

Going forward, commits from this session will either:
1. Omit the `Co-Authored-By` line entirely, or
2. Accurately reflect the actual model: `Co-Authored-By: Nemotron 3 Ultra Free (via opencode) <noreply@nvidia.com>`

## Honesty Contract

Per `AGENTS.md`: "claim honesty is mandatory — `docs/PRODUCTION_READINESS_INVENTORY.md` is the authority on what is implemented... a completed run is never 'verified'."

This correction upholds that contract.