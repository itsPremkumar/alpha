# Setup Guide

Quick setup instructions for Alpha.

## Configuration Setup

Alpha uses a YAML configuration file that should be placed in the **project root directory**.

### Steps

1. **Navigate to project root**:
   ```bash
   cd /path/to/agent-workspace
   ```

2. **Copy example configuration**:
   ```bash
   cp config.example.yaml config.yaml
   ```

3. **Edit configuration**:
   ```bash
   # Option A: Set environment variables (recommended)
   export OPENAI_API_KEY="your-key-here"

   # Optional: pin the project root when running from another directory
   export AGENT_WORKSPACE_PROJECT_ROOT="/path/to/agent-workspace"

   # Option B: Edit config.yaml directly
   vim config.yaml  # or your preferred editor
   ```

4. **Verify configuration**:
   ```bash
   cd backend
   python -c "from alpha.config import get_app_config; print('✓ Config loaded:', get_app_config().models[0].name)"
   ```

## Important Notes

- **Location**: `config.yaml` should be in `agent-workspace/` (project root)
- **Git**: `config.yaml` is automatically ignored by git (contains secrets)
- **Runtime root**: Set `AGENT_WORKSPACE_PROJECT_ROOT` if Alpha may start from outside the project root
- **Runtime data**: State defaults to `.agent-workspace` under the project root; set `AGENT_WORKSPACE_HOME` to move it
- **Skills**: Skills default to `skills/` under the project root; set `AGENT_WORKSPACE_SKILLS_PATH` or `skills.path` to move them

## Configuration File Locations

The backend searches for `config.yaml` in this order:

1. Explicit `config_path` argument from code
2. `AGENT_WORKSPACE_CONFIG_PATH` environment variable (if set)
3. `config.yaml` under `AGENT_WORKSPACE_PROJECT_ROOT`, or the current working directory when `AGENT_WORKSPACE_PROJECT_ROOT` is unset
4. Legacy backend/repository-root locations for monorepo compatibility

**Recommended**: Place `config.yaml` in project root (`agent-workspace/config.yaml`).

## Sandbox Setup (Optional but Recommended)

If you plan to use Docker/Container-based sandbox (configured in `config.yaml` under `sandbox.use: alpha.community.aio_sandbox:AioSandboxProvider`), it's highly recommended to pre-pull the container image:

```bash
# From project root
make setup-sandbox
```

**Why pre-pull?**
- The sandbox image (~500MB+) is pulled on first use, causing a long wait
- Pre-pulling provides clear progress indication
- Avoids confusion when first using the agent

If you skip this step, the image will be automatically pulled on first agent execution, which may take several minutes depending on your network speed.

## Local Voice Setup (Optional)

For fully local real-time conversation, install the optional speech runtime and pinned model assets from the repository root:

```bash
make voice-setup
make voice-verify
```

This uses faster-whisper for microphone transcription and Piper for response speech. It requires no cloud speech API key; only the configured LLM may incur a cost. See [Real-Time Voice Conversation](../../docs/VOICE_CONVERSATION.md) for configuration, Docker, privacy, and troubleshooting.

## Local Laya System One Setup (Optional)

Laya is an optional, open-weights System One decision model. It is not an LLM entry
under `models[]`; Alpha reaches it through the provider-neutral `system_one` client.
The helper creates an isolated environment and project-local checkpoint cache:

```bash
# From the repository root
python backend/scripts/system_one_laya_setup.py setup
python backend/scripts/system_one_laya_setup.py serve
```

Use `--model multilingual`, `--model typed-decisions`, or `--model router` to select
another checkpoint. Set `system_one.provider: laya` in `config.yaml`; keep
`shadow_mode: true` while measuring. See [System One](../../docs/SYSTEM_ONE.md) for
the wire contract, authentication, and fallback rules.

## Troubleshooting

### Config file not found

```bash
# Check where the backend is looking
cd agent-workspace/backend
python -c "from alpha.config.app_config import AppConfig; print(AppConfig.resolve_config_path())"
```

If it can't find the config:
1. Ensure you've copied `config.example.yaml` to `config.yaml`
2. Verify you're in the project root, or set `AGENT_WORKSPACE_PROJECT_ROOT`
3. Check the file exists: `ls -la config.yaml`

### Permission denied

```bash
chmod 600 ../config.yaml  # Protect sensitive configuration
```

## See Also

- [Configuration Guide](CONFIGURATION.md) - Detailed configuration options
- [Real-Time Voice Conversation](../../docs/VOICE_CONVERSATION.md) - Free local STT/TTS setup and operation
- [Architecture Overview](../CLAUDE.md) - System architecture
