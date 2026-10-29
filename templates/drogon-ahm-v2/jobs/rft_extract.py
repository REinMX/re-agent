#!/usr/bin/env python
"""ERT forward model: write the pilot-well RFT pressures as GEN_DATA files rft_<WELL>_1.txt.

Picks the last RFT survey per well and four connections spread evenly by depth. The connection
set is fixed by the schedule, so every realization reports the same points in the same order.
Usage: rft_extract.py <ECLBASE>   (run from the runpath)
"""

import sys

from res2df import ResdataFiles, rft

RFT_WELLS = ["R_A2", "R_A3", "R_A4", "R_A5", "R_A6"]
POINTS_PER_WELL = 4


def main():
    df = rft.df(ResdataFiles(sys.argv[1] + ".DATA"))
    for well in RFT_WELLS:
        w = df[df.WELL == well]
        w = w[w.DATE == w.DATE.max()].sort_values("DEPTH")
        idx = [round(i * (len(w) - 1) / (POINTS_PER_WELL - 1)) for i in range(POINTS_PER_WELL)]
        rows = w.iloc[idx]
        with open(f"rft_{well}_1.txt", "w") as fh:
            fh.write("\n".join(f"{p:.4f}" for p in rows.PRESSURE) + "\n")
    print("wrote rft files for", ", ".join(RFT_WELLS))


if __name__ == "__main__":
    main()
