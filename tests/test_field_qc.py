"""field_qc.py on tiny synthetic GRDECL files (4x3x2 = 24 cells)."""

import contextlib
import importlib.util
import io
import json
import math
import statistics
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "skills/history-match-qc/scripts/field_qc.py"
spec = importlib.util.spec_from_file_location("field_qc_t", SCRIPT)
field_qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(field_qc)

CELLS = 24


def grdecl(values: list[float], keyword: str = "PERMX") -> str:
    body = "\n".join(" ".join(f"{v:g}" for v in values[i : i + 6]) for i in range(0, len(values), 6))
    return f"-- synthetic\n{keyword}\n{body}\n/\n"


def ensemble(folder: Path, members: list[list[float]], keyword: str = "PERMX") -> str:
    folder.mkdir(parents=True)
    for real, values in enumerate(members):
        (folder / f"field-{real}.grdecl").write_text(grdecl(values, keyword))
    return str(folder / "field-*.grdecl")


def prior_members(n: int = 5) -> list[list[float]]:
    """Each cell: 100, 200, ... over the realizations, so the prior sd is the same in every cell."""
    return [[100.0 * (real + 1)] * CELLS for real in range(n)]


def posterior_members(collapsed: int, outside: int, n: int = 5) -> list[list[float]]:
    """The first `collapsed` cells have a tiny spread. The first `outside` cells have a mean of 900."""
    members = []
    for real in range(n):
        row = []
        for cell in range(CELLS):
            centre = 900.0 if cell < outside else 300.0
            step = 1.0 if cell < collapsed else 100.0
            row.append(centre + step * (real - (n - 1) / 2))
        members.append(row)
    return members


def run(*args: str) -> tuple[int, dict]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = field_qc.main([*args, "--json"])
    text = buffer.getvalue()
    return code, json.loads(text) if text.strip() else {}


def codes(report: dict) -> list[str]:
    return [f["code"] for f in report["findings"]]


def test_parse_repeat_counts_comments_and_terminator(tmp_path):
    path = tmp_path / "a.grdecl"
    path.write_text("-- header\nPERMX -- unit mD\n 3*100 2*0.5D1\n 1.5 /\nACTNUM\n4*1 0 1/\n")
    keywords = field_qc.parse_grdecl(path)
    assert keywords == {"PERMX": [100.0, 100.0, 100.0, 5.0, 5.0, 1.5], "ACTNUM": [1, 1, 1, 1, 0, 1]}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("PERMX\n1 2 3\n", "no '/' terminator"),
        ("PERMX\n1 x /\n", "not a number"),
        ("-- nothing\n", "no keyword"),
    ],
)
def test_parse_errors(tmp_path, text, message):
    path = tmp_path / "bad.grdecl"
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        field_qc.parse_grdecl(path)


def test_collapse_and_outside_fire(tmp_path):
    prior = ensemble(tmp_path / "prior", prior_members())
    post = ensemble(tmp_path / "post", posterior_members(collapsed=18, outside=2))
    code, report = run("--prior", prior, "--posterior", post)
    assert code == 0
    assert report["active_cells"] == 24 and report["collapsed_cells"] == 18 and report["outside_cells"] == 2
    assert codes(report) == ["HMQC-FIELD-COLLAPSE", "HMQC-FIELD-OUTSIDE"]
    collapse, outside = report["findings"]
    assert collapse["detail"].startswith(
        "18 of 24 active cells with prior spread (75.0%) have posterior sd / prior sd < 0.2"
    )
    assert "more than 50%" in collapse["detail"] and "Limiter" in collapse["detail"]
    assert "floor of the ratio 0.38" in collapse["detail"]  # N = 5
    assert outside["detail"].startswith("2 of 24 active cells (8.3%) have a posterior mean outside")
    assert "2/(N+1) = 33%" in outside["detail"]


def test_no_finding_below_thresholds(tmp_path):
    prior = ensemble(tmp_path / "prior", prior_members())
    post = ensemble(tmp_path / "post", posterior_members(collapsed=12, outside=0))
    code, report = run("--prior", prior, "--posterior", post)
    assert code == 0 and report["collapsed_cells"] == 12
    assert codes(report) == []


