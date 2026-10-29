"""Resolve optional role models without choosing or translating model identifiers."""

from __future__ import annotations

import re
from pathlib import Path

ROLES = ("code", "hardest", "judgment", "review", "fast")


def role_models(contract: Path | None = None, user_file: Path | None = None) -> dict[str, str]:
    user_file = user_file or Path.home() / ".agents/pstack-models.md"
    values = {}
    if user_file.is_file():
        for line in user_file.read_text(encoding="utf-8").splitlines():
            match = re.fullmatch(r"(code|hardest|judgment|review|fast):\s*(.*?)\s*", line)
            if match:
                role, value = match.groups()
                if role in values:
                    raise ValueError(f"duplicate model role: {role}")
                values[role] = value
    if contract:
        if __package__:
            from .doctor import read_contract_v2
        else:
            from doctor import read_contract_v2
        data = read_contract_v2(contract)
        agents = data.get("agents") or {}
        if not isinstance(agents, dict) or not isinstance(agents.get("models", {}), dict):
            raise ValueError("agents.models must be a mapping")
        models = agents.get("models", {})
        for role, value in models.items():
            if role not in ROLES:
                raise ValueError(f"unknown model role: {role}")
            if value is not None and not isinstance(value, str):
                raise ValueError(f"model role {role} must be a string or empty")
            if value and value.strip():
                values[role] = value.strip()
    return {role: value for role, value in values.items() if value and value != "inherit-parent"}
