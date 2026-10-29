# res2csv cheat sheet

Tested with res2df 1.3.15 on an OPM Flow case. All commands read the `.DATA` file and the files with the same base name.
Use `-o -` to print to the screen. Use `-o <file>` to write a file. The default for summary is `summary.csv`.

## Subcommands
`grid`, `summary`, `nnc`, `faults`, `trans`, `pillars`, `pvt`, `rft`, `fipreports`, `satfunc`, `compdat`,
`equil`, `gruptree`, `wellcompletiondata`, `vfp`, `wellconnstatus`, `wcon`.
Run `res2csv <subcommand> --help` for the arguments of each one.

## Summary
```bash
res2csv summary --time_index yearly --column_keys FOPT FWPT "WOPT:*" -o - CASE.DATA
```
| Argument | Meaning |
|---|---|
| `--time_index` | `raw`, `daily`, `monthly`, `yearly`, `first`, or `last`. Default is `raw`. |
| `--column_keys` | Space-separated vector names or wildcards. Default is all vectors. |
| `--start_date`, `--end_date` | ISO dates. Ignored for `first` and `last`. |
| `-p`, `--params` | Merge `parameters.txt` (or `.json`, `.yml`) into each row. |
| `--paramfile` | Name of the parameter file, if it is not the default. |
| `--include_restart` | Add data from before a restart. |
| `--arrow` | Write Arrow format. |

Output columns: `DATE`, then one column for each vector. Well vectors have names like `WOPT:A-1`.

## Deck and table subcommands
| Command | Output columns (first ones) |
|---|---|
| `res2csv compdat` | `WELL, I, J, K1, K2, OP/SH, SATN, TRAN, WBDIA, KH, SKIN, ... DATE` |
| `res2csv equil` | `Z, PRESSURE, OWC, PCOWC, GOC, PCGOC, ... EQLNUM, KEYWORD` |
| `res2csv satfunc` | `SW, KRW, KROW, PCOW, SATNUM, KEYWORD, SG, KRG, KROG, PCOG` |
| `res2csv pvt` | `PRESSURE, VOLUMEFACTOR, VISCOSITY, RS, PVTNUM, KEYWORD, ...` |
| `res2csv gruptree` | `DATE, CHILD, KEYWORD, PARENT` |

## In an ERT config
res2df installs the forward-model step `RES2CSV`. It takes the arguments `<SUBCOMMAND>`, `<OUTPUT>`, and `<XARG1>` to `<XARG10>`.
`<ECLBASE>` is the last argument. The step fails at validation if `<SUBCOMMAND>` or `<OUTPUT>` is empty.

## Python
```python
from res2df import ResdataFiles, summary

files = ResdataFiles("path/to/CASE.DATA")
frame = summary.df(files, time_index="yearly", column_keys=["FOPT", "WOPT:*"])
```
The scripts need res2df 1.0 or newer (`ResdataFiles`). The old `ecl2df` package is not supported.
The index of `frame` is the date.

## Common errors
| Message or symptom | Likely cause |
|---|---|
| File error for `.UNSMRY` or `.SMSPEC` | The simulator did not finish, or the base name differs from the `.DATA` name. |
| Empty table | No vector matches `--column_keys`. Check the spelling and the wildcard. |
| Rates differ from the raw output | A non-raw time index interpolates the data. Use the cumulative vector for volumes. |
| Different row counts between realizations | The time index is probably `raw`. Use `yearly` or `monthly`. |