def test_actnum_removes_inactive_cells(tmp_path):
    prior = ensemble(tmp_path / "prior", prior_members())
    post = ensemble(tmp_path / "post", posterior_members(collapsed=18, outside=2))
    actnum = tmp_path / "ACTNUM.grdecl"
    actnum.write_text("ACTNUM\n" + "0 " * 16 + "8*1 /\n")  # only the last 8 cells are active
    code, report = run("--prior", prior, "--posterior", post, "--actnum", str(actnum))
    assert code == 0
    assert report["active_cells"] == 8 and report["collapsed_cells"] == 2 and report["outside_cells"] == 0
    assert codes(report) == []


def test_log_scale_and_positive_values(tmp_path):
    # Native sd ratio is 408.2 / 483.6 = 0.84: no collapse. In ln space the posterior spread (500-1500) is small
    # against the prior (1-1000): 0.456 / 2.974 = 0.15, so every cell collapses.
    prior_values, post_values = (1, 10, 100, 1000), (500, 1000, 1000, 1500)
    prior = ensemble(tmp_path / "prior", [[v] * CELLS for v in prior_values])
    post = ensemble(tmp_path / "post", [[v] * CELLS for v in post_values])
    native = run("--prior", prior, "--posterior", post)[1]
    assert native["scale"] == "native" and native["collapsed_cells"] == 0 and codes(native) == []
    assert native["sd_ratio"]["median"] == pytest.approx(
        statistics.stdev(post_values) / statistics.stdev(prior_values)
    )
    assert native["sd_ratio"]["median"] == pytest.approx(0.8442, abs=1e-4)
    report = run("--prior", prior, "--posterior", post, "--log")[1]
    assert report["scale"] == "ln" and report["collapsed_cells"] == 24
    assert codes(report) == ["HMQC-FIELD-COLLAPSE"]
    # ln(10**k) = k ln 10, so the prior ln sd is ln(10) * stdev(0, 1, 2, 3).
    assert report["sd_ratio"]["median"] == pytest.approx(
        statistics.stdev([math.log(v) for v in post_values]) / (math.log(10) * statistics.stdev([0, 1, 2, 3]))
    )
    assert report["sd_ratio"]["median"] == pytest.approx(0.1534, abs=1e-4)
    zero = ensemble(tmp_path / "zero", [[0.0] * CELLS, [1.0] * CELLS])
    assert field_qc.main(["--prior", zero, "--posterior", post, "--log"]) == 1


def test_median_sd_ratio_of_an_even_cell_count(tmp_path):
    # Two active cells with ratios 0.1 and 0.3: the median is 0.2, not the upper middle value 0.3.
    prior = ensemble(tmp_path / "prior", [[0.0, 0.0], [10.0, 10.0]])
    post = ensemble(tmp_path / "post", [[5.0, 5.0], [6.0, 8.0]])
    code, report = run("--prior", prior, "--posterior", post)
    assert code == 0 and report["active_cells"] == 2
    assert report["sd_ratio"]["min"] == pytest.approx(0.1) and report["sd_ratio"]["max"] == pytest.approx(0.3)
    assert report["sd_ratio"]["median"] == pytest.approx(0.2)


def test_outdir_writes_json_and_markdown(tmp_path):
    prior = ensemble(tmp_path / "prior", prior_members())
    post = ensemble(tmp_path / "post", posterior_members(collapsed=18, outside=2))
    out = tmp_path / "out"
    assert field_qc.main(["--prior", prior, "--posterior", post, "--outdir", str(out)]) == 0
    assert codes(json.loads((out / "field_qc.json").read_text())) == [
        "HMQC-FIELD-COLLAPSE",
        "HMQC-FIELD-OUTSIDE",
    ]
    assert "FINDING HMQC-FIELD-COLLAPSE" in (out / "field_qc.md").read_text()
    assert field_qc.main(["--prior", prior, "--posterior", post, "--outdir", str(out)]) == 2


def test_fail_closed_inputs(tmp_path):
    prior = ensemble(tmp_path / "prior", prior_members())
    assert field_qc.main(["--prior", str(tmp_path / "none-*.grdecl"), "--posterior", prior]) == 2
    one = ensemble(tmp_path / "one", prior_members(1))
    assert field_qc.main(["--prior", one, "--posterior", prior]) == 2
    short = ensemble(tmp_path / "short", [[1.0] * 23, [2.0] * 23])
    assert field_qc.main(["--prior", prior, "--posterior", short]) == 1
    actnum = tmp_path / "ACTNUM.grdecl"
    actnum.write_text("ACTNUM\n10*1 /\n")
    assert field_qc.main(["--prior", prior, "--posterior", prior, "--actnum", str(actnum)]) == 1
    poro = ensemble(tmp_path / "poro", prior_members(), keyword="PORO")
    assert field_qc.main(["--prior", prior, "--posterior", poro]) == 1


