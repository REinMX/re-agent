---
name: deck-scal-pvt-qc
description: Check the property tables of a simulator deck before a run. The tables are relative permeability, capillary pressure, PVT, EQUIL, and VFP. Also make or interpolate SCAL tables with pyscal and subscript. Use it for questions about a deck or an include file, a table that breaks a run, or relperm input. Analyst role. The checks do not write.
version: 0.2.0
---

# Deck, SCAL, and PVT QC

You are the analyst. `scripts/deck_qc.py` reads tables with res2df and applies rules.
It does not run a simulator. A table that passes is not proof that the model is right.
For schedule checks, use `scripts/schedule_qc.py --deck <CASE>.DATA --json`.
It checks well references, completion bounds, negative history rates, and date order.
For an AHM physics check, use `scripts/physics_checklist.py --deck <CASE>.DATA [--ert-config <CASE>.ert]`.
It reads text only. It checks the aquifer, the fault segments, the kv/kh and barriers, the well PI, WCONHIST, and ROCK.
A missing item is a warning. A template or a matching GEN_KW name makes an item uncertain in the AHM.
The OPM parser (res2df) reads the deck with its INCLUDE files and DIMENS. This is the default.
If the deck does not parse, the script gives `L001`. Then use `--parser text`. The report names the parser.
Deck mode runs the same rules on the deck that it parsed.
To scan a schedule include alone, give it as `--deck`. Add `--dimens NX NY NZ` to check the completion bounds.
Without DIMENS, completion bounds are not checked. See `references/rules.md` for the codes and limits.
`S010`, `T002` and `T003` compare the tables with `TABDIMS`. Flow accepts more rows than NSSFUN. Eclipse stops.

## Step 1. Choose the mode
| Situation | Command |
|---|---|
| The deck parses in OPM | `deck_qc.py --deck <CASE>.DATA` |
| The deck does not parse, or you have only include files | `deck_qc.py --include a.inc --include b.inc` |

In deck mode the script first scans the `INCLUDE` and `IMPORT` lines as text. It reports each missing file as `I001`.
A deck that RMS prepares has includes that a workflow generates. They are often missing in a clone of the repository.
A deck with the keyword `IMPORT` also fails in the OPM parser. The script then reports `L001` with the parser message.
Use text mode on the include files of the tables.
Text mode reads each table family alone, from the files that have its keywords. It cannot see the unit system,
unless a file has `METRIC`, `FIELD`, or `LAB`. If a message is about table sizes, add the include files with
`TABDIMS` or `EQLDIMS`. Include files from Prosper are not UTF-8. The script reads them as latin-1.

## Step 2. Run
```bash
python skills/deck-scal-pvt-qc/scripts/deck_qc.py --deck <CASE>.DATA
python skills/deck-scal-pvt-qc/scripts/deck_qc.py --include props/pvt.inc --include props/relperm.inc --json
```
Exit code 0 means no errors. Exit code 1 means errors. Exit code 2 means a usage error.
Report each finding with its code and table, for example `S003 [SWOF SATNUM 2]`. See `references/rules.md`.

## Step 3. Interpret
- **Error (E):** the table breaks a simulator rule or is physically impossible. Give the table, the rule, and the rows.
- **Warning (W):** the table is unusual. A warning can be intended. Ask the user.
- **Information (I):** a fact for the engineer. It is not a problem.
- A finding in one SATNUM or PVTNUM does not mean that the other regions are wrong.
- Rules are checks of consistency. They do not know the field. A table with no findings can still be wrong for the reservoir.

## Step 4. Make or change SCAL tables
Do not edit a generated include file by hand. Change the input and generate again.
1. **Corey or LET tables from parameters:** `pyscal`. One SATNUM for each row of a CSV or XLSX file.
   ```bash
   pyscal relperm.csv --delta_s 0.1 -o relperm.inc
   ```
   Write the output to `output_root`. Then run `deck_qc.py --include relperm.inc`.
2. **Low, base, and high parameter recommendations:** use `scal-uncertainty` with pyscal `--int_param_wo`.
   Use `interp_relperm -c <config>.yml` only for already tabulated low, base, and high curves.
   These tables must have both SWOF and SGOF (or SWFN and SGFN). It cannot process an oil-water deck.
3. **SWATINIT against SWAT after a run:** use `scripts/swatinit_qc.py` in Step 5.
4. **VFP files from Prosper to a table:** `vfp2csv <files> -o vfp.csv`.

Details and the other subscript tools are in `references/rules.md`.
Do not copy a SCAL recommendation from memory. Use the values that the user gives and the reference they name.

## Step 5. Check initialization after a run
```bash
python skills/deck-scal-pvt-qc/scripts/swatinit_qc.py --case <CASE>.DATA --out <output_root>/swatinit
```
It needs subscript, INIT, and UNRST with report step 0. It sets the headless display options.
Use `--ensemble-glob '<runpath>/realization-*/iter-N/model/<CASE>*'` for an ensemble.
The glob selects ECLBASE paths or DATA files. Each realization must have one case.
The output folder must be new or empty and outside the input tree.
It writes `swatinit_qc.json`, `swatinit_qc.csv`, and `report.md`.

| Option | Default | Use |
|---|---|---|
| `--max-hc-below-fwl` | 1.0 | Maximum magnitude of HC change below FWL, percent of SWATINIT HCPV |
| `--max-swl-trunc` | 1.0 | Maximum magnitude of HC change from SWL truncation, same basis |
| `--max-pc-scaling` | 20 | Maximum capillary pressure scale factor |
| `--params` | Auto | A parameter file next to the case or in its parent folder |
| `--contact-params` | OWC, GOC, FWL, GWC names | Explicit contact parameter names |
| `--keep-cells` | Off | Save the large cell CSV files |

A relative `--params` path is relative to each case folder.
Volume changes keep their signs. Limits apply to their magnitudes.
A contact parameter with absolute Spearman correlation of at least 0.7 with HC_BELOW_FWL gets a STOIIP knob flag.
This flag is screening evidence. It does not prove causation. At least three paired realizations are required.
Missing parameter files are reported. They do not imply that contact effects are absent.
Exit 0 means no limit was exceeded, or SWATINIT is not applicable.
A case without SWATINIT is reported as "not applicable". It is not a pass.
Exit 1 means a limit was exceeded. Exit 2 means an input or dependency error.

## Report
1. The command, the input files, and the unit system (or "not known" in text mode).
2. The number of tables read for each family.
3. Each error and warning, with code, table, and rows.
4. What you did not check. Name the table families that are absent.
