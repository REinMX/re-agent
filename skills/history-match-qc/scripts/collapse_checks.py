#!/usr/bin/env python3
"""Collapse and overfit checks for misfit_report.py. Standard library only. No command line.

misfit (mean of squared normalized residuals) vs chi-square:
    When the errors are right and the model can match the data, each squared normalized residual has a mean
    of about 1. For n independent observations the mean has a standard deviation of about sqrt(2/n).
    A mean misfit far below 1 - k*sqrt(2/n) suggests overfit, or errors that are too large.
    A mean misfit far above 1 + k*sqrt(2/n) means the errors do not explain the misses.
    Correlated errors make the band wider. Treat the band as a hint.

Spread-aware reference (posterior):
    A member misfit holds the ensemble spread too, not only the noise. If the truth behaves like one more
    member, a member misses the observation by noise plus two exchangeable draws, so the mean misfit is about
    1 + 2 s, where s = var over members of SIMULATED / ERROR^2 (ddof 1), averaged over the observations.
    The finding "misfit above the spread-aware reference" fires only above 1 + 2 s + k*sqrt(2/n).
    Above the noise-only band but inside this reference is context, not a finding: a well-calibrated wide
    ensemble sits there. Limiter: an over-dispersed posterior widens the reference and can hide misfit;
    coverage and collapse checks cover that case.

Std ratio noise floor:
    For two independent samples of size N from the same normal distribution, log(std ratio) has a standard
    deviation of about 1/sqrt(N - 1). So a ratio down to exp(-1.96/sqrt(N - 1)) can come from sampling noise
    alone (for N = 5 that is 0.38). The ensemble smoother with N <= number of parameters also reduces the spread
    by construction.

Trust gate:
    weak            N < min_realizations, or N <= number of uncertain parameters
    untrustworthy   both
    not limited     neither. This does not mean that the history match is good.
"""

from __future__ import annotations

import math


def chi2_band(n_obs: int, sigma: float = 3.0) -> list[float] | None:
    """The band of the mean squared normalized residual that noise alone explains."""
    if n_obs <= 0:
        return None
    half = sigma * math.sqrt(2.0 / n_obs)
    return [max(0.0, 1.0 - half), 1.0 + half]


def misfit_vs_chi2(
    mean_misfit: float | None, n_obs: int, sigma: float = 3.0, spread: float | None = None
) -> dict:
    """Mean misfit against the noise-only band (`verdict`) and, given the mean spread s, the spread-aware
    reference 1 + 2 s + sigma*sqrt(2/n) (`spread_verdict`). `verdict` keeps its old meaning."""
    band = chi2_band(n_obs, sigma)
    if band is None or mean_misfit is None or not math.isfinite(mean_misfit):
        mean_misfit = None
        verdict = "unavailable"
    elif mean_misfit < band[0]:
        verdict = "below the noise band"
    elif mean_misfit > band[1]:
        verdict = "above the noise band"
    else:
        verdict = "inside the noise band"
    usable = spread is not None and math.isfinite(spread) and spread >= 0 and band is not None
    spread_expected = 1.0 + 2.0 * spread if usable else None
    spread_upper = spread_expected + sigma * math.sqrt(2.0 / n_obs) if usable else None
    if not usable or mean_misfit is None:
        spread_verdict = "unavailable"
    elif mean_misfit > spread_upper:
        spread_verdict = "above the spread-aware reference"
    else:
        spread_verdict = "inside the spread-aware reference"
    return {
        "mean_misfit": mean_misfit,
        "observations": n_obs,
        "expected": 1.0,
        "band": band,
        "sigma": sigma,
        "verdict": verdict,
        "spread": spread if usable else None,
        "spread_expected": spread_expected,
        "spread_upper": spread_upper,
        "spread_verdict": spread_verdict,
    }


def std_ratio_noise_floor(n: int) -> float | None:
    """Smallest std ratio that sampling noise alone can give (about 95 %) for N members in each ensemble."""
    if n < 2:
        return None
    return math.exp(-1.96 / math.sqrt(n - 1))


def trust_gate(n: int, n_params: int, min_realizations: int = 20) -> dict:
    reasons = []
    small = n < min_realizations
    deficient = n_params > 0 and n <= n_params
    if small:
        reasons.append(f"N = {n} is below {min_realizations}")
    if deficient:
        reasons.append(f"N = {n} is not more than the {n_params} uncertain parameters (rank deficient)")
    level = "untrustworthy" if small and deficient else "weak" if reasons else "not limited by ensemble size"
    return {"level": level, "realizations": n, "parameters": n_params, "reasons": reasons}


def findings(gates: dict[str, dict], chi2: dict[str, dict], posterior_label: str = "posterior") -> list[dict]:
    """Findings in the format of misfit_report.py."""
    out = []
    for label, gate in gates.items():
        if gate["reasons"]:
            out.append(
                {
                    "ensemble": label,
                    "finding": f"ensemble size: {gate['level']}",
                    "detail": "; ".join(gate["reasons"])
                    + ". Do not claim convergence. Treat spread and shift statistics as weak",
                    "examples": [],
                }
            )
    post = chi2.get(posterior_label)
    if post and post["verdict"] == "below the noise band":
        out.append(
            {
                "ensemble": posterior_label,
                "finding": "misfit below the noise level",
                "detail": (
                    f"mean misfit {post['mean_misfit']:.3g} over {post['observations']} observations; noise alone"
                    f" gives {post['band'][0]:.3g}-{post['band'][1]:.3g}. Possible overfit, or errors too large"
                ),
                "examples": [],
            }
        )
    if post and post.get("spread_verdict") == "above the spread-aware reference":
        out.append(
            {
                "ensemble": posterior_label,
                "finding": "misfit above the spread-aware reference",
                "detail": (
                    f"mean misfit {post['mean_misfit']:.3g} over {post['observations']} observations is above"
                    f" {post['spread_upper']:.3g} = 1 + 2 s + {post['sigma']:g} sqrt(2/n), with mean spread"
                    f" s = {post['spread']:.3g}. The errors and the ensemble spread do not explain the misses"
                ),
                "examples": [],
            }
        )
    return out


def collapse_caveat(n: int, n_params: int) -> str:
    """Text to add to a 'parameter spread collapsed' finding."""
    floor = std_ratio_noise_floor(n)
    parts = []
    if floor is not None:
        parts.append(f"with N = {n}, sampling noise alone gives std ratios down to about {floor:.2f}")
    if n_params and n <= n_params:
        parts.append(f"N <= {n_params} parameters, so the smoother reduces the spread by construction")
    return ("Caveat: " + "; ".join(parts)) if parts else ""
