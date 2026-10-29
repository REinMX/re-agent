# Rules of deck_qc.py

Tested with res2df 1.3.15, pyscal 0.17.0, and subscript, on the SPE1 and SPE9 decks, the synthetic HDN deck,
the public Drogon includes, and pyscal output.

## Rules
| Code | Sev | Rule | Why it matters |
|---|---|---|---|
| L001 | E | The deck or a table family cannot be read. | The checks for that family did not run. |
| I001 | E | An `INCLUDE` or `IMPORT` file does not exist. Found by a text scan. The script skips paths with `$` or `<>`. | The simulator stops at that line. |
| S001 | E | Saturation is not strictly increasing, or the table has fewer than 2 rows. | A simulator cannot interpolate. |
| S002 | E | A saturation or relative permeability is outside 0 to 1, or undefined. | Not physical. |
| S003 | E | `KRW` or `KRG` decreases with saturation. `KROW` or `KROG` increases with water or gas saturation. | Relative permeability must be monotone. |
| S004 | W | Capillary pressure goes both up and down. | Usually a table error. |
| S005 | W | `KRW` is not 0 at the first water saturation. | Water is mobile at connate saturation. This can be intended. |
| S006 | I | `SWOF` does not end at Sw = 1. | Normal when Sw max is 1 - Sor. SPE9 is such a deck. |
| S007 | W | `SGOF` does not start at Sg = 0. | The gas table needs the zero point. |
| S008 | W | `KROW` at connate water differs from `KROG` at Sg = 0. | The three-phase end points disagree. |
| S009 | W | Connate water plus maximum gas saturation is larger than 1. | The saturation ranges overlap. |
| P001 | E | Pressure, volume factor, viscosity, or density is not positive. `RS` or a `ROCK` compressibility is negative. | Not physical. |
| P002 | E | Pressure is not increasing where required. `RS` is not strictly increasing between `PVTO` records. | A simulator needs ordered tables. |
| P003 | W | Undersaturated oil volume factor increases with pressure. Viscosity decreases with pressure. Saturated pressure or Bo falls when `RS` rises. `PVDO` Bo increases with pressure. | Unusual trend. |
| P004 | W | Gas volume factor (`PVDG`) increases with pressure. | Unusual trend. |
| P005 | W | `ROCK` compressibility is 0. | Legal: an incompressible rock. The published Egg model has `ROCK 400 0` and runs in Flow. Often a placeholder. |
| E001 | E | EQUIL datum pressure is not positive. | Not physical. |
| E002 | E | The gas-oil contact is deeper than the oil-water contact. | Gas below water is not possible. |
| V001 | E | A VFP table has `TAB` values that are not finite or not positive. | The simulator cannot use the table. |
| V002 | W | BHP falls when THP rises at fixed rate and fractions. | Usually a table error. |
| V003 | W | The VFP datum depth is 0 or missing. | Unusual. |
| V004 | W | The VFP unit system differs from the deck. | A unit error changes all pressures. |
| T001 | I | A table family is absent, or a keyword is not checked. | Tells you what was not checked. |
| T002 | E | A keyword has more tables than `TABDIMS` NTSFUN (saturation) or NTPVT (PVT). A text scan counts the tables. | res2df reads only the first NTSFUN tables (measured: 3 of 12), so the other checks miss tables. The simulator needs the right count. |
| T003 | W | A PVT table has more rows than `TABDIMS` NPPVT, or a `PVTO`/`PVTG` table has more records or rows than NRPVT or NPPVT. Saturation rows above NSSFUN are `S010`. | Flow accepts extra saturation rows (measured on Egg: 91 rows, NSSFUN 24). Eclipse stops (from the manual, not tested). |

The rules are consistency checks. They are not a standard. A rule with a known exception is a warning or information.

## What the tool does not check
- Values against a SCAL study, a PVT report, or a fluid sample.
- `SWATINIT`, `SCALECRS`, end-point scaling, and hysteresis.
- Region numbers in the grid against the number of tables.
- `PVTG` trends other than pressure order. `SLGOF`, `SGWFN`, and `SWFU` tables.
- Whether the simulator accepts the table. Only a run proves it.

## Facts from the test decks
- `SPE9_CP.DATA`: SWOF ends at Sw = 0.88149 and runs in Flow. A rule that required Sw = 1 gave a false alarm. It is information now.
- `DROGON_HIST.DATA`: the OPM parser stops at the keyword `IMPORT` (line 123). The text scan finds 17 missing includes that RMS exports
  (grid, properties, regions, and the schedule). The runspec includes exist. Use `--include` on the table files that exist.
