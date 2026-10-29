# Screening integrity and held-out AHM validation

## Scope

The implementation starts from `68c4a92879c6f0b55411798fbd83cd5f0d4daf2b`. It repairs screening evidence and adds retrospective forecast diagnostics. It changes no model inputs, observation values, solver flags or scientific acceptance thresholds. It starts no simulator runs.

The branch is `feature/ahm-integrity-heldout`. Delivery is local. A commit, push, release or site activation is not implied by a completed local check.

## Grounding

`screen_params.py` owns parameter-screening decisions. `ahm_preflight.py` owns readiness and prints a command only when every check passes. `long_run.py` calls that preflight for update runs. The prior parser already owns distribution syntax. The coverage reader already validates unique rectangular member-observation data. ERT remains the runner. Native simulator libraries remain the readers.

The parent reproduced missing predictions, conflicting duplicate predictions and stale sampled-prior acceptance through the actual CLI before implementation. A test-only worker added regression cases. The parent ran those cases independently and observed 20 failures and four passing controls. The original affected paths passed 272 tests before production edits.

## Design alternatives

| Candidate | Structure | Tradeoff |
|---|---|---|
| A | Extend the existing screening record. Add a narrow diagnostic scorer under history-match-qc. | Keeps existing ownership and limits migration. |
| B | Add shared immutable evidence types and migrate coverage to a common domain module. | Centralizes invariants but expands the change beyond the reproduced defects. |

A is the selected base. The implementation must reuse existing parsers and strict validation, rather than copy readers. B contributes explicit retrospective-provenance limits and physical response identity for leakage checks. A separate read-only judge checks the selection. This is independent context review, not diverse-model evidence.

## Screening data shape

Schema `ahm-screening/2` keeps the existing parameter decisions and adds the scientific inputs that justify them. Those inputs include normalized prior definitions, source-file digests and explicit member and observation inventories.

The design table supplies the complete member inventory, including reference members that are not sensitivity endpoints. An independently supplied observation inventory supplies the complete observation domain. A record without that inventory may describe a diagnostic, but must not establish readiness.

Validate the complete domain before statistics:

- Member IDs are unique nonnegative integers.
- Every member has exactly one prediction for every expected observation.
- Required numeric values and computed results are finite.
- Observation errors are strictly positive.
- Observation metadata is consistent across members. STATUS may differ because extraction is member-specific.
- A missing response never becomes zero sensitivity.
- A duplicate never selects its first row silently.

Preflight compares normalized sampled prior definitions, not prior file bytes. Comments, number spelling, file order and path changes do not alter a prior. A changed sampled prior does alter it and requires new screening. A dropped parameter may disappear or become CONST. Old records require regeneration rather than an automatic upgrade to trusted evidence.

Evidence content changes invalidate the record. Relative references permit relocation of a complete input bundle. Missing or malformed evidence produces a clear refusal, not a traceback or a command.

## Held-out data shape

A versioned split manifest binds evaluation responses to the actual assimilation observation file. It declares a history cutoff, expected ensemble member IDs, physical observation identities, dates, units, reference kinds, error assumptions and source digests.

History and held-out identities must be disjoint by response key and date, not merely by observation name. This prevents a rename from hiding overlap. Temporal held-out dates follow the history cutoff. The scorer checks the declared history inventory against the actual parsed assimilation observations. Unsupported observation forms are explicit errors in this initial implementation.

Reference kinds remain separate:

- A noisy observation is scored against the ensemble plus its declared Gaussian measurement noise.
- A latent synthetic truth is scored against the noise-free ensemble.

Report bias, RMS, coverage, 90% interval width and CRPS per key in native units. Do not pool incompatible units. Normalized RMS can be compared across keys only with its error assumptions stated. New scores are diagnostics. They do not introduce an acceptance threshold or replace an existing gate.

A retrospective check of current input files cannot establish execution-time independence. Report that limit. Do not label an old run pre-registered merely because its manifest is created now.

## Verification sequence

1. Preserve the observed RED regression evidence.
2. Make invalid screening inputs fail closed while valid numerical results stay unchanged.
3. Verify stale sampled priors refuse readiness and dropped-to-CONST conversion remains valid.
4. Test scoring against analytic latent and Gaussian cases, row/member permutations and leakage adversaries.
5. Exercise the portable CLI with saved public MODEL1 and MODEL6 ensembles.
6. Run affected scientific tests, the lean suite, static checks and a copied-install smoke.
7. Review the final diff independently. Record any environment-specific or skipped gate.

Use `~/projects/ert_fmu/.venv/bin/python` for scientific checks and remove inherited PYTHONPATH and PYTHONHOME. The lean repository environment lacks scientific dependencies. ERT 26 compatibility is not established by running under the installed ERT 23.0.1. The known pre-existing localization-warning test must remain disclosed rather than being changed to make an unrelated gate green.

## Native evidence

The native oracle is the existing public-model lab at `53eb55e854e5749b18186dec6bfc1caef54c7cfc`. Its saved MODEL1 verdict is PASS and its saved MODEL6 verdict is FAIL. Each case has 50 members in four ES-MDA ensembles.

The verification exporter uses the existing lab `read_ensemble` function and resdata units. It does not implement another simulator reader. It reads exact report dates and hashes the native summary, PRT, parameters and completion markers before and after export. It writes only a new report directory outside both source checkouts.

The exported history references use the actual rounded values in the assimilation observation file. The older lab QC uses the full-precision observation table. Keep that distinction visible. Recompute the old QC separately and preserve its complete result instead of claiming those differently defined history metrics are identical.

MODEL6 is a mandatory negative control. Its improved history fit does not authorize forecast acceptance. No gate or observation may change to remove its FAIL.
