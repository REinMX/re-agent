# GitHub source handoff — local verification refresh

## Scope

This prepares the existing private `REinMX/reservoir-engineer-agent` source for a reviewed copy into
an approved company-managed account. It does not certify a company deployment, transfer repository
ownership, add a license, import field data, or configure another account.

`docs/REVIEW_2026-10-04.md` is the earlier readiness record. Its test counts, publication state and
failed-review-provider notes are historical, not the final handoff status. GitHub CI and publication
status are recorded in the eventual PR/release, not inferred from that old report.

## Independently reproduced gaps and fixes

Two reviewers inspected an immutable source copy. Their fail-closed findings drove test-first fixes:

- The source exporter could follow local Git replacement objects while recording the original SHA.
  All Git reads now disable replacement objects; blob and commit replacement regressions verify
  canonical committed bytes. Windows-invalid names and casefold collisions, including implicit
  directories, are rejected before ZIP creation.
- The hook misclassified value-taking `nice`/`sudo` options. It now parses narrowly supported wrapper
  syntax or requests review for opaque syntax. This corrects classification without adding permissions
  or claiming that a shell classifier is a sandbox. (The hook and its gates were removed later.)
- Normal eval fixtures lacked fields required by the deployment contract. They now carry explicitly
  synthetic values and pass the contract validator; negative missing/unapproved-contract cases remain
  distinct. The synthetic approval flag is not authorization for a real model service.
- History-match reports accepted nonfinite parameter values/thresholds and overflowing derived statistics,
  and compared prior/posterior without native units. Inputs, outputs and serialized numeric fields are now
  validated before writes, with only explicitly undefined constant-prior ratios/partial-coverage cells
  nullable. Native units must be available and consistent across members/ensembles, even in partial mode.
  Observation values/errors are explicitly assumed to use those units; no conversion is invented.

Initial RED runs and focused GREEN checks were recorded during development. A second independent review
of the fixed candidate is required before publication; do not interpret this file as its verdict.

## Fresh local gates

On the local public-data FMU environment:

- `make check-full PY=~/projects/ert_fmu/.venv/bin/python`: **577 passed**, no skips,
  31 upstream warnings; exit 0. Ruff lint/format and all 12 skill checks passed.
- `make check PY=~/reservoir-engineer-agent/.venv/bin/python`: **377 passed, 20 skipped**;
  exit 0. This lean environment lacks reservoir dependencies and is not native/site coverage.
- `git diff --check`: passed after formatting the fixture/test updates.

The full suite originally had five success-expectation failures after numeric hardening because upstream
Reek realization 0 contains the deliberate parameter sentinel `FOO NaN`. Positive native tests now use a
session-local copied fixture with only that exact sentinel removed. Regression tests verify identical
file membership and all other bytes, retain all five realization identities/native summary files, and
reject an upstream-style NaN parameter even with `--allow-partial`. No upstream fixture, simulator output,
scientific threshold or production validation rule was weakened or modified to make tests green.

A prior roundtrip candidate passed export/hash verification, extracted-source full gates, scoped copy
installation and an idempotent second installation. Those checks must be repeated against the final
committed ZIP; previous package evidence is not proof of the final release.

## Known boundaries

- `AGENTS.md` remains unchanged: its separate protected-file safeguard timed out. The transfer guide
  documents checkout-link versus portable-placeholder behavior and conditional STE-linter availability.
- `COPYING.md` grants no new license. Obtain owner/site redistribution decisions before corporate import.
- The ZIP excludes history and known runtime/sensitive paths, not every possible confidential string.
  Review its exact content and use the approved import channel. Checksums are not authentication or DLP.
- A checksum-writing I/O failure can leave an incomplete delivery pair. Do not use failed deliveries;
  the exporter does not delete an incomplete archive automatically.
- Native Windows/actual IDE enforcement, internal Komodo/plugins, queues/licenses, Sumo and endpoint
  authorization require site validation. GitHub's lean cross-platform matrix does not provide those.