# ---------- --truth ----------
# Prior in each cell: 100, 200, 300, 400, 500. Mean 300, sd 158.11. Linear (type 7) percentiles of 5 values:
# P5 at rank 1 + 0.05 * 4 = 1.2 -> 100 + 0.2 * 100 = 120; P95 at rank 4.8 -> 400 + 0.8 * 100 = 480.
# Posterior in each cell: 480, 490, 500, 510, 520. Mean 500, sd 15.81, P5 482, P95 518.
TRUTH_PRIOR = [[100.0 * (real + 1)] * 5 for real in range(5)]
TRUTH_POST = [[480.0 + 10.0 * real] * 5 for real in range(5)]
TRUTH = [300.0, 450.0, 150.0, 300.0, 1e6]  # the last cell is inactive


def truth_case(tmp_path, posterior=TRUTH_POST, truth=TRUTH) -> list[str]:
    prior = ensemble(tmp_path / "prior", TRUTH_PRIOR)
    post = ensemble(tmp_path / "post", posterior)
    (tmp_path / "truth.grdecl").write_text(grdecl(truth))
    (tmp_path / "ACTNUM.grdecl").write_text("ACTNUM\n4*1 0 /\n")
    return ["--prior", prior, "--posterior", post, "--truth", str(tmp_path / "truth.grdecl"),
            "--actnum", str(tmp_path / "ACTNUM.grdecl")]  # fmt: skip


def truth_codes(report: dict) -> list[str]:
    return [code for code in codes(report) if "TRUTH" in code]


def test_truth_worse_and_coverage_fire(tmp_path):
    code, report = run(*truth_case(tmp_path))
    assert code == 0
    prior, post = report["truth"]["prior"], report["truth"]["posterior"]
    # Prior errors 0, 150, -150, 0: RMSE sqrt(45000 / 4) = 106.066. Posterior errors 200, 50, 350, 200:
    # RMSE sqrt(205000 / 4) = 226.385.
    assert prior["rmse"] == pytest.approx(106.066, abs=1e-3)
    assert post["rmse"] == pytest.approx(226.385, abs=1e-3)
    # The truth (150..450) is inside 120-480 in all 4 active cells, and inside 482-518 in none.
    assert prior["coverage_p5_p95"] == 1.0 and post["coverage_p5_p95"] == 0.0
    # Prior |z| = 0, 0.95, 0.95, 0. Posterior |z| = 12.6, 3.2, 22.1, 12.6: all above 3.
    assert prior["share_over_z"] == 0.0 and post["share_over_z"] == 1.0 and post["cells_over_z"] == 4
    assert truth_codes(report) == ["HMQC-FIELD-TRUTH-WORSE", "HMQC-FIELD-TRUTH-COVERAGE"]
    worse, coverage = [f for f in report["findings"] if "TRUTH" in f["code"]]
    assert "prior 106.066, posterior 226.385 (+113.4%; threshold: more than +2%)" in worse["detail"]
    assert "posterior 0.00" in coverage["detail"] and "below 0.8 and at least 0.1 below" in coverage["detail"]
    for item in (worse, coverage):
        assert "synthetic truth only" in item["detail"] and "one truth field" in item["detail"]
        assert "N = 5 prior and 5 posterior members are noisy" in item["detail"]


