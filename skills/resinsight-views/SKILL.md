---
name: resinsight-views
version: 0.1.0
description: Make ResInsight 3D views of one case from a ViewSpec (a JSON file) and save PNG files, a manifest, and a project file. Use it to show a grid property, an array from a numbers step, a layer or a row with a range filter, a north or east section, or the well completions that grid-property-qc found. Pictures only; numbers come from resdata. Analyst role.
---

# ResInsight views

This skill makes pictures. It does not make numbers.
The numbers come from an earlier step (resdata, xtgeo, `grid-property-qc`).
Before you start, do `resinsight-session` Step 2. Run the scripts with `resinsight.python`.

## Step 1. Write a ViewSpec
A ViewSpec is a JSON file. The docstring of `scripts/view_spec.py` gives all the keys.
```json
{"case": "/abs/BASE.EGRID", "out": "/abs/new/folder",
 "arrays": [{"name": "SDRATIO", "file": "/abs/sdratio.grdecl"}],
 "views": [{"name": "sd_k3", "property": "INPUT_PROPERTY:SDRATIO", "range": {"k": [3, 3]}},
           {"name": "swat_row", "property": "DYNAMIC_NATIVE:SWAT", "time_step": "last",
            "range": {"j": [12, 12]}, "camera": "north", "zoom": 3}]}
```
- Use absolute paths. Use a new `out` folder.
- An array is a whole-grid GRDECL file: one value for each cell, i fastest.
  Write it with `scripts/grdecl.py` `write_grdecl`. Show it with `INPUT_PROPERTY:<name>`.
- Range values start at 1. The first and the last cell are in the range.
- `time_step` is an index, `first`, `last`, or a date that is a report step. Another date is an error.
- Use `camera: north` with a one-row range (`j`). Use `camera: east` with a one-column range (`i`).
- An unknown key is an error. Do not remove a key to make the spec pass. Correct the key.

## Step 2. Render
```bash
<resinsight.python> skills/resinsight-views/scripts/render_views.py --spec spec.json --contract site.contract.yml
```
- Exit 0: all views are in `out/views/`.
- Exit 1: one or more views failed. The other views are there. Read `failed` in `out/manifest.json`.
- Exit 2: the spec, the case, an array, ResInsight, or rips is not correct. Nothing was rendered.
The script always writes `out/views.rsp`. Give this file to the engineer. It opens in ResInsight with the arrays.

## Step 3. Well completions (optional)
Do `grid-property-qc` `completion_in_grid.py` first. Then make a spec from its output folder:
```bash
python skills/resinsight-views/scripts/well_views.py --completions <cig-out> --case /abs/BASE.EGRID --out <folder> --only-flagged
```
- `COMPFLAG.grdecl`: 0 no connection, 1 ok, 2 warning, 3 error. A cell with two flags gets the worse flag.
- `spec.json`: for each well, a north section through the row with the most connections.
- ResInsight does not show `COMP-INACTIVE` and `COMP-OUTSIDE` cells. The spec `notes` and `wells.json` name them.
  Put these notes in the report.
- Then do Step 2 with `<folder>/spec.json`.

## Rules
- Pictures only. Take each number in a report from the numbers step, not from a picture or a ResInsight label.
- Put the PNG files in the pre-read. Give the `.rsp` file to the engineer.
- There is no value filter. A property filter from rips shows a label but filters no cell (2026.09.1).
  To show cells by value, write a 0/1 array in the numbers step and show that array.
- Read the `failed` list. Do not report a view that is not in `views`.
- Do not kill ResInsight by name. The session stops its own process.
