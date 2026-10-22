"""`pixi.toml` -- the environment contract.

Not a source module, which is why it gets its own file rather than hiding inside one. It is
here because the project makes two claims about its environments that are invisible at runtime
and easy to break by accident, and one of them **was already broken** when this suite was
written:

* **The `pets` runtime environment is lean.** Contract 2.7 and `PHASE1-LOG.md` both state that
  Pillow is not a runtime dependency -- that is the argument for the whole `QImage`-only
  loading path. Pixi, however, folds the *default* feature (`[dependencies]`: napari,
  JupyterLab, Pillow) into every environment unless `no-default-feature = true` is set. It was
  not, so `pets` resolved to 277 packages and shipped Pillow. The declaration said one thing
  and the solved environment did another, with no symptom anywhere.

* **`pixi run pets` and `pixi run test` each resolve to exactly one environment.** A pixi
  feature carries its *tasks* as well as its dependencies, so composing `dev` out of
  `["pets", "dev"]` -- the obvious way to give the test environment PyQt6 -- silently makes
  bare `pixi run pets` ambiguous and the app un-launchable without `-e`.

Both are one-line mistakes with no local symptom, which is the definition of something worth a
test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def manifest() -> dict:
    return tomllib.loads((REPO_ROOT / "pixi.toml").read_text(encoding="utf-8"))


def test_regression_runtime_environments_exclude_the_art_qa_stack(manifest: dict) -> None:
    """`no-default-feature = true` on every environment `src/` runs in.

    Without it the default feature's napari + JupyterLab + Pillow are merged into `pets` and
    `dev`, and contract 2.7's "Pillow is not a runtime dependency" becomes false while still
    reading as true everywhere it is written down.
    """
    for name in ("pets", "dev"):
        env = manifest["environments"][name]
        assert env.get("no-default-feature") is True, (
            f"environment {name!r} inherits the default feature, so it contains napari, "
            "JupyterLab and Pillow")


def test_pillow_is_not_declared_in_any_runtime_feature(manifest: dict) -> None:
    """Contract 2.7, at the declaration level. The default feature may keep it; nothing else."""
    for feature in ("pets", "dev"):
        deps = {k.lower() for k in manifest["feature"][feature]["dependencies"]}
        assert "pillow" not in deps, f"Pillow is declared in feature {feature!r}"
    assert "pillow" in {k.lower() for k in manifest["dependencies"]}, (
        "Pillow should stay in the default feature -- tools/frame_qa.py uses it")


def test_dev_feature_mirrors_the_runtime_dependencies(manifest: dict) -> None:
    """The test environment must contain everything the app needs, at the same pins.

    `dev` mirrors `[feature.pets.dependencies]` instead of composing the `pets` feature (see
    the next test for why), so the two lists can drift. This is the guard that stops that:
    every runtime pin must appear in `dev`, spelled identically.
    """
    runtime = manifest["feature"]["pets"]["dependencies"]
    dev = manifest["feature"]["dev"]["dependencies"]
    missing = {name: spec for name, spec in runtime.items() if name not in dev}
    assert not missing, f"dev is missing runtime dependencies: {missing}"
    differing = {name: (spec, dev[name]) for name, spec in runtime.items()
                 if dev[name] != spec}
    assert not differing, f"dev pins differ from the runtime pins: {differing}"
    assert "pytest" in dev, "the dev feature exists to add pytest"
    assert "pytest" not in runtime, "pytest must not reach the runtime environment"


def test_every_task_resolves_to_exactly_one_environment(manifest: dict) -> None:
    """`pixi run pets` and `pixi run test` must not need `-e`.

    PHASE1-LOG.md records `pixi run pets` resolving unambiguously as a deliberate property.
    A task defined in a feature that two environments both include breaks it, and the error
    only shows up when someone actually types the command.
    """
    features: dict[str, set[str]] = {"default": set(manifest.get("tasks", {}))}
    for name, body in manifest.get("feature", {}).items():
        features[name] = set(body.get("tasks", {}))

    owners: dict[str, list[str]] = {}
    for env_name, env in manifest["environments"].items():
        included = list(env["features"])
        if not env.get("no-default-feature"):
            included.append("default")
        for feature in included:
            for task in features.get(feature, set()):
                owners.setdefault(task, []).append(env_name)

    ambiguous = {task: envs for task, envs in owners.items() if len(envs) > 1}
    assert not ambiguous, f"tasks reachable from more than one environment: {ambiguous}"
    assert set(owners) >= {"pets", "test"}, owners


def test_the_solve_group_is_shared(manifest: dict) -> None:
    """`pets` and `dev` resolve together, so the suite tests the versions the app ships."""
    groups = {name: manifest["environments"][name].get("solve-group")
              for name in ("pets", "dev")}
    assert groups["pets"] is not None
    assert groups["pets"] == groups["dev"], groups


def test_platforms_are_still_win_64_only(manifest: dict) -> None:
    """Windows-only is a documented scope decision, not an oversight.

    Adding osx-arm64/linux-64 forces the heavy default environment to re-solve for them too.
    If this ever fails on purpose, the macOS/Linux coverage note in tests/README.md needs
    revisiting at the same time.
    """
    assert manifest["workspace"]["platforms"] == ["win-64"]
