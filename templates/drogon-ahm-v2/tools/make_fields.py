#!/usr/bin/env python
"""Generate seeded, zone-independent Gaussian log-permeability fields on the public grid."""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jobs"))
from drogon_v2_lib import write_grdecl  # noqa: E402


def gaussian_field(dims, zones, spacing, seed, lateral=750.0, vertical=5.0):
    """FFT Gaussian filter; population sd ln(3)/2, in log space."""
    if min(dims) <= 0 or min(spacing) <= 0:
        raise ValueError("dimensions and spacing must be positive")
    shape = tuple(reversed(dims))
    rng = np.random.default_rng(seed)
    kz, ky, kx = np.meshgrid(
        *(np.fft.fftfreq(n, d=d) * 2 * np.pi for n, d in zip(shape, reversed(spacing), strict=True)),
        indexing="ij",
    )
    filt = np.exp(-0.25 * ((kx * lateral) ** 2 + (ky * lateral) ** 2 + (kz * vertical) ** 2))
    out = np.zeros(np.prod(dims))
    for zone in sorted(set(zones) - {0}):
        cells = zones == zone
        field = np.fft.ifftn(np.fft.fftn(rng.standard_normal(shape)) * filt).real.ravel()
        scale = np.sqrt(np.mean(filt**2))
        if scale <= 0:
            raise ValueError("degenerate covariance")
        out[cells] = field[cells] / scale * np.log(3) / 2
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20261015)
    args = parser.parse_args(argv)
    static = dict(np.load(args.static))
    spacing = [float(np.nanmedian(static[k])) for k in ("dx", "dy", "dz")]
    args.out.mkdir(parents=True, exist_ok=True)
    for i in range(args.n):
        field = gaussian_field(static["dims"], static["multnum"], spacing, args.seed + i)
        (args.out / f"field_{i}.grdecl").write_text(write_grdecl("LNPERM", field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