def test_truth_no_finding_when_the_posterior_moves_to_the_truth(tmp_path):
    # Posterior centred on the truth in each cell, sd 15.81: RMSE 0, coverage 1, no |z| above 3.
    posterior = [[t + 10.0 * (real - 2) for t in TRUTH] for real in range(5)]
    code, report = run(*truth_case(tmp_path, posterior=posterior))
    assert code == 0 and truth_codes(report) == []
    assert report["truth"]["posterior"]["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert report["truth"]["posterior"]["coverage_p5_p95"] == 1.0
    assert report["truth"]["posterior"]["share_over_z"] == 0.0


@pytest.mark.parametrize(
    ("prior", "post", "expected"),
    [
        ((100.0, 1.0), (101.9, 1.0), []),  # 1.9 % worse: below the 2 % threshold
        ((100.0, 1.0), (102.1, 1.0), ["HMQC-FIELD-TRUTH-WORSE"]),
        ((100.0, 0.85), (100.0, 0.79), []),  # below 0.8, but the drop 0.06 is below 0.1
        ((100.0, 0.9), (100.0, 0.8), []),  # a drop of 0.1, but 0.8 is not below 0.8
        ((100.0, 0.9), (100.0, 0.75), ["HMQC-FIELD-TRUTH-COVERAGE"]),
    ],
)
def test_truth_thresholds(prior, post, expected):
    def stats(rmse, coverage):
        return {"rmse": rmse, "coverage_p5_p95": coverage, "share_over_z": 0.0, "cells_over_z": 0}

    found = field_qc.truth_findings(stats(*prior), stats(*post), 100, 100, "ln", field_qc.TRUTH_LIMITS)
    assert [f["code"] for f in found] == expected


def test_truth_log_scale(tmp_path):
    # ln prior = k ln 10 (k = 0..4), mean 2 ln 10 = ln 100 = the truth: RMSE 0. ln posterior = ln 1000 + 0.1 (k - 2):
    # mean ln 1000, so the error is ln 10 = 2.302585 in each cell. The prior P5-P95 (0.2 ln10, 3.8 ln10) holds ln 100.
    prior = ensemble(tmp_path / "prior", [[10.0**k] * 3 for k in range(5)])
    post = ensemble(tmp_path / "post", [[1000.0 * math.exp(0.1 * (k - 2))] * 3 for k in range(5)])
    truth = tmp_path / "truth.grdecl"
    truth.write_text(grdecl([100.0] * 3))
    code, report = run("--prior", prior, "--posterior", post, "--truth", str(truth), "--log")
    assert code == 0 and report["scale"] == "ln"
    assert report["truth"]["prior"]["rmse"] == pytest.approx(0.0, abs=1e-12)
    assert report["truth"]["posterior"]["rmse"] == pytest.approx(math.log(10))
    assert report["truth"]["prior"]["coverage_p5_p95"] == 1.0
    assert report["truth"]["posterior"]["coverage_p5_p95"] == 0.0
    assert truth_codes(report) == ["HMQC-FIELD-TRUTH-WORSE", "HMQC-FIELD-TRUTH-COVERAGE"]
    truth.write_text(grdecl([100.0, 0.0, 100.0]))
    assert field_qc.main(["--prior", prior, "--posterior", post, "--truth", str(truth), "--log"]) == 1


def test_truth_markdown_and_fail_closed(tmp_path):
    args = truth_case(tmp_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert field_qc.main(args) == 0
    text = buffer.getvalue()
    assert "| RMSE of mean vs truth, prior / posterior | 106.066 / 226.385 |" in text
    assert "| truth inside P5-P95, prior / posterior | 1.00 / 0.00 |" in text
    assert "| cells with abs(z) > 3, prior / posterior | 0.0% / 100.0% |" in text
    (tmp_path / "truth.grdecl").write_text(grdecl([1.0] * 4))
    assert field_qc.main(args) == 1  # 4 truth cells, 5 field cells
    missing = [*args[:5], str(tmp_path / "none.grdecl"), *args[6:]]
    assert field_qc.main(missing) == 2
    assert field_qc.main([*args, "--coverage-min", "0"]) == 2


def test_truth_coverage_drop_at_the_threshold_fires():
    # 0.7 - 0.6 is 0.09999999999999998 in floating point; the documented drop is "at least 0.1".
    result = field_qc.compare(
        [[-1] * 7 + [1] * 3, [1] * 10],
        [[-1] * 6 + [1] * 4, [1] * 10],
        [True] * 10,
        False,
        0.2,
        0.5,
        0.01,
        [0] * 10,
    )
    assert result["truth"]["prior"]["coverage_p5_p95"] == pytest.approx(0.7)
    assert result["truth"]["posterior"]["coverage_p5_p95"] == pytest.approx(0.6)
    assert "HMQC-FIELD-TRUTH-COVERAGE" in [f["code"] for f in result["findings"]]


def test_collapse_share_counts_only_cells_with_prior_spread():
    result = field_qc.compare(
        [[100] * 10, [100] * 9 + [200]],
        [[100] * 9 + [149], [100] * 9 + [151]],
        [True] * 10,
        False,
        0.2,
        0.5,
        0.01,
    )
    assert result["cells_without_prior_spread"] == 9
    collapse = [f for f in result["findings"] if f["code"] == "HMQC-FIELD-COLLAPSE"]
    assert collapse and "1 of 1 active cells with prior spread" in collapse[0]["detail"]
