# {{param:case_name}}: drill order with Everest

## What it is

Optimization of the order in which new wells are drilled. Each new well has a priority control between 0 and 1.
The default `jobs: everest_models` uses `drill_planner`. The generator represents each fixed completion
slot as a one-day surrogate drilling window. This preserves the existing completion dates, not physical
drilling durations. The highest priority gets the earliest window. Rig slots: {{param:rig_slots}}.
The adapter rejects missing wells or dates outside these distinct slots. Review tied priorities separately.
Use `--set jobs=custom` when rendering to retain the original priority-sort writer.

The NPV includes the well cost: each well costs {{param:well_cost}}, discounted from its drill date at
{{param:discount_rate}} per year. A later drill date makes the well cheaper in present value and delays its oil.
`npv` reads the same `drill_dates.json` as the schedule adapter and discounts the well cost from its ready date.
It uses daily discounted cash flows, while `custom` uses report-step endpoint cash flows.
Maintained jobs do not remove the piecewise-constant priority objective or its zero-gradient problem.

### Large perturbations

A priority changes the result only when it crosses the priority of another well. A small perturbation
(1-10 % of the range, the usual advice) seldom changes the order. The gradient estimate is then zero and the
optimizer stops at the initial guess. This config uses a perturbation of {{param:drill_perturbation}} on the range 0-1 and
{{param:perturbation_num}} perturbations for each realization. `everest_lint.py` reports the large perturbation as a warning.
That warning is expected here. The objective is a step function of the controls: read the result as a
ranking of orders, not as a smooth optimum. Check the best order against a direct run of the two or three best orders.

## Files

| File | Use |
|---|---|
| `case.yml` | Job selection, deck, posterior, wells, fixed rig slots, prices and experiment. |
| `everest/everest_models/` | Generated `drill.yml`, well records, injection constraints and NPV prices. |
| `wells/` | One WELSPECS/COMPDAT file for each new well. You supply them. See `wells/README.md`. |

The job and the config generator are in `{{param:well_control_dir}}` (render `03-everest-robust-well-control` first).

## How to run at your site

Activate Komodo in a `{{site:activate_shell}}` shell:

```
{{site:activate}}
```

Link the posterior through the gate (see the README of `03-everest-robust-well-control`), then write the config:

```
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/ahm_posterior_link.py \
  --runpath '{{site:scratch_root}}/{{param:ahm_case}}/realization-*/iter-{{param:ahm_last_iter}}' \
  --qc {{site:output_root}}/{{param:ahm_case}}/qc-post/misfit/manifest.json \
  --data-source '{{site:scratch_root}}/{{param:ahm_case}}/realization-<REALIZATION_ID>/iter-{{param:ahm_last_iter}}' \
  --target posterior --out posterior_link.yml
{{site:python}} {{param:well_control_dir}}/bin/make_configs.py case.yml --posterior-link posterior_link.yml
```

Queue: `{{site:queue.system}}`, queue `{{site:queue.name}}`, at most {{site:queue.max_running}} simulations at a time.
Simulations go to `{{site:scratch_root}}/everest/{{param:case_name}}`.

```
{{site:everest_executable}} run everest/drill_order.yml
```

## Checks before you run

```
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/drill_order.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_cost.py --config everest/drill_order.yml --sim-hours <hours of one simulation>
```

Cost: at most max_batch_num x (perturbation_num + 1) x realizations = {{param:max_batch_num}} x ({{param:perturbation_num}} + 1) x realizations simulations.
Read `drill_dates.json`, `drill_plan.json` and the schedule from a kept runpath.
The initial priorities must give the order that you expect. The generator exits 2 before writing configs
if the selected maintained jobs or executables are missing. See template 03 for the job selection table.
The historical Drogon results below used `custom`.

## What the Drogon run showed

Drogon E3: 4 new producers (OP5-OP8), 4 rig slots, 25 MUSD per well, 8 D1 posterior members, optpp_q_newton with 2
perturbations of 0.25, 6 batches: 80 simulations in 2415 s. Sources: `docs/drogon-everest-report.md` section 4.7.

| | Order | Posterior mean NPV (MUSD) | Truth NPV |
|---|---|---|---|
| Start | OP5, OP6, OP7, OP8 | 1425.4 | 1372.0 |
| Best (batch 3) | OP8, OP7, OP5, OP6 | 1487.9 (+62.5) | 1409.6 (+37.6) |

- The gain on the truth is about 60% of what the posterior predicted.
- A perturbation of 0.25 (a quarter of the range) was large enough to reach 12 different orders. Smaller perturbations
  give zero gradients.
- Two priorities reached the same bound (OP7 = OP8 = 1.0 at batch 5). `well_control.py` breaks ties by well
  name, so the tied wells cannot be separated by the optimizer. Run the best order and its neighbours directly.
- At the base rates, the best order exceeds a 15,000 sm3/d field water limit on the truth (17,400). Check the order
  together with rate controls and constraints before you use it.
