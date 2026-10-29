### Added
- `check_ert_config.py --ahm` raises a review item when adaptive localization uses a `LOCALIZATION_CORRELATION_THRESHOLD` below 2/sqrt(N), the sampling noise of a correlation with N realizations. The message gives ERT's default, 3/sqrt(N). Works for the ERT 26 `PARAMETERS ... ADAPTIVE` lines and the older `LOCALIZATION True` switch.
