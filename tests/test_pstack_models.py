"""Model roles preserve explicit inheritance and do not require a provider name."""

import pytest

from tools.pstack_models import role_models


def test_contract_wins_and_empty_roles_fall_back(tmp_path):
    pytest.importorskip("yaml")
    user = tmp_path / "models.md"
    user.write_text(
        "# Roles\ncode: GPT-5\nhardest:\njudgment: inherit-parent\nreview: Claude Sonnet 4.5\nfast:\n"
    )
    contract = tmp_path / "contract.yml"
    contract.write_text("agents:\n  models:\n    code: inherit-parent\n    hardest: gpt-5\n    review:\n")
    assert role_models(contract, user) == {"hardest": "gpt-5", "review": "Claude Sonnet 4.5"}


def test_empty_configuration_uses_parent(tmp_path):
    user = tmp_path / "missing.md"
    assert role_models(user_file=user) == {}
