"""Patch the upstream Drogon decks so ERT-generated include files carry the uncertain parameters.

Usage: python tools/make_deck.py   (writes deck/model/DROGON_AHM.DATA and DROGON_PRED.DATA, idempotent)
"""

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
UP = HERE / "deck" / "upstream"
OUT = HERE / "deck"

COMMON = [
    ("FAULTDIM\n 500 /", "FAULTDIM\n 500 /\n\nAQUDIMS\n 1* 1* 1* 1* 1 4000 /"),
    ("'../include/grid/drogon.multregt' /", "'../include/ert/multregt.inc' /"),
    (
        "'../include/grid/drogon.multnum' / --exported by rms",
        "'../include/grid/drogon.multnum' / --exported by rms\n\nINCLUDE\n '../include/ert/gridmult.inc' /\n\nINCLUDE\n '../include/ert/aquancon.inc' /",
    ),
    ("'../include/props/drogon.sattab' /", "'../include/ert/sattab.inc' /"),
]
HIST = COMMON + [
    (
        " '../include/solution/drogon.equil' / --exported by rms",
        " '../include/ert/equil.inc' /\n\nINCLUDE\n '../include/ert/aqufetp.inc' /",
    ),
]
PRED = COMMON + [
    (
        "RESTART\n 'DROGON_HIST'  82 / -- read from restart file, defined by ert",
        "RESTART\n '../HIST/DROGON'  82 /\n\nINCLUDE\n '../include/ert/aqufetp.inc' /",
    ),
    ("'../include_pred/schedule/drogon_pred.sch' /", "'../pred.sch' /"),
]


def patch(text: str, edits) -> str:
    for old, new in edits:
        if text.count(old) != 1:
            raise SystemExit(f"expected exactly one match for {old!r}, found {text.count(old)}")
        text = text.replace(old, new)
    return text


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    global UP, OUT
    UP = args.case / "deck" / "upstream"
    OUT = args.case / "deck"
    (OUT / "model").mkdir(parents=True, exist_ok=True)
    for name, edits in (("DROGON_HIST", HIST), ("DROGON_PRED", PRED)):
        src = (UP / "model" / f"{name}.DATA").read_text()
        dst = OUT / "model" / name.replace("HIST", "AHM").replace("PRED", "PRED")
        dst.with_suffix(".DATA").write_text(patch(src, edits))
    inc = OUT / "include"
    if inc.exists():
        shutil.rmtree(inc)
    shutil.copytree(UP / "include", inc)
    shutil.copytree(UP / "include_pred", OUT / "include_pred", dirs_exist_ok=True)
    (inc / "ert").mkdir()
    print("wrote", OUT / "model")


if __name__ == "__main__":
    main()
