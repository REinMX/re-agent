---
name: stage-contract
version: 0.1.0
description: Load and validate site.contract.yml (schema v2.1 with profiles, or v1 flat) for an FMU project. Use it first before ensemble profiles, scenario QC, ERT runs, or gate packets. It refuses missing keys and placeholders, and states the P10/P90 convention. All contract-driven skills use its shared loader. Analyst role.
---

# Stage contract

`scripts/stage_contract.py` loads the site contract, selects the profile, and checks required keys.
It prints a short summary that includes the P10/P90 convention. It writes nothing.
Exit code 0 means valid. Exit code 2 means missing, invalid, or a placeholder is still in a required key.

`scripts/contract_lib.py` is the shared loader. All skills that read the contract use it.
Do not write a second loader in a skill.

## Step 1. Find the contract
The loader looks in this order and uses the first file it finds:

| Order | Location |
|---|---|
| 1 | `--contract <file>` |
| 2 | env `RE_AGENT_CONTRACT` |
| 3 | `./site.contract.yml` (project file) |
| 4 | `~/.config/re-agent/site.contract.yml` (user file) |

The user file holds `profiles` (where things run: python, activate, simulator, queue, scratch_root,
output_root, ResInsight). The project file holds project keys (eclbase, paths, vectors, p10_p90,
gate_stages). The project file overlays the user file: dicts merge, lists replace.
A `profiles` block in the project file is ignored unless `RE_AGENT_ALLOW_PROJECT_PROFILES=1`.

The profile comes from `--profile`, then env `RE_AGENT_PROFILE`, then `active_profile`.
An unknown profile is an error (exit 2). A v1 flat contract maps to the profile `legacy`.

If no contract exists, stop for any task that needs paths, summary vectors, or a run.
Copy `site.contract.example.yml` and ask the user to fill each value. Do not guess.

## Step 2. Run the loader
Use the `python` of the contract profile (PyYAML is required):
```bash
python skills/stage-contract/scripts/stage_contract.py --contract site.contract.yml
python skills/stage-contract/scripts/stage_contract.py --profile <name> --json
python skills/stage-contract/scripts/contract_lib.py   # prints the resolved contract as JSON
```

## Step 3. Use the result
1. State `p10_p90` in every later band or percentile report.
2. Use `paths.*` only. `${scratch_root}`, `${output_root}`, and `$VAR` expand. An unresolved name is an error.
3. Treat `vectors.required_summary` as a hard list. A missing vector is an error. Do not fill zero.
4. If `everest` or `sumo` is disabled, do not start those tools.
5. The simulator name is open (for example FLOW or ECLIPSE). An unknown name gives a note, not an error.

## Rules
- Do not invent a contract value. Fill `<ASK: ...>` placeholders with the user.
- Do not start ERT, Everest, Flow ensembles, or deletes from this skill.
- Do not write inside a runpath or scratch realization folder.
- For scenario work, read `scenario_qc` before you claim a QC pass.
