# New-well definitions

Put one schedule file for each new well here, with the name that `drill_wells` gives it.
The file holds the WELSPECS and the COMPDAT of the well, and nothing else. Open the well SHUT in WELSPECS
or leave it without a control: `well_control.py schedule` opens it with WCONPROD at its drill date.

```
WELSPECS
 '<well>' '<group>' <i> <j> 1* OIL /
/
COMPDAT
 '<well>' <i> <j> <k1> <k2> OPEN 1* 1* <diameter> /
/
```

Take the trajectory and the completions from the well plan. Do not copy them from another project.
