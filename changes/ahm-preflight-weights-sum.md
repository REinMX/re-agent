### Changed
- `ahm_preflight.py` shows the weights ERT will run with in its AHM-PRE-WEIGHTS note: w * sum(1/w), the rescaling in ERT 23 and ERT 26. Weights such as 4,2 or 4,2,1 stay ready. Negative, non-finite and nonnumeric weights are still refused.
