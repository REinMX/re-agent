# {{param:case_name}}: robust well control with Everest

## What it is

Robust optimization of well rates over the history-match posterior. Two Everest configs share one case file:

- `robust_mean`: maximize the mean NPV over the realizations.
- `robust_cvar`: maximize the CVaR of the NPV over the worst {{param:cvar_percentile}} fraction of the realizations.
  Use `cvar.percentile`. On this ERT/ropt pin `cvar.number_of_realizations` picks the BEST realizations,
  which is the opposite of a risk measure. `everest_lint.py` reports it as an error.

Controls:

- `inj`: the water rate of each injector in each period ({{param:periods}}). Everest names them `inj.<well>.<period>`.
- `prod`: the liquid rate of each controlled producer ({{param:controlled_producers}}).

Constraints:

- Linear input constraint: in each period, the sum of the injector rates is at most {{param:injection_capacity}} (field injection capacity).
- Output constraints: maximum field water production rate `max_fwpr` at most {{param:max_fwpr}},
  voidage replacement ratio `vrr` at least {{param:min_vrr}}.

Select `jobs` with `--set jobs=everest_models` or `--set jobs=custom` when rendering.
The default is `everest_models`. The generator checks the package's ERT plugin and its executables
before it writes configs. A missing job exits 2. Activate an environment with ERT and everest-models.

| Selection | Rate events | Drill dates | NPV |
|---|---|---|---|
| `everest_models` | `well_constraints` | `drill_planner` in template 04 | `npv` |
| `custom` | `well_control.py schedule` | Fixed slots sorted by priority | `well_control.py economics` |

The maintained jobs provide events and dates. `well_control.py model_schedule` writes the deck-specific
completions, LRAT/BHP limits and report dates from those outputs. Producer liquid controls remain in this
writer. `prepare` copies the deck and links the posterior; `simulate` runs it; `model_metrics` writes
`fopt_mm`, `max_fwpr`, `vrr` and converts the currency NPV to millions.
Wells, periods and signed cumulative-vector prices come from `case.yml`.
`04-everest-drill-order` and `05-everest-algorithm-check` share this generator and job.

The maintained NPV discounts daily blocked production. The custom NPV discounts each report-step cash flow
at its endpoint. Both use the prediction start as the reference date and a 365.25-day year.
The maintained job writes currency units, rounded to cents; the adapter converts them to millions afterward.
Do not compare an optimization with a reference scan that uses a different definition.
The historical Drogon results below used `custom`.

## Files

| File | Use |
|---|---|
| `case.yml` | Deck, posterior, schedule, wells, prices, controls, constraints, and experiments. |
| `bin/make_configs.py` | Checks required jobs and writes configs and job inputs into `everest/`. |
| `everest/everest_models/` | Generated NPV prices, injection constraints, well records and drill windows. |
| `jobs/well_control.py` | Deck adapter, simulator launcher and response extraction. Needs res2df. |

The prediction deck must restart from `{{param:restart_link}}` (relative to the runpath) and include
`{{param:schedule_file}}` for the controlled schedule. Its summary must have FOPT, FGPT, FWPT, FWIT, FWPR, FVPR, and FVIR.

## How to run at your site

Activate Komodo in a `{{site:activate_shell}}` shell:

```
{{site:activate}}
```

Link the posterior through the gate. The gate refuses a posterior that the history-match QC flags as collapsed:

```
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/ahm_posterior_link.py \
  --runpath '{{site:scratch_root}}/{{param:ahm_case}}/realization-*/iter-{{param:ahm_last_iter}}' \
  --qc {{site:output_root}}/{{param:ahm_case}}/qc-post/misfit/manifest.json \
  --data-source '{{site:scratch_root}}/{{param:ahm_case}}/realization-<REALIZATION_ID>/iter-{{param:ahm_last_iter}}' \
  --target posterior --out posterior_link.yml
{{site:python}} bin/make_configs.py case.yml --posterior-link posterior_link.yml
```

Each linked realization must have finished. Run `triage_runpath.py` on the posterior iteration first.

