#!/usr/bin/env python3
"""Write the Everest `controls:` section for the variables of a scenario spec.

  everest_controls.py --spec scenario.yml --out controls.yml [--name scenario] [--perturbation 0.05]

One control (default name `scenario`) with one variable per spec variable: initial_guess, min, max,
perturbation_magnitude (a fraction of the range, default 5 %), and control_type integer for integer variables.
Everest writes the control values of each simulation to `<name>.json` in the runpath. Render that point with
  scenario_build.py --spec scenario.yml --controls <runpath>/<name>.json --out <runpath>/<scenario>
The objective, model, and forward-model steps stay in the Everest config. This script runs nothing.
Exit: 0 written, 2 bad spec or no variables.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import scenario_build as sb  # noqa: E402


def controls_section(spec: sb.Spec, name: str = "scenario", perturbation: float = 0.05) -> dict[str, Any]:
    if not spec.variables:
        raise sb.SpecError("the spec has no variables: nothing for Everest to control")
    if not 0 < perturbation <= 0.5:
        raise sb.SpecError("perturbation must be a fraction of the range in (0, 0.5]")
    variables = []
    for var in spec.variables.values():
        entry: dict[str, Any] = {
            "name": var.name,
            "initial_guess": number(var.initial),
            "min": number(var.low),
            "max": number(var.high),
            "perturbation_magnitude": round((var.high - var.low) * perturbation, 6),
        }
        if var.integer:
            entry["control_type"] = "integer"
        variables.append(entry)
    # Everest 26 deprecates `type`, which only pairs with the deprecated `wells` section, so the control has none.
    return {"controls": [{"name": name, "variables": variables}]}


def number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="YAML file to write (default: stdout)")
    parser.add_argument("--name", default="scenario", help="control name; Everest writes <name>.json")
    parser.add_argument("--perturbation", type=float, default=0.05, help="fraction of each variable range")
    args = parser.parse_args(argv)
    try:
        section = controls_section(sb.load_spec(args.spec), args.name, args.perturbation)
    except (sb.SpecError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    text = sb.contract_lib._yaml().safe_dump(section, sort_keys=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote: {args.out} ({len(section['controls'][0]['variables'])} variables)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
