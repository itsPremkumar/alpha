"""The shipped default model must be one a fresh install can actually use.

`config.example.yaml` is the template ``make config`` copies to ``config.yaml``,
so its *effective* default is the model every new user gets without configuring
anything. That default is not only the chat model: the memory subsystem resolves
its extraction LLM from the same place. ``memory.backend_config.model: null``
does not mean "no model" -- ``DeerMem.from_config`` treats an empty model as
"ask the host", and the host answers with ``create_chat_model(name=None)``,
which resolves ``AppConfig.default_model_name``. So a default that needs an
account makes the whole memory subsystem dead on arrival.

That is not hypothetical. The template shipped ``default_model`` commented out
with ``union-alpha`` as the only ``models[]`` entry, so a fresh install resolved
its default (and therefore its memory extractor) to an OpenRouter entry needing
a funded key. Measured against the live provider that entry answers HTTP 402,
and with no key at all OpenRouter's Clerk answers 401. Every memory update then
failed on every run, and the only evidence was a log line while the run still
reported ``status: success`` and ``GET /api/memory`` still answered 200 with an
empty ``facts`` list.

The load-time contract is already fail-closed elsewhere (``default_model`` must
name an entry in ``models[]``). These tests pin the part nothing enforced: that
the entry it resolves to needs no credential.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from alpha.config.app_config import AppConfig

EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"

# The one provider class that can reach a no-auth endpoint. `ChatOpenAI` always
# sends an `Authorization` header, and the working keyless gateways reject a
# bogus one rather than ignoring it.
KEYLESS_USE = "alpha.models.free_router:ChatFreeLLM"


def _load_template(tmp_path: Path) -> AppConfig:
    """Load the shipped template exactly as a fresh install would."""
    target = tmp_path / "config.example.yaml"
    shutil.copyfile(EXAMPLE, target)
    return AppConfig.from_file(str(target))


def _entry(cfg: AppConfig, name: str):
    for model in cfg.models:
        if model.name == name:
            return model
    raise AssertionError(f"config.example.yaml no longer declares a model named {name!r}")


def test_shipped_template_declares_a_keyless_default_model(tmp_path):
    """The effective default of a fresh install must need no account."""
    cfg = _load_template(tmp_path)

    default_name = cfg.default_model_name
    assert default_name, "config.example.yaml must resolve to a default model name"

    entry = _entry(cfg, default_name)
    assert entry.use == KEYLESS_USE, (
        f"the shipped default_model is {default_name!r} built with {entry.use!r}. "
        "A default that needs an API key is not a default a fresh install can use, "
        "and it is the model the memory subsystem extracts with "
        "(memory.backend_config.model: null -> create_chat_model(name=None)). "
        f"Point it at {KEYLESS_USE}."
    )
    assert (entry.model_extra or {}).get("api_key") is None, f"the shipped default_model {default_name!r} declares an api_key. A keyless default must not carry one, or it is credentialed in everything but name."


def test_shipped_template_default_pins_a_free_route(tmp_path):
    """`free:<gateway>:<model>` is load-bearing; a bare model id is not a route."""
    cfg = _load_template(tmp_path)
    entry = _entry(cfg, cfg.default_model_name)

    assert str(entry.model).startswith("free:"), (
        f"the shipped default_model is pinned to {entry.model!r}. The free router reads a string with no `free:` prefix as a plain model id and then honestly reports that no provider offers it, so the default would have no route at all."
    )


def test_union_alpha_stays_configured_and_first(tmp_path):
    """Moving the default must not delete the operator's paid option.

    `union-alpha` is the Alpha-side name the docs and the OpenRouter slug tests
    are pinned to; it stays declared and stays first in `models[]`.
    """
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))

    assert [m["name"] for m in raw["models"]][0] == "union-alpha", "the first model in config.example.yaml is union-alpha by documented contract; changing the default must not reorder the list"
    cfg = _load_template(tmp_path)
    assert _entry(cfg, "union-alpha").model == "unbiased/pareto"


def test_shipped_template_memory_extraction_inherits_that_default(tmp_path):
    """Tie the two together: the memory extractor follows the shipped default.

    Asserted as a composition rather than a restatement. ``DeerMem`` asks the
    host for a model exactly when ``memory.backend_config.model`` is empty, and
    the host resolves it as ``create_chat_model(name=None)`` -- the app default.
    So the credential-free default above is also what decides whether passive
    memory extraction can run at all.
    """
    from alpha.agents.memory.manager import _host_default_llm

    cfg = _load_template(tmp_path)
    assert (cfg.memory.backend_config or {}).get("model") is None, (
        "config.example.yaml must leave memory.backend_config.model empty so extraction inherits the app default; an explicit entry here would decouple memory from the default this module pins"
    )

    requested: list[str | None] = []

    def _fake_create_chat_model(*, name=None, **kwargs):
        requested.append(name)
        return object()

    monkey = pytest.MonkeyPatch()
    try:
        import alpha.models as models_module

        monkey.setattr(models_module, "create_chat_model", _fake_create_chat_model)
        llm = _host_default_llm()
    finally:
        monkey.undo()

    assert llm is not None, "DeerMem must be able to resolve a memory-update model with the shipped config"
    assert requested == [None], f"memory extraction must resolve the app default, not a named model (asked for {requested!r})"

    entry = _entry(cfg, cfg.default_model_name)
    assert entry.use == KEYLESS_USE, "the app default memory extraction inherits must be the keyless router"
