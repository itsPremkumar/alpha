"""Install, pre-download, and serve Convai Innovations' Laya System One model.

Laya is intentionally isolated from Alpha's normal Python environment. It brings
PyTorch and Transformers, while Alpha's HTTP client only needs the stable
Jev-compatible ``POST /v1/systemone`` protocol. Keeping the runtime under the
ignored ``.agent-workspace/laya`` directory prevents a later ``uv sync`` from
removing it and avoids expanding Alpha's core lockfile for an optional model.

Typical setup (English checkpoint, CPU inference, loopback-only server)::

    python scripts/system_one_laya_setup.py setup
    python scripts/system_one_laya_setup.py serve

Other useful commands::

    python scripts/system_one_laya_setup.py setup --model router
    python scripts/system_one_laya_setup.py status
    python scripts/system_one_laya_setup.py download --model multilingual

The setup pins the Laya package version, but model weights are immutable Hub
repositories rather than Python packages. Re-running ``download`` is safe and
reuses ``.agent-workspace/laya/hf-cache``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAYA_VERSION = "0.3.20"
LAYA_REPO = "convaiinnovations/laya"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_DEVICE = "auto"
DEFAULT_CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
DEFAULT_CUDA_TORCH_INDEX = "https://download.pytorch.org/whl/cu126"
TORCH_VERSION = "2.14.0"

MODEL_CHOICES = ("english", "multilingual", "typed-decisions", "router")
MODEL_SPECS: dict[str, tuple[str, str | None]] = {
    "english": (LAYA_REPO, None),
    "multilingual": (LAYA_REPO, "multilingual"),
    "typed-decisions": (LAYA_REPO, "typed-decisions"),
}


def resolve_torch_index(device: str) -> str | None:
    """Choose a wheel index for auto/explicit device setup.

    ``auto`` prefers CUDA when the host exposes ``nvidia-smi`` and otherwise
    selects the CPU wheel. Callers can always override this with
    ``--torch-index-url`` or choose ``--device cpu/cuda`` explicitly.
    """
    if device == "cuda":
        return DEFAULT_CUDA_TORCH_INDEX
    if device == "cpu":
        return DEFAULT_CPU_TORCH_INDEX
    if device != "auto":
        return None
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        probe = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, check=False)
        if probe.returncode == 0 and probe.stdout.strip():
            return DEFAULT_CUDA_TORCH_INDEX
    return DEFAULT_CPU_TORCH_INDEX


def default_home() -> Path:
    """Return the ignored project-local Laya runtime directory."""
    configured = os.getenv("AGENT_WORKSPACE_HOME", "").strip()
    root = Path(configured).expanduser() if configured else PROJECT_ROOT / ".agent-workspace"
    return root.resolve() / "laya"


def venv_python(home: Path) -> Path:
    """Return the interpreter path for the isolated Laya virtual environment."""
    if os.name == "nt":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def expand_models(selected: str) -> tuple[list[str], str]:
    """Expand a model selector to server model names and Alpha's model value.

    ``router`` asks Laya to preload the English and multilingual checkpoints and
    leaves Alpha's model value empty, which makes the server auto-route by
    language. A single checkpoint keeps memory use bounded and is therefore the
    setup default.
    """
    if selected == "router":
        return ["english", "multilingual"], ""
    if selected not in MODEL_SPECS:
        raise ValueError(f"unknown Laya model {selected!r}; choose one of {', '.join(MODEL_CHOICES)}")
    return [selected], selected


def _load_laya_dotenv(env: dict[str, str]) -> None:
    """Load only Laya/Hugging Face secrets from the project .env if present.

    The main Alpha app already uses ``python-dotenv``; this isolated helper is
    intentionally stdlib-only, so it reads just the two relevant keys and never
    overrides an explicitly exported environment variable.
    """
    dotenv = PROJECT_ROOT / ".env"
    if not dotenv.is_file():
        return
    try:
        lines = dotenv.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in {"LAYA_API_KEY", "HF_TOKEN"} or env.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            env[key] = value


def runtime_environment(home: Path, *, models: list[str], device: str, host: str, port: int) -> dict[str, str]:
    """Build the environment shared by download and server processes."""
    env = os.environ.copy()
    _load_laya_dotenv(env)
    # The isolated model process does not need Alpha's hosted-provider keys.
    # Strip them before launching the server/download helper so a child process
    # cannot accidentally receive unrelated credentials.
    for key in list(env):
        if key.endswith("_API_KEY") and key not in {"LAYA_API_KEY"}:
            env.pop(key, None)
    if device == "auto":
        # Laya's Agent uses ``None`` to select CUDA/CPU itself; the literal
        # string "auto" is not a valid torch device.
        env.pop("LAYA_DEVICE", None)
    else:
        env["LAYA_DEVICE"] = device
    env.update(
        {
            "HF_HOME": str((home / "hf-cache").resolve()),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "LAYA_HOST": host,
            "LAYA_PORT": str(port),
            "LAYA_MODELS": ",".join(models),
            "LAYA_PRELOAD": "1",
            "LAYA_AUTO_TASK": "0",
            # Avoid TensorFlow import probes in the transformers ecosystem. The
            # upstream Laya README calls this out for Windows model construction.
            "USE_TF": "0",
        }
    )
    return env


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, check=True, env=env)


def install_runtime(home: Path, *, uv: str, torch_index_url: str | None) -> Path:
    """Create the isolated environment and install the pinned server package."""
    home.mkdir(parents=True, exist_ok=True)
    python = venv_python(home)
    if not python.exists():
        _run([uv, "venv", "--python", sys.executable, str(home / ".venv")])
    if torch_index_url:
        # Pin the local build explicitly: uv otherwise treats an already
        # installed CPU wheel as satisfying a request for CUDA and silently
        # leaves the process without CUDA support. The index is selected by
        # ``--device``/``auto`` before this function is called.
        index_name = torch_index_url.rstrip("/").rsplit("/", 1)[-1]
        local_suffix = index_name if index_name.startswith("cu") else "cpu"
        torch_spec = f"torch=={TORCH_VERSION}+{local_suffix}"
        _run([uv, "pip", "install", "--reinstall", "--python", str(python), torch_spec, "--index-url", torch_index_url])
    _run([uv, "pip", "install", "--python", str(python), f"laya[serve]=={LAYA_VERSION}"])
    return python


def download_checkpoints(
    home: Path,
    models: list[str],
    *,
    device: str = DEFAULT_DEVICE,
    python: Path | None = None,
) -> None:
    """Download and validate selected checkpoints through Laya's own loader."""
    interpreter = python or venv_python(home)
    if not interpreter.exists():
        raise FileNotFoundError(f"Laya environment is missing: {interpreter}. Run the setup command first.")
    specs: list[dict[str, str | None]] = []
    for model in models:
        if model not in MODEL_SPECS:
            raise ValueError(f"router-only model cannot be downloaded directly: {model!r}")
        repo, subfolder = MODEL_SPECS[model]
        specs.append({"repo": repo, "subfolder": subfolder})
    device_arg = None if device == "auto" else device
    code = f"""
import json
import laya

specs = json.loads({json.dumps(specs)!r})
for spec in specs:
    print(f"Loading {{spec['repo']}}" + (f"/{{spec['subfolder']}}" if spec.get('subfolder') else ""), flush=True)
    with laya.load(spec["repo"], subfolder=spec.get("subfolder"), device={device_arg!r}) as agent:
        print(f"Downloaded and loaded {{agent.model_id}} on {{agent.device}}", flush=True)
"""
    env = runtime_environment(home, models=models, device=device, host=DEFAULT_HOST, port=DEFAULT_PORT)
    _run([str(interpreter), "-c", code], env=env)


