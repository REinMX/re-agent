# Site translation pack

Templates that turn a checked public workflow (Drogon, OPM/opm-tests, ODbL) into a case for a site project.
Site values (Komodo activation, queue, scratch) come from the site contract. Model values (zones, faults, wells,
dates, prices) come from `params.yml` in each template. The `params.yml` defaults document the Drogon example.

| Template | Use |
|---|---|
| `01-fmu-prior-design` | GEN_KW prior with parameter families, a generic parameter job, an observation builder, the prior ERT config. |
| `02-ahm-esmda-qc` | ES-MDA config with rescaled weights and per-type update strategies, the QC chain, and a decision memo. |
| `03-everest-robust-well-control` | Mean and CVaR well-rate optimization with injection-capacity, water-rate, and VRR constraints. Holds the shared job and config generator. |
| `04-everest-drill-order` | Drill priorities to rig slots, with the well cost in the NPV. Uses the job of 03. |
| `05-everest-algorithm-check` | A two-control known-answer case, a grid scan, and three algorithm configs. Uses the job of 03. |

Render each template into a sibling folder with its own name, outside this repository:

```
python tools/render_template.py templates/site/01-fmu-prior-design <project>/ahm/01-fmu-prior-design \
  --contract site.contract.yml --profile company --set agent_root=<this checkout> --set deck_dir=<deck folder>
```

The renderer lists every token without a value, with file and line, and then writes nothing.
A value that is empty or still `<ASK ...>` in the contract counts as missing. Fill the contract; do not edit the
rendered files to work around a missing key.
