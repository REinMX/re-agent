---
bump: everest-config-author-lint patch
---
### Added
- Select maintained everest-models NPV, injection constraints and fixed-window drill planning in the Everest templates, with an explicit custom path and dependency preflight.
- Verify generated inputs and deck adapters against installed job schemas and CLIs.

### Changed
- Check maintained forward-model jobs through the everest-models ERT plugin even when real Everest lint is disabled.
- Keep the algorithm reference scan on its custom report-step economics definition and reject mismatched job selections.