def save_runtime_config(
    home: Path,
    *,
    models: list[str],
    alpha_model: str,
    device: str,
    host: str,
    port: int,
    python: Path,
) -> Path:
    """Persist non-secret launch defaults inside the ignored runtime directory."""
    path = home / "runtime.json"
    payload: dict[str, Any] = {
        "package": "laya",
        "package_version": LAYA_VERSION,
        "models": models,
        "alpha_model": alpha_model,
        "device": device,
        "host": host,
        "port": port,
        "python": str(python),
        "base_url": f"http://{host}:{port}",
        "api_base_url": f"http://{host}:{port}/v1",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_runtime_config(home: Path) -> dict[str, Any]:
    path = home / "runtime.json"
    if not path.is_file():
        raise FileNotFoundError(f"Laya runtime is not configured: {path}. Run the setup command first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("package") != "laya":
        raise ValueError(f"unexpected Laya runtime descriptor: {path}")
    return data


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def show_status(home: Path, data: dict[str, Any] | None = None) -> int:
    """Print installation, cache, and server health information."""
    if data is None:
        try:
            data = load_runtime_config(home)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            print(f"Laya setup: incomplete ({exc})")
            return 1

    python = Path(str(data.get("python", venv_python(home))))
    package_version = "not installed"
    if python.exists():
        completed = subprocess.run(
            [str(python), "-I", "-c", "import laya; print(laya.__version__)"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            package_version = completed.stdout.strip()
        else:
            package_version = f"import failed: {completed.stderr.strip() or completed.returncode}"

    base_url = str(data.get("base_url", "http://127.0.0.1:8000"))
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=2.0) as response:  # noqa: S310 - operator-configured loopback URL
            health = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        health = {"status": "unreachable", "detail": str(exc)}

    cache_bytes = _directory_size(home / "hf-cache")
    print(
        json.dumps(
            {
                "runtime": str(home),
                "package_version": package_version,
                "expected_package_version": LAYA_VERSION,
                "models": data.get("models", []),
                "alpha_model": data.get("alpha_model", ""),
                "device": data.get("device", DEFAULT_DEVICE),
                "base_url": base_url,
                "cache_mb": round(cache_bytes / (1024 * 1024), 2),
                "health": health,
            },
            indent=2,
        )
    )
    return 0 if health.get("status") == "ok" else 2


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", type=Path, default=default_home(), help="Project-local Laya runtime directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="install Laya, pre-download weights, and save launch defaults")
    _add_common(setup)
    setup.add_argument("--model", choices=MODEL_CHOICES, default="english", help="english (default), multilingual, typed-decisions, or router")
    setup.add_argument("--device", choices=("cpu", "cuda", "mps", "auto"), default=DEFAULT_DEVICE)
    setup.add_argument("--host", default=DEFAULT_HOST, help="server bind address; keep loopback for unauthenticated use")
    setup.add_argument("--port", type=int, default=DEFAULT_PORT)
    setup.add_argument("--torch-index-url", default=None, help="PyTorch wheel index; default selects CPU or CUDA cu126 from --device; pass an empty string to use PyPI")
    setup.add_argument("--skip-download", action="store_true", help="install the package without downloading weights")

    download = sub.add_parser("download", help="download selected checkpoints into the project-local Hugging Face cache")
    _add_common(download)
    download.add_argument("--model", choices=("english", "multilingual", "typed-decisions"), required=True)
    download.add_argument("--device", choices=("cpu", "cuda", "mps", "auto"), default=DEFAULT_DEVICE)

    serve = sub.add_parser("serve", help="run the Jev-compatible Laya HTTP server in the foreground")
    _add_common(serve)

    status = sub.add_parser("status", help="show package, checkpoint cache, and server health")
    _add_common(status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    home = args.home.expanduser().resolve()

    if args.command == "setup":
        uv = shutil.which("uv") or shutil.which("uv.exe")
        if not uv:
            raise SystemExit("uv is required to create the isolated Laya environment")
        models, alpha_model = expand_models(args.model)
        if args.torch_index_url is None:
            torch_index_url = resolve_torch_index(args.device)
        else:
            torch_index_url = args.torch_index_url or None
        python = install_runtime(home, uv=uv, torch_index_url=torch_index_url)
        if not args.skip_download:
            direct_models = [model for model in models if model in MODEL_SPECS]
            download_checkpoints(home, direct_models, device=args.device, python=python)
        path = save_runtime_config(
            home,
            models=models,
            alpha_model=alpha_model,
            device=args.device,
            host=args.host,
            port=args.port,
            python=python,
        )
        print(f"Laya setup saved to {path}")
        print(f"Configure system_one.provider: laya, base_url: http://{args.host}:{args.port}, and shadow_mode: true")
        if alpha_model:
            print(f'Set system_one.model: {alpha_model} (or use "" to enable language routing).')
        else:
            print("Leave system_one.model empty for Laya language routing.")
        if not args.host.startswith("127.0.0.1") and not args.host.startswith("localhost"):
            print("WARNING: non-loopback Laya servers should set LAYA_API_KEY on both sides.")
        return 0

    if args.command == "download":
        download_checkpoints(home, [args.model], device=args.device)
        return 0

    if args.command == "serve":
        data = load_runtime_config(home)
        models = [str(item) for item in data.get("models", [])]
        device = str(data.get("device", DEFAULT_DEVICE))
        host = str(data.get("host", DEFAULT_HOST))
        port = int(data.get("port", DEFAULT_PORT))
        env = runtime_environment(home, models=models, device=device, host=host, port=port)
        command = [
            str(data.get("python", venv_python(home))),
            str(PROJECT_ROOT / "backend" / "scripts" / "system_one_laya_server.py"),
        ]
        print(f"Starting Laya on http://{host}:{port} with models={models} device={device}", flush=True)
        try:
            return subprocess.run(command, check=True, env=env).returncode
        except KeyboardInterrupt:
            return 0

    if args.command == "status":
        return show_status(home)

    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
