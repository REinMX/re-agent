# Licensing and provenance

This project is licensed under the GNU General Public License, version 3 or (at your option)
any later version (`GPL-3.0-or-later`). The full text is in `LICENSE`.

Third-party parts keep their own terms:

- `plugins/pstack/` is MIT licensed, Copyright (c) 2026 Lauren Tan. See
  `plugins/pstack/LICENSE` and `plugins/pstack/NOTICE.md`.
- `skills/flow-preflight/references/flow_keyword_support.json` holds facts extracted from
  OPM opm-simulators source files, which are GPL-3.0-or-later. Its `license_note` gives the
  attribution. No OPM source file is copied.
- Public datasets are referenced, not redistributed: the Drogon deck from OPM/opm-tests (ODbL),
  fmu-drogon (CC-BY-SA-4.0) and the Egg benchmark model. Only our own outputs and synthetic
  fixtures derived from them are in this repository, and they keep the upstream attribution.

Dependencies (ERT, Everest, res2df, resdata, pyscal and others) are used, not vendored, and keep
their own licenses.

`EXPORT_MANIFEST.json` in a prepared ZIP records the exact source commit, tree and file hashes.
