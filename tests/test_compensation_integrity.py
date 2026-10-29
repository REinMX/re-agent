"""Standard-library evidence rejection tests. All positive controls are synthetic."""

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "comp_integrity", ROOT / "skills/history-match-qc/scripts/compensation_check.py"
)
comp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = comp
SPEC.loader.exec_module(comp)


def write_csv(path, rows, names=("REAL", "A", "B"), encoding="utf-8"):
    with path.open("w", newline="", encoding=encoding) as stream:
        writer = csv.writer(stream)
        writer.writerow(names)
        writer.writerows(rows)
    return path


@pytest.fixture
def sample(tmp_path):
    dist = tmp_path / "p.dist"
    dist.write_text("A UNIFORM 0 1\nB UNIFORM 0 1\n")
    prior = write_csv(tmp_path / "prior.csv", [(0, 0.2, 0.7), (1, 0.8, 0.3), (2, 0.5, 0.5)])
    post = write_csv(tmp_path / "post.csv", [(0, 0.3, 0.6), (1, 0.7, 0.4), (2, 0.5, 0.5)])
    return dist, prior, post


def args_for(sample):
    dist, prior, post = sample
    return [str(dist), "--prior-csv", str(prior), "--posterior-csv", str(post)]


@pytest.mark.parametrize("stage", [1, 2])
def test_missing_declared_bounded_parameter_refuses(sample, capsys, stage):
    write_csv(sample[stage], [(0, 0.3), (1, 0.7), (2, 0.5)], ("REAL", "A"))
    assert comp.main(args_for(sample)) == 1
    assert "missing" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "rows",
    [
        [(0, 0.3, 0.6), (1, 0.7, 0.4)],
        [(0, 0.3, 0.6), (1, 0.7, 0.4), (3, 0.5, 0.5)],
        [(0, 0.3, 0.6), (0, 0.7, 0.4), (2, 0.5, 0.5)],
    ],
)
def test_member_domains_must_match_before_scoring(sample, capsys, rows):
    write_csv(sample[2], rows)
    assert comp.main(args_for(sample)) == 1
    assert "member" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("real", ["nan", "1.5", "-1", "not-a-real"])
def test_real_identity_must_be_nonnegative_integer(sample, real, capsys):
    write_csv(sample[2], [(real, 0.3, 0.6), (1, 0.7, 0.4), (2, 0.5, 0.5)])
    assert comp.main(args_for(sample)) == 1
    assert "member identity must be a nonnegative integer" in capsys.readouterr().err


def test_csv_requires_explicit_member_identity(sample, capsys):
    write_csv(sample[2], [(0.3, 0.6), (0.7, 0.4), (0.5, 0.5)], ("A", "B"))
    assert comp.main(args_for(sample)) == 1
    assert "need one REAL member identity column" in capsys.readouterr().err


@pytest.mark.parametrize("names", [("REAL", "A", "A", "B"), ("REAL", "A", "G:A", "B")])
def test_duplicate_parameter_columns_or_aliases_refuse(sample, names, capsys):
    write_csv(sample[2], [(0, 0.3, 0.4, 0.6), (1, 0.7, 0.6, 0.4), (2, 0.5, 0.4, 0.5)], names)
    assert comp.main(args_for(sample)) == 1
    assert "duplicate" in capsys.readouterr().err


def test_ambiguous_bare_parameter_match_refuses(sample, capsys):
    sample[0].write_text("G:A UNIFORM 0 1\nH:A UNIFORM 0 1\nB UNIFORM 0 1\n")
    assert comp.main(args_for(sample)) == 1
    assert "ambiguous parameter A" in capsys.readouterr().err


@pytest.mark.parametrize("option,value", [("--expected", "4"), ("--realizations", "0-1,4")])
def test_external_member_inventory_is_authoritative(sample, option, value, capsys):
    assert comp.main([*args_for(sample), option, value]) == 1
    assert "member sets differ from expected" in capsys.readouterr().err


def test_reader_preserves_real_and_row_order_does_not_change_score(sample):
    before = comp.read_ensemble(None, sample[1])
    assert before.members == (0, 1, 2)
    write_csv(sample[1], [(2, 0.5, 0.5), (0, 0.2, 0.7), (1, 0.8, 0.3)])
    after = comp.read_ensemble(None, sample[1])
    params = {p["name"]: p for p in comp.prior_qc.parse_dist(sample[0])[0]}
    post = comp.read_ensemble(None, sample[2])
    assert (
        comp.check_run("one", params, before, post).score == comp.check_run("two", params, after, post).score
    )


