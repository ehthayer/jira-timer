"""Characterization tests for state-file I/O and type handling in jira_timer.cli.

Locks in current behavior BEFORE the Phase-2 refactor (atomic writes,
narrower except, type coercion). If these break during refactor, something
regressed.
"""
import json
import os
import stat

import pytest

from jira_timer import cli


@pytest.fixture(autouse=True)
def state_in_tmp(tmp_path, monkeypatch):
    """Redirect STATE_FILE to a temp dir so tests don't touch the real file."""
    state_file = tmp_path / ".jira-timer.json"
    monkeypatch.setattr(cli, "STATE_FILE", state_file)
    return state_file


class TestInitState:
    def test_creates_file_with_defaults(self, state_in_tmp):
        state = cli.init_state()
        assert state_in_tmp.exists()
        assert state["ticket"] is None
        assert state["start_time"] is None
        assert state["accumulated"] == 0
        assert state["paused"] is False
        assert state["paused_reason"] is None
        assert state["status_cache"] == {}
        assert state["config"]["rounding"] == cli.CONFIG_ROUNDING
        assert state["config"]["roundDirection"] == cli.CONFIG_ROUND_DIRECTION

    def test_preserves_existing_state(self, state_in_tmp):
        """init_state() must not overwrite an existing state file.

        Load-time coercion may normalize types (e.g. start_time), but user
        data like the ticket and custom fields must survive.
        """
        state_in_tmp.write_text(json.dumps({"ticket": "ENG-1", "custom_field": 42}))
        state = cli.init_state()
        assert state["ticket"] == "ENG-1"
        assert state["custom_field"] == 42


class TestLoadSaveRoundTrip:
    def test_roundtrip_preserves_all_fields(self, state_in_tmp):
        src = {
            "ticket": "ENG-1",
            "start_time": 1_700_000_000,
            "accumulated": 1234,
            "paused": True,
            "paused_reason": "idle",
            "status_cache": {"ENG-1": {"status": "In Progress", "timestamp": 1.5}},
            "config": {"rounding": 15, "roundDirection": "up"},
        }
        cli.save_state(src)
        assert cli.load_state() == src

    def test_save_state_sets_mode_0600(self, state_in_tmp):
        cli.save_state({"ticket": None})
        mode = stat.S_IMODE(os.stat(state_in_tmp).st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"


class TestLoadStateRecovery:
    def test_missing_file_returns_default_without_writing(self, state_in_tmp):
        """load_state() must not have write side effects; only init_state() writes."""
        assert not state_in_tmp.exists()
        state = cli.load_state()
        assert not state_in_tmp.exists(), "load_state should not create the file"
        assert state["ticket"] is None
        assert state["accumulated"] == 0

    def test_corrupt_json_backs_up_and_returns_default(self, state_in_tmp):
        state_in_tmp.write_text("{not valid json")
        state = cli.load_state()
        assert state["ticket"] is None
        assert state["accumulated"] == 0
        # Corrupt file should be preserved with a .corrupt.<ts> suffix.
        corrupt = list(state_in_tmp.parent.glob(".jira-timer.json.corrupt.*"))
        assert len(corrupt) == 1, f"expected one backup, got {corrupt}"
        assert not state_in_tmp.exists(), "original corrupt file should have been renamed"

    def test_truncated_file_recovers(self, state_in_tmp):
        state_in_tmp.write_text('{"ticket": "ENG-1",')
        state = cli.load_state()
        assert state["ticket"] is None

    def test_empty_file_recovers_without_recursing(self, state_in_tmp):
        """Regression: empty file used to cause infinite recursion between
        load_state() and init_state()."""
        state_in_tmp.write_text("")
        state = cli.load_state()
        assert state["ticket"] is None


class TestStartTimeCoercion:
    """load_state() must guarantee start_time is int-or-None regardless of
    how the file was written. This defends against a historical bug where
    some write paths persisted the string 'None'."""

    def test_none_stays_none(self, state_in_tmp):
        cli.save_state({"start_time": None, "accumulated": 0})
        assert cli.load_state()["start_time"] is None

    def test_int_preserved(self, state_in_tmp):
        cli.save_state({"start_time": 1_700_000_000, "accumulated": 0})
        loaded = cli.load_state()
        assert loaded["start_time"] == 1_700_000_000
        assert isinstance(loaded["start_time"], int)

    def test_string_none_coerced_to_none(self, state_in_tmp):
        state_in_tmp.write_text(json.dumps({"start_time": "None", "accumulated": 0}))
        assert cli.load_state()["start_time"] is None

    def test_numeric_string_coerced_to_int(self, state_in_tmp):
        state_in_tmp.write_text(json.dumps({"start_time": "1700000000", "accumulated": 0}))
        loaded = cli.load_state()
        assert loaded["start_time"] == 1_700_000_000
        assert isinstance(loaded["start_time"], int)

    def test_garbage_string_coerced_to_none(self, state_in_tmp):
        state_in_tmp.write_text(json.dumps({"start_time": "xyz", "accumulated": 0}))
        assert cli.load_state()["start_time"] is None

    def test_accumulated_coerced_to_int(self, state_in_tmp):
        state_in_tmp.write_text(json.dumps({"start_time": None, "accumulated": "42"}))
        loaded = cli.load_state()
        assert loaded["accumulated"] == 42
        assert isinstance(loaded["accumulated"], int)


class TestAtomicWrite:
    def test_no_tmp_files_left_behind(self, state_in_tmp):
        cli.save_state({"ticket": "ENG-1", "accumulated": 0})
        leftovers = list(state_in_tmp.parent.glob(".jira-timer.*.tmp"))
        assert leftovers == [], f"leftover tmp files: {leftovers}"
