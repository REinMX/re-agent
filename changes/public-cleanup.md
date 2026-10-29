---
bump: fmu-drogon-conventions patch
---
### Changed
- The project is licensed GPL-3.0-or-later (`LICENSE`, `pyproject.toml`). `COPYING.md` states the licence and
  the third-party terms (pstack MIT, OPM-derived keyword facts, referenced public datasets).
- The company profile example is now `profiles/company.example.yml` with the profile key `company`
  (`--profile company`, `RE_AGENT_PROFILE company`). The template pack moved to `templates/site/`, and its
  READMEs say "How to run at your site". The QC chain test is now `tests/test_site_qc_chain.py`.
- Docs, skills, evals and tests no longer name a company. Links to public open-source repositories and the
  attributions that licences require stay.
- Personal home paths in docs and plans are `~/` placeholders. The Sumo eval prompt uses `/scratch/fmu/<user>/`.
- `docs/SITE_SETUP.md` is the short, neutral setup guide: agent host and execution host, install, site contract
  profiles, doctor, manual handoff.

### Added
- `tests/test_public_hygiene.py` fails on a company name, the company domain or a personal path in any tracked
  file, with file:line. The terms are rot13 in `tests/data/public_hygiene_terms.json`. CHANGELOG.md, LICENSE,
  open-source repository links and Copyright attribution lines are allowed. The template guard in
  `tests/test_render_template.py` reads the same terms.

### Removed
- The company source-copy, merge, transfer-kit and VS Code deployment guides, and the exporter projection for
  the transfer kit.
- The raw `evals/results/*/results.json` runner output is no longer tracked (git-ignored). `summary.md` and
  `NOTES.md` stay.
