Drogon-lite fixtures. Drogon-lite is a synthetic FMU model (github.com/REinMX/drogon-lite). No field data.

- `coverage_obs.csv`, `coverage_posterior_sim.csv.gz`: observations (with the noise-free truth response) and the
  simulated values of one ES-MDA posterior, 50 members, seed 123456, field-carries-perm, radius 500 m.
- `comp_base.csv.gz`, `comp_screened.csv.gz`, `comp_*.dist`, `comp_truth.txt`: GEN_KW parameters of the prior
  (ITER 0) and the ES-MDA posterior (ITER 3) of the base and screened setups, 100 members, seeds 1001, 123456, 2002,
  with their priors and the synthetic truth.
- `screen_*`: the one-by-one screening of the full-physics model. The design (37 runs, P10 and P90 of 18
  parameters), the simulated values of its 306 summary observations, their values and errors, the priors, and the
  AHM_ANALYSIS influence table (ahm_influence.py) of the prior ensemble.
