---
name: gate-packet
version: 0.1.0
description: Checklist and gate status for a decision-gate stage from contract gate_stages. Assembles qa-qc-preread, then PASSes, DRAFTs with open issues, or REFUSEs (exit 2) in strict mode. Use when packaging RE deliverables for a named stage. Analyst role. Does not start Flow or ERT. Does not invent numbers.
---

# Gate packet

Assemble the pre-read, then apply the stage checklist from contract `gate_stages`.
There are no built-in stage names. An unknown stage is an error (exit 2).

P10/P90: state the contract `p10_p90` convention in every packet.

## Modes
| Flag / default | Behavior |
|---|---|
| `gate_stages.<stage>.mode_default: draft` | Write packet; open issues listed; exit 0 |
| `gate_stages.<stage>.mode_default: strict` | Exit 2 if any open issue or missing required artifact |
| `--allow-open-issues` | Force draft |
| `--strict` | Force strict |

`mode_default` must be `draft` or `strict`. Any other value is an error.

## Step 1. Contract
Run `stage-contract`. Read `gate_stages` and `gate_default_stage`.

## Step 2. Draft packet (open issues OK, listed)
Use the `python` of the contract profile:
```bash
python skills/gate-packet/scripts/gate_packet.py --contract site.contract.yml --stage <STAGE>
# or force draft: --allow-open-issues
```

## Step 3. Strict packet
```bash
python skills/gate-packet/scripts/gate_packet.py --contract site.contract.yml --stage <STAGE> --strict
# exit 2 until open issues are cleared
```

## Outputs
```
deliverables/gate_packets/<STAGE>/
  preread.md | preread_outline.json | preread.html
  gate_packet.json | gate_packet.md
```

## Exit codes
| Code | Meaning |
|---|---|
| 0 | PASS or DRAFT (draft may list open issues) |
| 2 | REFUSED (missing required artifacts and/or strict with open issues), unknown stage, or bad contract |

## Rules
- Do not start ERT, Everest, Flow, or deletes.
- Do not invent numbers. Cite deliverables only.
- Scenario QC FAIL rows and missing required SUMMARY vectors are open issues; they must surface in the pre-read.
- Plots are evidence, not a substitute for tables or scenario-qc.
