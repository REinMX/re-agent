---
bump: reservoir-engineer patch
---
### Added
- `production-kpis` skill. `production_kpis.py` computes plateau length, recovery factor, VRR, gas sales against a configured target, the binding constraint per period for each well and group, and an NPV when prices and a discount rate are given.
- VRR uses reservoir volumes (FVPT/FVIT, else FVPR/FVIR). The flagged surface approximation is used only when there is no gas.
- Sources are one case, a complete ensemble (a partial one fails), ensemble-profiles output, or scenarios against a baseline. The script writes CSV, JSON and md with P10/P50/P90 in the contract convention and lists the unconfirmed defaults.
- The report lists the recommended SUMMARY vectors (FVPR, FVIR, FVPT, FVIT; optional WVPR/WVIR; FOIP/FGIP, FGSR, WMCTL/GMCT*) absent from each case, and lists the gas sales and gas RF methods under "Gas methods".
- Adds router, AGENTS.md and FEATURE_MAP rows, and an empty `production_kpis` block in `site.contract.example.yml`.
