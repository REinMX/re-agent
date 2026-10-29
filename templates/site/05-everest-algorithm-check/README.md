# {{param:case_name}}: Everest algorithm check on a known answer

## What it is

A small case with a known answer: two controls (the constant water rates of {{param:injectors}}) and
{{param:used_realizations}} realizations. A brute-force scan of a grid gives the reference optimum. Three Everest
configs solve the same problem with three algorithms. Use it to check an algorithm, a version pin, or a
forward model before a large optimization.

| Config | Algorithm | Kind | Cost (simulations, at most) |
|---|---|---|---|
| `algo_optpp` | `optpp_q_newton` | gradient (stochastic, perturbations) | max_batch_num x (perturbation_num + 1) x realizations = {{param:optpp_batches}} x 2 x {{param:used_realizations}} |
| `algo_cobyla` | `scipy/COBYLA` | derivative-free, one evaluation per batch | max_batch_num x realizations = {{param:cobyla_batches}} x {{param:used_realizations}} |
| `algo_slsqp` | `scipy/SLSQP` | gradient (stochastic, perturbations) | max_batch_num x (perturbation_num + 1) x realizations = {{param:slsqp_batches}} x 2 x {{param:used_realizations}} |
| `bin/scan.py` | grid | brute force | len(scan_rates)^2 x realizations |

Known traps on this ERT/ropt pin:

- `scipy/SLSQP` stalled at the initial guess in earlier runs on this pin, and `everest_lint.py` still warns.
  On Drogon (below) it reached the scan optimum with the fewest simulations, so the stall depends on the config.
  Run `everest_diagnostics` on the result: it reports a stall.
- COBYLA uses no gradient. Its cost is batches x realizations; `perturbation_num` does not apply.
  `everest_cost.py --config` ignores it for COBYLA. Without a config, give `--perturbation-num 0`.
- A gradient run with a perturbation that is too small for the NPV noise can stop early. Compare the optimum
  of each algorithm with the scan before you trust it.

The default `jobs: custom` preserves the report-step NPV used by the reference scan and the historical
results below. The generator also accepts `jobs: everest_models`. The scan requires `custom` so that
it cannot silently compare different NPV definitions. Templates 03 and 04 describe the maintained jobs.

## Files

| File | Use |
|---|---|
| `case.yml` | The two-control case, the three experiments, and the scan grid. |
| `bin/scan.py` | Runs the forward model on the grid and writes `scan.csv` with the mean NPV of each point. |

The job and the config generator are in `{{param:well_control_dir}}` (render `03-everest-robust-well-control` first).

## How to run at your site

Activate Komodo in a `{{site:activate_shell}}` shell:

```
{{site:activate}}
```

Link the posterior through the gate (see the README of `03-everest-robust-well-control`), then write the configs:

```
{{site:python}} {{param:well_control_dir}}/bin/make_configs.py case.yml --posterior-link posterior_link.yml
```

Queue: `{{site:queue.system}}`, queue `{{site:queue.name}}`, at most {{site:queue.max_running}} simulations at a time.
Simulations go to `{{site:scratch_root}}/everest/{{param:case_name}}`.

```
{{site:everest_executable}} run everest/algo_optpp.yml
{{site:everest_executable}} run everest/algo_cobyla.yml
{{site:everest_executable}} run everest/algo_slsqp.yml
```

The scan runs the simulator on this host, not on the queue:

```
{{site:python}} bin/scan.py --out {{site:scratch_root}}/everest/{{param:case_name}}/scan --posterior-link posterior_link.yml --workers 3
```

## Checks before you run

```
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/algo_optpp.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/algo_cobyla.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/algo_slsqp.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_cost.py --config everest/algo_optpp.yml --sim-hours <hours of one simulation>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_cost.py --batches {{param:cobyla_batches}} --perturbation-num 0 --realizations {{param:used_realizations}} --sim-hours <hours of one simulation>
```

After the runs, read each result with `everest_diagnostics.py` and compare the final controls and the mean NPV
with the best point and its neighbours in `scan.csv`.

## What the Drogon run showed

Drogon, 2 controls (A5 and A6 injection, 1000-12000 sm3/d), 4 D1 posterior realizations, same start point (6500, 6500), 1443.8 MUSD (truth 1418.1). Sources:
`docs/drogon-everest-report.md` section 4.1.

| Algorithm | Simulations | Wall time | Best mean NPV, 4 members (MUSD) | Truth NPV at that point |
|---|---|---|---|---|
| Scan, 5 x 5 grid | 100 | 2839 s | 1618.7 at (1000, 12000) | 1622.9 |
| SLSQP | 16 | 407 s | 1618.7 at (1000, 12000) | 1622.9 |
| optpp_q_newton | 36 | 1757 s | 1605.5 at (1000, 8109) | 1613.7 |
| COBYLA | 44 | 2440 s | 1522.7 at (11999, 1001) | 1470.1 |

- SLSQP found the scan optimum, with the fewest simulations.
- COBYLA went to the opposite corner. Its trial points left the bounds (G19, flagged by `everest_diagnostics` in PR #18).
- The objective is discontinuous where Flow shuts a well ("can not operate"): a 53 MUSD jump between neighbouring
  points (G16). Flow is deterministic, so the cause is the well shut, not noise.
  `flow_log_diagnose.py` (PR #19) reports the shut wells. Check it before trusting a gradient.
- `everest_cost.py` overestimated the simulation count for every algorithm (G21, PR #20): treat its number as an upper bound.

Run the scan first on a real case only when there are at most 2 or 3 controls. Otherwise compare algorithms on a reduced problem.
