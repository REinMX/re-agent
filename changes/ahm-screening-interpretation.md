---
bump: ahm-workflow patch, ahm-analysis patch
---
### Changed
- A dropped screening parameter is no longer told to become CONST by default. Historical non-identifiability does not remove forecast uncertainty. The run still refuses while that parameter remains sampled without an explicit decision.
- The KS floor is described as an independent-sample reference scale. A dominant observation group is a hypothesis, not proof of spurious correlation.
- The scalar ensemble-size warning is not a stop for a localized FIELD or SURFACE update.