- Drogon `drogon.pvt` (2 PVTNUM, 18 `RS` records in PVTNUM 1, 389 rows) and the four Prosper VFP files in
  `eclipse/include/schedule/vfp` give no errors and no warnings.
- The Prosper VFP files contain a byte that is not valid UTF-8. Read deck text as latin-1.
- res2df needs the phases (`OIL`, `GAS`, `WATER`) to read EQUIL. The script adds them in text mode.
- res2df takes seconds to guess table sizes when a keyword is absent. The script reads only the families that exist.
- One parse error spoils a joined text. PVT (2 tables) and EQUIL (7 records) in one text failed. The script reads each family alone.

## pyscal
`pyscal <parametertable.csv|xlsx> --delta_s <step> -o <file.inc>`. One SATNUM for each row.
Useful options: `--int_param_wo` and `--int_param_go` (interpolation parameters between -1 and 1 when the table has
low, base, and high rows), `--slgof`, `--family2`, `--plot`.
The CSV columns for Corey are, for example: `SATNUM, swl, sorw, Nw, Now, swcr, sorg, Ng, Nog`.
Pyscal writes comments in the include file with the parameters, so keep the file with the model.
The Python API (`WaterOil`, `GasOil`, `WaterOilGas`) makes the same curves.

## subscript tools for decks (from their --help)
| Tool | Purpose |
|---|---|
| `check_swatinit` | QC of SWATINIT against SWAT in a finished run with restart data. Output CSV and plots. |
| `interp_relperm` | Interpolate low, base, and high SWOF/SGOF (or SWFN/SGFN) with parameters in [-1, 1]. |
| `vfp2csv` | Relayout VFPPROD and VFPINJ files (for example from Prosper) as CSV. |
| `prtvol2csv` | In-place volumes per FIPNUM from an Eclipse PRT file. |
| `sunsch` | Make a schedule file from merges and insertions, defined in a YAML file. OPM checks the output. |
| `eclcompress` | Run-length compression of input files (`1 1 1 1` becomes `4*1`). It replaces the files. Ask first. |
# Schedule rules

`scripts/schedule_qc.py --parser opm` (the default) reads the deck with the OPM parser through res2df, as Flow does.
It reads the INCLUDE files and DIMENS of the deck (lessons G1 and G2). A text scan runs first and reports `I001` and `I002`.
The OPM parser then does not run: it skips a missing file and stops the process on a cycle. The script reports `L001`.
A deck that the OPM parser cannot read is `L001`. There is no fallback to the text scan. res2df missing is exit code 2.
`--parser text` is the text scan. It needs no res2df. It follows INCLUDE files. A path is relative to the file that has the
INCLUDE keyword. `--dimens NX NY NZ` gives the grid size to a schedule include that has no DIMENS (both parsers).
The report names the parser. Deck QC runs the OPM rules on the deck that it parsed.

| Code | Severity | Check |
|---|---|---|
| W001 | E | WCONHIST, WCONPROD, WCONINJE, WCONINJH, or COMPDAT refers to a well with no preceding WELSPECS. Wildcard names must match a defined well. In a deck with RESTART, the wells can come from the restart file: one W warning lists them. |
| W002 | E | COMPDAT I/J exceeds DIMENS, an index is negative, K is outside 1..NZ, or K1 exceeds K2. I/J zero uses the WELSPECS location and is allowed. |
| W003 | E | An explicit oil, water, or gas rate in WCONHIST is negative. Zero is allowed. |
| W004 | E | DATES records are not strictly increasing. |
| W005 | E/W | A record is invalid, or a default or missing DIMENS prevents a bounds check. A missing DIMENS gives one warning for each file, with the count of COMPDAT records that were not checked. |
| W006 | W | No well keyword (WELSPECS, COMPDAT, WCONHIST, WCONPROD, WCONINJE, WCONINJH) was found. Nothing was checked. |
| I001 | E | An INCLUDE file does not exist. |
| I002 | E | An INCLUDE file includes itself directly or through other files. |

Text scan limits: keywords must be on their own lines. Slash records can span lines.
Repeat and default fields are expanded. With the OPM parser, `where` is the keyword and the well, not a file line.
An INCLUDE path with an environment variable or a PATHS alias is not resolved. It gives I001.
There is no ACTNUM, LGR, unit, VFP constraint, history end date, or control mode check.
WELSPECS preferred phase does not identify an injector. No injector warning is inferred from it.
Give the main DATA file, not an include alone, when include order affects a well reference.

## SCAL row limits and rock compressibility

`S010` warns when a saturation table exceeds TABDIMS NSSFUN in deck mode.
`P005` warns for zero rock compressibility. Negative or nonfinite rock compressibility is `P001` error.
