---
name: resinsight-plots
version: 0.1.0
description: Export summary plot evidence for FMU ensemble results. Prefer ResInsight CLI headless snapshots (binary and headless mode from the contract profile); fall back to matplotlib from deliverables profiles/bands. Use after ensemble-profiles and ensemble-bands. Plots are evidence only — tables and scenario-qc gate pass/fail. Analyst role. Does not start Flow or ERT.
---

# ResInsight plots

Write PNGs under `deliverables/plots/`. Do not treat plots as a QC gate.

P10/P90: matplotlib band figures use the contract `p10_p90` convention. State it on every band plot.
ResInsight `--summaryplot` shows cases, not percentile bands.

## Step 1. Contract and inputs
Run `stage-contract`, then make sure `deliverables/profiles/` and `deliverables/bands/` exist.
The profile gives `resinsight.bin`, `resinsight.headless` (`xvfb-run` or `none`),
`resinsight.gui_launcher`, and `resinsight.version`.

## Step 2. Orchestrator (preferred)
Use the `python` of the contract profile (matplotlib, pandas):
```bash
python skills/resinsight-plots/scripts/resinsight_plots.py --contract site.contract.yml \
  --multi-reals 0,10,20
```

Order inside the script:
1. `try_resinsight_cli.py`: ResInsight `--summaryplot` with `--savesnapshots plots`, headless as the profile says.
   `--ri-bin` and `--headless` override the profile.
2. `plot_profiles_evidence.py`: matplotlib bands and a multi-realization sample from deliverables.
   It always runs for P10/P90 bands, and covers the required vectors if ResInsight fails.

- The deck name comes from contract `eclbase`. `--eclbase` overrides it.
- `--iter` selects the runpath iteration (default contract `runpath.default_iter`).
- Use `--skip-resinsight` when the profile has no ResInsight.

## Step 3. Required plots
The required vectors are contract `vectors.plot_required` (default FOPT, FOPR), plus
`WOPR:<wells.plot_default>` when the contract names a well.
A missing SUMMARY vector gives no plot and a failed coverage. Do not invent plots.

## Outputs
```
deliverables/plots/
  manifest.json   # required_vectors, coverage_ok, p10_p90
  README.md
  resinsight/     # ResInsight CLI PNGs when CLI works
  evidence/       # matplotlib; labeled NOT ResInsight
```

## Interactive GUI (when a display is available)
Use the profile `resinsight.gui_launcher`, or `ResInsight --case <SMSPEC_or_EGRID>`.

## Rules
- Plots are evidence only. Pass/fail comes from tables (`ensemble-bands`, `scenario-qc`).
- Do not start ERT or Flow. Do not delete scratch. Do not write inside scratch.
- Missing SUMMARY vector = no plot for that vector. Never fill zeros.