@pytest.mark.parametrize("seeded", [False, True])
@pytest.mark.parametrize("copied", [False, True])
def test_repeated_parameter_evidence_cannot_become_independent(sample, tmp_path, seeded, copied, capsys):
    args = [str(sample[0])]
    for i in range(3):
        prior, post = sample[1:]
        if copied:
            prior = tmp_path / f"copy-prior-{i}.csv"
            post = tmp_path / f"copy-post-{i}.csv"
            write_csv(prior, [(2, 0.5, 0.5), (1, 0.8, 0.3), (0, 0.2, 0.7)])
            write_csv(post, [(1, 0.7, 0.4), (0, 0.3, 0.6), (2, 0.5, 0.5)])
        args += ["--prior-csv", str(prior), "--posterior-csv", str(post), "--label", f"invented-{i}"]
        if seeded:
            args += ["--seed", str(100 + i)]
    if seeded:
        assert comp.main([*args, "--json"]) == 1
        assert "repeated" in capsys.readouterr().err.lower()
        return
    assert comp.main([*args, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["claim_ready"]
    assert "repeated prior" in result["seed_note"] and "repeated posterior" in result["seed_note"]


def independent_args(sample, tmp_path):
    args = [str(sample[0])]
    for i in range(3):
        offset = i * 0.01
        prior = write_csv(
            tmp_path / f"synthetic-prior-{i}.csv",
            [(r, a + offset, b) for r, a, b in [(0, 0.2, 0.7), (1, 0.8, 0.3), (2, 0.5, 0.5)]],
        )
        post = write_csv(
            tmp_path / f"synthetic-post-{i}.csv",
            [(r, a + offset, b) for r, a, b in [(0, 0.3, 0.6), (1, 0.7, 0.4), (2, 0.5, 0.5)]],
        )
        args += ["--prior-csv", str(prior), "--posterior-csv", str(post)]
    return args


@pytest.mark.parametrize("reused", ["prior", "posterior"])
def test_either_reused_component_refuses_distinct_seeds(sample, tmp_path, reused, capsys):
    args = independent_args(sample, tmp_path)
    option = "--prior-csv" if reused == "prior" else "--posterior-csv"
    source = sample[1] if reused == "prior" else sample[2]
    for i, arg in enumerate(args):
        if arg == option:
            args[i + 1] = str(source)
    assert comp.main([*args, "--seed", "10", "--seed", "11", "--seed", "12"]) == 1
    assert f"repeated {reused}" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("reused", ["prior", "posterior"])
def test_unseeded_reused_component_is_a_diagnostic_note(sample, tmp_path, reused, capsys):
    """Two posteriors from one shared prior are a valid diagnostic; they never claim."""
    args = independent_args(sample, tmp_path)
    option = "--prior-csv" if reused == "prior" else "--posterior-csv"
    source = sample[1] if reused == "prior" else sample[2]
    for i, arg in enumerate(args):
        if arg == option:
            args[i + 1] = str(source)
    assert comp.main([*args, "--expected", "3", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["claim_ready"]
    other = "posterior" if reused == "prior" else "prior"
    assert f"repeated {reused}" in result["seed_note"] and f"repeated {other}" not in result["seed_note"]


@pytest.mark.parametrize("reused", ["prior", "posterior"])
def test_function_summary_refuses_either_reused_component(sample, tmp_path, reused):
    args = independent_args(sample, tmp_path)
    params = {p["name"]: p for p in comp.prior_qc.parse_dist(sample[0])[0]}
    runs = []
    for i in range(3):
        prior = comp.read_ensemble(None, sample[1] if reused == "prior" else Path(args[2 + 4 * i]))
        post = comp.read_ensemble(None, sample[2] if reused == "posterior" else Path(args[4 + 4 * i]))
        run = comp.check_run(f"synthetic-{i}", params, prior, post)
        run.seed = 10 + i
        runs.append(run)
    with pytest.raises(ValueError, match=f"repeated {reused}"):
        comp.summarize(runs)


def test_three_unseeded_diagnostics_never_claim_readiness(sample, tmp_path, capsys):
    assert comp.main([*independent_args(sample, tmp_path), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["claim_ready"] and result["seeds"] == 0


def test_explicit_unique_seed_control_is_claim_ready_with_honest_synthetic_scope(sample, tmp_path, capsys):
    args = independent_args(sample, tmp_path) + [
        "--seed",
        "10",
        "--seed",
        "11",
        "--seed",
        "12",
        "--expected",
        "3",
        "--json",
    ]
    assert comp.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["claim_ready"] and result["seeds"] == 3
    assert [run["seed"] for run in result["runs"]] == [10, 11, 12]
    assert all(run["inventory_checked"] for run in result["runs"])


def seeded_member_args(sample, tmp_path, sizes):
    args = [str(sample[0])]
    for i, size in enumerate(sizes):
        offset, members = 0.01 * i, range(size)
        prior = write_csv(
            tmp_path / f"m-prior-{i}.csv",
            [(r, 0.2 + 0.1 * r + offset, 0.7 - 0.1 * r) for r in members],
        )
        post = write_csv(
            tmp_path / f"m-post-{i}.csv",
            [(r, 0.3 + 0.08 * r + offset, 0.6 - 0.08 * r) for r in members],
        )
        args += ["--prior-csv", str(prior), "--posterior-csv", str(post), "--seed", str(10 + i)]
    return args


def test_seeds_with_different_member_counts_never_claim(sample, tmp_path, capsys):
    """Reviewer probe: seeds with 3, 2 and 5 members, distinct fingerprints, no inventory."""
    assert comp.main([*seeded_member_args(sample, tmp_path, (3, 2, 5)), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["seeds"] == 3 and not result["claim_ready"]
    assert not any(run["inventory_checked"] for run in result["runs"])
    assert "inventory" in result["seed_note"]


def test_equal_member_sets_claim_only_with_an_authoritative_inventory(sample, tmp_path, capsys):
    args = seeded_member_args(sample, tmp_path, (3, 3, 3))
    assert comp.main([*args, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["claim_ready"] and "inventory" in result["seed_note"]
    assert comp.main([*args, "--expected", "3", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["claim_ready"]


def test_function_summary_needs_identical_inventoried_member_sets(sample, tmp_path):
    params = {p["name"]: p for p in comp.prior_qc.parse_dist(sample[0])[0]}
    args = seeded_member_args(sample, tmp_path, (3, 3, 2))
    runs = []
    for i in range(3):
        prior = comp.read_ensemble(None, Path(args[2 + 6 * i]))
        post = comp.read_ensemble(None, Path(args[4 + 6 * i]))
        runs.append(comp.check_run(f"s{i}", params, prior, post, seed=10 + i, expected=set(post.members)))
    assert all(run.inventory_checked for run in runs)
    report = comp.summarize(runs)
    assert not report.claim_ready and "member sets differ" in report.seed_note
    assert not comp.check_run("unchecked", params, prior, post).inventory_checked


def test_duplicate_declared_seed_refuses(sample, tmp_path, capsys):
    assert (
        comp.main([*independent_args(sample, tmp_path), "--seed", "10", "--seed", "10", "--seed", "12"]) == 1
    )
    assert "duplicate" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "text,message",
    [("A 0.5\nB nan\n", "must be finite"), ("A 0.5\nG:A 0.4\nB 0.5\n", "duplicate match")],
)
def test_supplied_truth_must_be_finite_and_unambiguous(sample, tmp_path, text, message, capsys):
    truth = tmp_path / "truth.txt"
    truth.write_text(text)
    assert comp.main([*args_for(sample), "--truth", str(truth)]) == 1
    assert message in capsys.readouterr().err


def test_partial_truth_grades_what_it_covers_and_lists_the_rest(sample, tmp_path, capsys):
    truth = tmp_path / "truth.txt"
    truth.write_text("A 0.5\n")
    assert comp.main([*args_for(sample), "--truth", str(truth), "--json"]) == 0
    run = json.loads(capsys.readouterr().out)["runs"][0]
    assert run["truth_missing"] == ["B"]
    graded = {p["name"]: p["truth_inside_p5_p95"] for p in run["parameters"]}
    assert graded["A"] is not None and graded["B"] is None
    assert comp.main([*args_for(sample), "--truth", str(truth)]) == 0
    assert "no truth for B" in capsys.readouterr().out


def test_runpaths_retain_integer_member_identity_and_reject_duplicate_real(tmp_path):
    for group, real in [("one", 7), ("one", 9), ("duplicate", 7)]:
        folder = tmp_path / group / f"realization-{real}" / "iter-0"
        folder.mkdir(parents=True)
        (folder / "parameters.txt").write_text("A 0.3\nB 0.6\n")
    ensemble = comp.read_ensemble(str(tmp_path / "one/realization-*/iter-0"), None)
    assert ensemble.members == (7, 9)
    with pytest.raises(ValueError, match="duplicate.*member"):
        comp.read_ensemble(str(tmp_path / "*/realization-*/iter-0"), None)


@pytest.mark.parametrize("value", ["nan", "inf", "", "invalid"])
def test_parameter_values_cannot_be_silently_skipped(sample, value, capsys):
    write_csv(sample[2], [(0, 0.3, value), (1, 0.7, value), (2, 0.5, value)])
    assert comp.main(args_for(sample)) == 1
    error = capsys.readouterr().err
    assert "missing declared bounded parameters ['B']" in error and "ignored" in error


def test_declared_parameter_with_one_blank_value_refuses(sample, capsys):
    write_csv(sample[2], [(0, 0.3, 0.6), (1, 0.7, ""), (2, 0.5, 0.5)])
    assert comp.main(args_for(sample)) == 1
    assert "missing declared bounded parameters ['B']" in capsys.readouterr().err


@pytest.mark.parametrize("stage", [1, 2])
@pytest.mark.parametrize("invalid_name,valid_name", [("G:A", "A"), ("A", "G:A")])
@pytest.mark.parametrize("value", ["", "nan", "inf", "invalid"])
def test_invalid_declared_column_cannot_be_replaced_by_an_alias(
    sample, capsys, stage, invalid_name, valid_name, value
):
    sample[0].write_text("G:A UNIFORM 0 1\nB UNIFORM 0 1\n")
    write_csv(
        sample[stage],
        [(0, 0.3, 0.4, 0.6), (1, value, 0.6, 0.4), (2, 0.5, 0.4, 0.5)],
        ("REAL", invalid_name, valid_name, "B"),
    )
    assert comp.main(args_for(sample)) == 1
    error = capsys.readouterr().err
    assert f"parameter column {invalid_name}" in error and "finite numeric" in error
    assert ("prior" if stage == 1 else "posterior") in error


@pytest.mark.parametrize("stage", [1, 2])
def test_ignored_ambiguous_alias_cannot_hide_behind_valid_exact_columns(sample, capsys, stage):
    sample[0].write_text("G:A UNIFORM 0 1\nH:A UNIFORM 0 1\nB UNIFORM 0 1\n")
    for path in sample[1:]:
        write_csv(
            path, [(0, 0.3, 0.4, 0.6), (1, 0.7, 0.6, 0.4), (2, 0.5, 0.4, 0.5)], ("REAL", "G:A", "H:A", "B")
        )
    write_csv(
        sample[stage],
        [(0, 0.3, 0.4, 0.6, 0.3), (1, 0.7, 0.6, 0.4, ""), (2, 0.5, 0.4, 0.5, 0.5)],
        ("REAL", "G:A", "H:A", "B", "A"),
    )
    assert comp.main(args_for(sample)) == 1
    assert "ambiguous parameter A" in capsys.readouterr().err


def test_bom_header_and_undeclared_text_columns_are_accepted(sample, capsys):
    names = ("REAL", "CASE", "DATE", "A", "B")
    tables = {
        1: [(0, 0.2, 0.7), (1, 0.8, 0.3), (2, 0.5, 0.5)],
        2: [(0, 0.3, 0.6), (1, 0.7, 0.4), (2, 0.5, 0.5)],
    }
    for stage, rows in tables.items():
        text = [(r, "base", "2020-01-01" if r else "", a, b) for r, a, b in rows]
        write_csv(sample[stage], text, names, encoding="utf-8-sig")
    ensemble = comp.read_ensemble(None, sample[2])
    assert ensemble.members == (0, 1, 2) and set(ensemble.values) == {"A", "B"}
    assert comp.main(args_for(sample)) == 0
    assert "COMPENSATION" in capsys.readouterr().out
