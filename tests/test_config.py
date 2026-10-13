"""`src/config.py` -- tunables, and the JSON settings round-trip.

Pure Python; no `QApplication` needed, which is the property `config.py`'s docstring claims
("deliberately Qt-free below the SETTINGS PERSISTENCE banner") and this module quietly proves
by importing nothing from PyQt6.

The interesting half is `load_settings`: it is the one piece of the app that reads a file a
human may have hand-edited, and its contract is that it *never raises* -- every malformed
input degrades to defaults plus a log line. Corruption is the normal case here, not the
exotic one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src import config
from src.core.state_machine import STATES


# --- settings round-trip ---------------------------------------------------------------------


def test_settings_round_trip_through_disk() -> None:
    """save_settings -> load_settings returns an equal object, field for field."""
    original = config.Settings(
        pos_x=100, pos_y=200, screen="\\\\.\\DISPLAY1",
        click_through=True, always_on_top=False,
        card_visible=True, pet_visible=False, scale=2,
    )
    assert config.save_settings(original) is True
    assert config.load_settings() == original


def test_save_settings_writes_valid_json_and_verifies_by_read_back() -> None:
    """The file on disk parses, and is what `to_dict()` said it would be.

    CLAUDE.md's "validate generated files with the format's own tooling" rule, applied to the
    one file this app writes. `save_settings` already does the read-back internally; this
    asserts the artefact independently, with `json` rather than with the writer.
    """
    settings = config.Settings(pos_x=7, pos_y=9, scale=3)
    assert config.save_settings(settings) is True
    raw = json.loads(config.settings_path().read_text(encoding="utf-8"))
    assert raw == settings.to_dict()
    assert raw["schema_version"] == config.SETTINGS_SCHEMA_VERSION


def test_scale_is_clamped_into_range() -> None:
    """`scale` is an int clamped to 1..MAX_RENDER_SCALE (contract 2.5).

    Fractional scaling of pixel art is the exact thing the design avoids, so the field is an
    int; the clamp is what stops a hand-edited `"scale": 9` from producing a 1728x1872 pet.
    """
    assert config.Settings(scale=9).clamped().scale == config.MAX_RENDER_SCALE
    assert config.Settings(scale=0).clamped().scale == 1
    assert config.Settings(scale=-4).clamped().scale == 1
    assert config.Settings(scale=2).clamped().scale == 2


# --- tolerant parsing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("truncated object", "{not json"),
        ("empty file", ""),
        ("whitespace only", "   \n\t "),
        ("a JSON array, not an object", "[1, 2, 3]"),
        ("a bare JSON string", '"hello"'),
        ("HTML someone pasted in", "<!doctype html>"),
    ],
)
def test_corrupt_settings_file_yields_defaults_and_never_raises(label: str, content: str) -> None:
    """A settings file the user has broken must degrade to defaults, silently and safely.

    Includes the deliberate non-destruction guarantee: the bad file is *left on disk*. Renaming
    or deleting it would be a surprise destruction of something the user may have been editing.
    """
    path = config.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    assert config.load_settings() == config.Settings(), label
    assert path.exists(), "a corrupt settings file must not be deleted or renamed"


def test_settings_file_with_a_utf8_bom_still_parses() -> None:
    """Notepad writes a BOM. `load_settings` reads with `utf-8-sig`, so that is not corruption."""
    settings = config.Settings(pos_x=11, pos_y=22)
    path = config.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("﻿".encode("utf-8")
                     + json.dumps(settings.to_dict()).encode("utf-8"))
    assert config.load_settings() == settings


def test_missing_settings_file_yields_defaults(tmp_path: Path) -> None:
    """First run: no file at all is the normal path, not an error path."""
    assert config.load_settings(tmp_path / "nope.json") == config.Settings()


def test_from_dict_ignores_unknown_keys_and_coerces_junk() -> None:
    """Forward compatible in one direction, defensive in the other.

    An unknown key means a *newer* build wrote the file -- ignoring it lets an older build keep
    running. A key of the wrong type means the file was hand-edited -- coercing to the default
    is better than raising inside startup.
    """
    parsed = config.Settings.from_dict({
        "schema_version": 1,
        "pos_x": "not a number",       # junk -> None
        "pos_y": 640.0,                # float -> int
        "screen": 12345,               # junk -> None
        "click_through": "true",       # the QSettings trap: a truthy *string* -> default False
        "scale": 2,
        "a_key_from_the_future": {"nested": True},
    })
    assert parsed.pos_x is None
    assert parsed.pos_y == 640
    assert parsed.screen is None
    assert parsed.click_through is False, 'the string "true" must not read as True'
    assert parsed.scale == 2


def test_schema_version_mismatch_does_not_raise() -> None:
    """A file from a future schema is read on a best-effort basis, with a warning."""
    parsed = config.Settings.from_dict({"schema_version": 99, "pos_x": 5})
    assert parsed.pos_x == 5


# --- invariants over the tunables -------------------------------------------------------------


def test_every_state_has_card_copy() -> None:
    """`STATE_PRESENTATION` covers `STATES` exactly -- no orphan copy, no state without copy.

    A state with no entry falls back to `DEFAULT_PRESENTATION`, which silently shows the wrong
    words rather than failing, so this is the only place the omission can be caught.

    NOTE, and this is a correction to the scratch script this test replaces: `check_config.py`
    asserted `len(STATE_PRESENTATION) == 9`. That number was stale the moment `working-together`
    was added as a tenth state. Pinning a count is the wrong assertion; the set relationship is
    the thing that actually matters and it cannot go stale.
    """
    assert set(config.STATE_PRESENTATION) == set(STATES)


def test_schmitt_bands_all_have_a_lower_exit_than_enter() -> None:
    """Every hysteresis band must be (enter, exit) with exit < enter, or it is not hysteresis.

    Inverted or equal bounds would type-check, import fine, and reintroduce the boundary flap
    the bands exist to remove -- with no symptom other than a twitchy pet.
    """
    bands = {
        "BAND_CPU_HIGH": config.BAND_CPU_HIGH,
        "BAND_RAM_HIGH": config.BAND_RAM_HIGH,
        "BAND_CPU_MODERATE": config.BAND_CPU_MODERATE,
        "BAND_SWAP_HIGH": config.BAND_SWAP_HIGH,
        "BAND_TEMP_HIGH": config.BAND_TEMP_HIGH,
    }
    for name, (enter, exit_) in bands.items():
        assert exit_ < enter, f"{name} = ({enter}, {exit_}) is not a Schmitt band"


def test_thresholds_match_the_cleaned_spec_table_not_the_prototype() -> None:
    """20 / 60 / 80 / 85, from CLAUDE.md's state table -- not the prototype's 30 / 75.

    PHASE1-LOG.md defect 7. The prototype's numbers contradicted the spec, and the two sets are
    close enough that the discrepancy is invisible unless something asserts it.
    """
    assert config.CPU_IDLE_MAX == 20.0
    assert config.CPU_MODERATE_MIN == 20.0
    assert config.CPU_MODERATE_MAX == 60.0
    assert config.CPU_HIGH == 80.0
    assert config.RAM_HIGH == 85.0
    assert 30.0 not in (config.CPU_IDLE_MAX, config.CPU_HIGH)
    assert 75.0 not in (config.RAM_HIGH, config.CPU_HIGH)


def test_nonloop_end_policy_stays_hold() -> None:
    """Contract 2.1: the animator holds; `StateMachine` owns all reversion.

    Flipping this to "fallback" gives two owners racing over the same state and a pet that
    ping-pongs. It is a one-word change with no local symptom, so it gets a guard.
    """
    assert config.NONLOOP_END_POLICY == "hold"


def test_render_scale_defaults_to_an_integer_one() -> None:
    """Contract 2.5 again, at the config level: the default scale is `int`, not `float`."""
    assert isinstance(config.RENDER_SCALE, int)
    assert config.RENDER_SCALE == 1
    assert config.SMOOTH_SCALING is False, "nearest-neighbour, or the 0.6x resample is blurred"


def test_flourish_pools_only_reference_real_states() -> None:
    """A typo in a flourish pool must be impossible to ship.

    `_pick_flourish` filters unknown names at runtime so a typo degrades rather than crashes,
    but degrading silently to "no flourish ever plays" is not a good outcome either.
    """
    for pool_name in ("IDLE_FLOURISHES", "MODERATE_FLOURISHES"):
        for state, hold_ms, weight in getattr(config, pool_name):
            assert state in STATES, f"{pool_name} references unknown state {state!r}"
            assert hold_ms > 0, f"{pool_name}: {state} has a non-positive hold"
            assert weight > 0, f"{pool_name}: {state} has a non-positive weight"


def test_interaction_greeting_state_is_a_real_state() -> None:
    """`INTERACTION_GREETING_STATE` is a state *name*, so it has to name one."""
    assert (config.INTERACTION_GREETING_STATE is None
            or config.INTERACTION_GREETING_STATE in STATES)


def test_config_module_is_qt_free() -> None:
    """`config.py` must import no Qt, so settings are unit-testable with no QApplication.

    Asserted structurally rather than by `sys.modules`, which would be polluted by whichever
    test happened to run first.
    """
    source = config.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    offenders = re.findall(r"^\s*(?:from|import)\s+(PyQt6\S*)", text, re.MULTILINE)
    assert not offenders, f"config.py imports Qt: {offenders}"
