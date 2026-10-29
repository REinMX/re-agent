# {{param:case_name}}: FMU prior design

## What it is

A prior ensemble for an assisted history match. One design file (`ert/design.yml`) names the zones, faults,
zone pairs, and contact groups. The same file drives two steps:

- `bin/prior_design.py dist` writes the GEN_KW distribution file `ert/prior.dist`.
- `bin/prior_design.py apply` is the forward-model step. It turns `parameters.txt` into deck include files.

The parameter families:

| Family | Parameters | Prior | Include file |
|---|---|---|---|
| Zone permeability | `PERM_<zone>` | `{{param:perm_prior}}` | `MULTIREG PERMX/PERMY/PERMZ` in `gridmult.inc` |
| Zone porosity | `PORO_<zone>` | `{{param:poro_prior}}` | `MULTIREG PORO` in `gridmult.inc` |
| kv/kh | `KVKH_MULT` | `{{param:kvkh_prior}}` | multiplies `PERMZ` in `gridmult.inc` |
| Fault transmissibility | `MULTFLT_<fault>` | `{{param:multflt_prior}}` | `MULTFLT` in `gridmult.inc` |
| Transmissibility between zones | `MULTREGT_<zone>_<zone>` | `{{param:multregt_prior}}` | `MULTREGT` in `multregt.inc` |
| Relative permeability exponent | `KRW_EXP`, `KROW_EXP` | `{{param:krw_exp_prior}}`, `{{param:krow_exp_prior}}` | power transform of the SWOF columns in `sattab.inc` |
| Contact shift | one per contact group | `{{param:contact_prior}}` | OWC depth in `equil.inc` |
| Aquifer strength | `AQ_STRENGTH` | `{{param:aquifer_prior}}` | volume and PI in `aqufetp.inc` |

The ranges are a starting point. They are not a site standard. Each range needs a reason in the uncertainty register.

## Files

| File | Use |
|---|---|
| `ert/prior.ert` | Prior ensemble config: case, runpath, queue. Includes `model.ert`. |
| `ert/model.ert` | Model part: GEN_KW, forward model, summary keys. `02-ahm-esmda-qc` includes it too. |
| `ert/design.yml` | Zones, faults, zone pairs, contact groups, aquifer, and priors. |
| `ert/jobs/PRIOR_PARAMS` | ERT job config for `bin/prior_design.py apply`. |
| `bin/prior_design.py` | Writes `ert/prior.dist`, and the include files in each realization. Needs PyYAML. |
| `bin/make_obs.py` | Observation builder from a reference case, with the shut-well skip. Needs res2df. |
| `obs/obs_spec.yml` | Wells, dates, vectors, and the error rule of each vector for `make_obs.py`. |

The deck must include the files that `apply` writes, in `<runpath>/include/ert/`: `gridmult.inc` and
`multregt.inc` in GRID or EDIT, `sattab.inc` in PROPS instead of the original table,
`equil.inc` in SOLUTION instead of the original EQUIL, and `aqufetp.inc` in SOLUTION.
`AQUANCON` stays in the deck. `apply` does not write it.

`make_obs.py` skips a point when the well has no flow at that date. The BHP or the rate of a shut well carries
no information about the reservoir, and it adds misfit that the update cannot reduce.
For field history, set `value_suffix: H` (it reads `WOPRH` and the other history vectors) and `noise_seed: null`.
Noise is for a synthetic truth only.

## How to run at your site

Activate Komodo in a `{{site:activate_shell}}` shell:

```
{{site:activate}}
```

Write the distribution file and the observations:

```
python bin/prior_design.py dist
python bin/make_obs.py <reference case .DATA> --out obs/history.obs
```

Queue: `{{site:queue.system}}`, queue `{{site:queue.name}}`, at most {{site:queue.max_running}} realizations at a time.
Runpaths go to `{{site:scratch_root}}/{{param:case_name}}`.
The queue lines in `ert/prior.ert` come from the site contract. Do not write a queue name or a resource string
into the config by hand. NUM_CPU above 1 means MPI for FLOW. Do not give FLOW a `--threads` argument.

Run one realization first, then the ensemble:

```
{{site:ert.executable}} test_run ert/prior.ert
{{site:ert.executable}} ensemble_experiment ert/prior.ert
```

## Checks before you run

```
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/prior_qc.py ert/prior.dist --realizations {{param:num_realizations}}
{{site:python}} {{param:agent_root}}/skills/ert-config-author-lint/scripts/ert_config_map.py ert/prior.ert
{{site:python}} {{param:agent_root}}/skills/ert-config-author-lint/scripts/check_ert_config.py --verbose ert/prior.ert
{{site:python}} {{param:agent_root}}/skills/history-match-qc/scripts/obs_qc.py obs/history.obs
```

After the test run:

```
{{site:python}} {{param:agent_root}}/skills/ert-diagnose/scripts/triage_runpath.py {{site:scratch_root}}/{{param:case_name}} --iter 0
{{site:python}} {{param:agent_root}}/skills/deck-scal-pvt-qc/scripts/deck_qc.py --deck {{site:scratch_root}}/{{param:case_name}}/realization-0/iter-0/<ECLBASE>.DATA
```

`ert lint` loads no plugins and always exits 0. Use `check_ert_config.py` as the gate.
`prior_qc.py` reports fewer realizations than parameters. Count the parameters in `ert/prior.dist`.

## What the Drogon run showed

Drogon (public OPM deck), 20 GEN_KW parameters, 16 realizations, MAX_RUNNING 3. Full numbers and sources are in
`docs/drogon-everest-report.md`, sections 2 and 3.

- `prior_qc.py` flagged N = 16 < 20 parameters before anything ran. That warning was correct: the unlocalized ES-MDA
  posterior collapsed (18 of 20 parameters with sd ratio < 0.2).
- `obs_qc.py` did not flag 20 of 200 observations taken on shut-in wells; a plot did. That is fixed in PR #14 (G9).
  Run obs_qc on the regenerated observations before the AHM.
- `schedule_qc.py` passed a SCHEDULE kept inside an INCLUDE without checking it (G1, fixed in PR #14).
  If your deck keeps the schedule in includes, check that the report lists the include files.
- Prior misfit was 45.8 (9.46-129) against 0.88 for the truth run. That spread is enough for ES-MDA to work with.

Takeaway for a field case: use N at least 3-5 times the number of parameters you actually expect the data to inform,
or cut parameters (on Drogon the six fault multipliers carried little signal).
