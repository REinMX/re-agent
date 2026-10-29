Add one changes/<topic>.md fragment per change.
Use ### Added, Changed, Deprecated, Removed, Fixed, or Security headings.
Follow each heading with Markdown bullets, as they will appear in CHANGELOG.md.
Optional front matter lists skill bumps:
```
---
bump: history-match-qc minor, everest-diagnostics patch
---
```
Run `make release VERSION=x.y.z` to fold fragments and apply version bumps.