Queue: `{{site:queue.system}}`, queue `{{site:queue.name}}`, at most {{site:queue.max_running}} simulations at a time.
The generator takes these from `case.yml`, which takes them from the site contract.
Simulations go to `{{site:scratch_root}}/everest/{{param:case_name}}`.

```
{{site:everest_executable}} run everest/robust_mean.yml
{{site:everest_executable}} run everest/robust_cvar.yml
```

### Restarts and resubmits

ERT resubmits a failed realization into the same runpath, and a resumed suite reuses its simulation folders. Every
forward-model step must therefore be safe to run twice: `well_control.py prepare` replaces its own links and never
writes into the posterior. Do not mark an experiment finished from the `everest run` exit code alone. On Drogon,
Everest 23.0.1 exited 0 after "DAKOTA analysis failed". Check `logs/everest.log` for "Experiment failed" and check
that `experiment_results.csv` has at least `min_realizations_success` unperturbed rows per batch.

## Checks before you run

```
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/robust_mean.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_lint.py everest/robust_cvar.yml --contract <site contract>
{{site:python}} {{param:agent_root}}/skills/everest-config-author-lint/scripts/everest_cost.py --config everest/robust_mean.yml --sim-hours <hours of one simulation>
```

`everest_lint.py` runs `everest lint` when Everest is in the environment. Cost: at most
max_batch_num x (perturbation_num + 1) x realizations simulations for each config.

After a run:

```
{{site:python}} {{param:agent_root}}/skills/everest-diagnostics/scripts/everest_diagnostics.py --objectives <objectives table> --controls <controls table> --config everest/robust_cvar.yml --out <folder>
```

Compare the CVaR result with the mean result at the same controls before you choose one.

## What the Drogon run showed

Drogon, 12 controls (A5/A6 injection in 4 periods, 4 producer rates), 8 D1 posterior members, optpp_q_newton, 8
batches unless stated. Sources: `docs/drogon-everest-report.md` sections 4.4-4.9.

| Run | Objective | Sims / time | Posterior mean NPV, start → best (MUSD) | Truth NPV at best | Truth VRR |
|---|---|---|---|---|---|
| E1 | mean | 72 / 2303 s | 1453.3 → 1708.7 | 1724.7 | 0.72 |
| E4 | CVaR, percentile 0.25 | 40 / 1534 s | 1453.3 → 1602.1 (tail 1578.6) | 1585.3 | 0.73 |
| E2 | mean, with VRR ≥ 0.9 and FWPR ≤ 15000 | 128 / 3789 s | no feasible improvement | 1418.1 (start) | 0.92 |
| E5 | 0.75 NPV + 0.25 FOPT, 6 batches | 56 / 1824 s | 1453.3 → 1587.9 | 1597.2 | 0.80 |
| E1_d2 | mean, D2 (localized) posterior | 72 / 2371 s | 1362.1 → 1509.8 | 1555.3 | 0.65 |

Start on the truth: 1418.1 MUSD.

- Use `cvar: percentile`, not `number_of_realizations`. In ropt 0.27.1, `number_of_realizations` selected the best members.
- CVaR gradient batches only simulate the tail members (2 of 8 here). At the same batch budget, the mean result had the better tail
  (1657.4 against 1578.6). Give CVaR a budget in simulations, not in batches.
- Start the constrained case feasible with a margin. E2 started at VRR 0.916 against 0.9, left the feasible region,
  and never came back above the start. Read the "Best feasible batch" line of `everest_diagnostics` (PR #21), not the
  overall best.
- Unconstrained plans (E1, E4) broke VRR 0.9 on the truth. Do not hand on an unconstrained plan as a plan to execute.
- A weighted sum of two objectives that agree (NPV and oil) shows no tradeoff.
- The posterior decides the plan: on D2, A6 goes to its minimum, on D1 to its maximum, and the truth NPV differs by 169 MUSD.
  Fix the history match before you tune the optimizer.
- `prepare` is idempotent (G17). A resubmitted realization relinks its posterior instead of failing.
