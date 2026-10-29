# Observations while the well does not flow (HMQC-OBS-NOFLOW)

Lesson G9. On Drogon, 20 of 180 summary observations were dated when the well did not flow.
A3 and A4 were not open yet. A5 and A6 were injectors before start-up. The values were noise around 0.
They added data points and weight, but no reservoir information.

## Command
```bash
python skills/history-match-qc/scripts/obs_qc.py <file.obs> --deck <CASE>.DATA
```
The deck does not need to be run. res2df (the OPM parser) reads its SCHEDULE with the INCLUDE files.
`--case` reads the summary of a run. `--deck` reads the schedule. You can give both.

## Rule
For each well, the script makes a timeline from `WCONHIST`, `WCONPROD`, `WCONINJH`, `WCONINJE`, `WELOPEN`, and `COMPDAT`.
An observation at DATE uses the state of the report step that ends at DATE: the last schedule date before DATE.
A well observation (`W*:WELL`) is a warning when the well in that step:
- is not open yet (DATE is on or before its first schedule date),
- has no WCON keyword yet,
- is `SHUT` or `STOP` (WCON status or a well-level `WELOPEN`),
- has a zero rate in `WCONHIST` (oil + water + gas) or `WCONINJH`,
- has no open connection (`COMPDAT` with `WELOPEN` on connections applied by res2df),
- has no `COMPDAT` or `WCON` keyword in the deck.

`WCONPROD` and `WCONINJE` give a status only. Their rates are targets, not flow.
On one date, a WCON row applies before `WELOPEN`. Wildcard well names match the wells of the deck.
Cumulative keys (`WOPT`, `WWPT`, `WGPT`, `WLPT`, `WWIT`, `WGIT`, and the `H` keys) are not flagged.

## Interpretation
- `WBHP` or `WTHP`: the value is a shut-in pressure, not a flowing pressure. A model with a different shut-in model gives a misfit that is not about the reservoir.
- `WWCT`, `WGOR`, `WGLR`, `WOGR`, `WWGR`: a ratio of zero rates is noise.
- Rates: the value is 0 or noise around 0. It carries no reservoir information.
- One PROPOSAL for each well. Options: remove the observations, or keep them on purpose. Do not edit the file unless the user asks.

## Exit codes
A finding is a warning. It does not change the exit code. res2df missing, a missing `--deck`, or a deck that does not parse is exit code 2.

## Measured
Drogon realization 0 (OPM Drogon, public): res2df reads 232 connections of 11 wells, 278 `WCONHIST` and 14 `WCONINJH` rows, in about 1 s.
The G9 observation file gives 20 warnings: A3 8, A4 8, A5 1, A6 3. The `--case` summary check gives the same wells and counts.
The cleaned project observation files (160 summary observations) give 0 warnings.
