"""Profile migrations keep command proposals local and use no site values."""

from pathlib import Path

import pytest

from tools.doctor import read_runtime_contract

yaml = pytest.importorskip("yaml")
ROOT = Path(__file__).resolve().parents[1]


def test_flow_settings_are_profile_local(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_AGENT_PROFILE", raising=False)
    path = tmp_path / "contract.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "active_profile": "home",
                "profiles": {
                    "home": {
                        "simulator": {"version": "test"},
                        "queue": {"system": "LOCAL"},
                        "flow_cmd": "synthetic-wrapper",
                        "flow_extra_args": "--synthetic=true",
                    },
                    "company": {"flow_cmd": None, "flow_extra_args": None},
                },
            }
        )
    )
    result = read_runtime_contract(path)
    assert result["flow_cmd"] == "synthetic-wrapper"
    assert result["flow_extra_args"] == "--synthetic=true"
    assert result["simulator_version"] == "test"
    assert result["queue_system"] == "LOCAL"
    assert read_runtime_contract(path, "company")["flow_cmd"] is None


def test_example_flow_keys_are_empty_and_nested():
    data = yaml.safe_load((ROOT / "site.contract.example.yml").read_text())
    assert "flow_cmd" not in data and "flow_extra_args" not in data
    for name in ("home", "company"):
        assert data["profiles"][name]["flow_cmd"] is None
        assert data["profiles"][name]["flow_extra_args"] is None
