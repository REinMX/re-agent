"""Sensitivity designs use numerical prior quantiles and paired run contrasts."""

import builtins
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/uncertainty-register/scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_design_backend():
    if importlib.util.find_spec("fmudesign") is None and importlib.util.find_spec("semeio") is None:
        pytest.skip("requires fmudesign or semeio.fmudesign")


def block_imports(monkeypatch, failures):
    original = builtins.__import__
    attempts = []

    def importing(name, *args, **kwargs):
        attempts.append(name)
        for prefix, error in failures.items():
            if name == prefix or name.startswith(prefix + "."):
                raise error
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    return attempts


def fake_backend(monkeypatch, namespace):
    pd = pytest.importorskip("pandas")
    package = ModuleType(namespace)
    package.__path__ = []
    excel = ModuleType(namespace + "._excel_to_dict")
    create = ModuleType(namespace + ".create_design")
    calls = []

    def excel_to_dict(path):
        calls.append(("read", Path(path).name))
        return path

    class FakeDesignMatrix:
        def generate(self, config):
            calls.append(("generate", Path(config).name))
            self.designvalues = pd.DataFrame(
                [
                    dict(REAL=0, SENSNAME="base", SENSCASE="ref", A=0.5),
                    dict(REAL=1, SENSNAME="A", SENSCASE="low", A=0.1),
                    dict(REAL=2, SENSNAME="A", SENSCASE="high", A=0.9),
                ]
            )

        def to_xlsx(self, path, *, designsheet, defaultsheet):
            calls.append(("write", designsheet, defaultsheet))
            with pd.ExcelWriter(path) as writer:
                self.designvalues.to_excel(writer, sheet_name=designsheet, index=False)
                pd.DataFrame([["A", 0.5]]).to_excel(
                    writer, sheet_name=defaultsheet, index=False, header=False
                )

    excel.excel_to_dict = excel_to_dict
    create.DesignMatrix = FakeDesignMatrix
    for module in (package, excel, create):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return excel, create, calls


def test_prior_quantiles_and_rows(tmp_path):
    design = load("make_design")
    dist = tmp_path / "prior.dist"
    dist.write_text("A UNIFORM 0 10\nB LOGUNIF 1 100\nC TRUNCATED_NORMAL 0 1 -1 1\n")
    priors = design.read_priors([dist])
    assert design.quantile(priors["A"], 0.1) == pytest.approx(1)
    assert design.quantile(priors["B"], 0.9) == pytest.approx(100**0.9)
    assert design.quantile(priors["C"], 0.1) == pytest.approx(-0.74901459896957)
    rows = design.design_rows(priors, "onebyone", "", 42)
    assert len(rows) == 7
    assert rows[0]["A"] == 5
    assert rows[1]["A"] == 1
    assert rows[2]["A"] == 9
    assert rows[1]["B"] == pytest.approx(10)


def test_tornado_math():
    tornado = load("tornado")
    rows = [
        dict(REAL=i, SENSNAME=n, SENSCASE=c)
        for i, n, c in [
            (0, "base", "ref"),
            (1, "A", "low"),
            (2, "A", "high"),
            (3, "B", "low"),
            (4, "B", "high"),
        ]
    ]
    result = tornado.contrasts(rows, {0: 100, 1: 80, 2: 130, 3: 105, 4: 95}, False)
    assert result[0] == dict(parameter="A", low_delta=-20, high_delta=30, span=50)
    assert result[1]["parameter"] == "B"
    with pytest.raises(ValueError, match="Missing realizations"):
        tornado.contrasts(rows, {0: 100}, False)
    assert tornado.contrasts(rows, {0: 100, 1: 80, 2: 130}, True) == result[:1]


def test_attribution_identity(tmp_path):
    design, tornado = load("make_design"), load("tornado")
    dist = tmp_path / "priors"
    dist.write_text("PORO UNIFORM 0 1\nPERM LOGUNIF 1 100\n")
    rows = design.design_rows(design.read_priors([dist]), "attribution", "static=PORO;dynamic=PERM", 7)
    assert len(rows) == 4
    assert rows[1]["PERM"] == rows[0]["PERM"]
    assert rows[2]["PORO"] == rows[0]["PORO"]
    assert rows[1]["PORO"] == rows[3]["PORO"]
    result = tornado.contrasts(rows, {0: 100, 1: 120, 2: 130, 3: 160}, False)
    assert [r["delta"] for r in result] == [20, 30, 10]
    assert sum(r["share"] for r in result) == pytest.approx(1)
    with pytest.raises(ValueError, match="exactly one"):
        design.design_rows(design.read_priors([dist]), "attribution", "static=*;dynamic=*", 7)


@pytest.mark.parametrize("fixed", [False, True])
@pytest.mark.parametrize("mode,count", [("onebyone", 5), ("attribution", 4)])
def test_xlsx_is_readable_by_ert(tmp_path, mode, count, fixed):
    require_design_backend()
    import pandas as pd
    from ert.config.design_matrix import DesignMatrix

    design = load("make_design")
    dist = tmp_path / "priors.dist"
    dist.write_text("PORO UNIFORM .8 1.2\nPERM LOGUNIF .01 1\n" + ("PRED_BHP CONST 220\n" if fixed else ""))
    priors = design.read_priors([dist])
    result = design.write_design(priors, mode, "static=PORO;dynamic=PERM", 42, tmp_path)
    ert = DesignMatrix.from_config_list(
        [str(result), {"DESIGN_SHEET": "DesignSheet", "DEFAULT_SHEET": "DefaultValues"}]
    )
    assert sum(ert.active_realizations) == count
    frame = pd.read_excel(result, sheet_name="DesignSheet")
    assert frame["REAL"].tolist() == list(range(count))
    assert frame.iloc[0]["PORO"] == 1
    assert frame.iloc[0]["PERM"] == pytest.approx(0.1)
    expected = design.design_rows(priors, mode, "static=PORO;dynamic=PERM", 42)
    pd.testing.assert_frame_equal(frame, pd.DataFrame(expected), check_dtype=False)
    if fixed:
        assert frame["PRED_BHP"].tolist() == [220] * count
        defaults = pd.read_excel(result, sheet_name="DefaultValues", header=None, index_col=0)
        assert defaults.loc["PRED_BHP", 1] == 220
        native_values = ert.design_matrix_df.to_pandas()
        assert native_values["PRED_BHP"].tolist() == [220] * count
    if mode == "onebyone":
        assert frame.iloc[1]["PORO"] == pytest.approx(0.84)
        assert frame.iloc[4]["PERM"] == pytest.approx(10**-0.2)
    else:
        assert frame.iloc[1]["PERM"] == pytest.approx(0.1)
        assert frame.iloc[2]["PORO"] == 1
        assert frame.iloc[3]["PORO"] == frame.iloc[1]["PORO"]


@pytest.mark.parametrize(
    "text",
    [
        "A LOGUNIF -1 1",
        "A UNIFORM 1 1",
        "A TRUNCATED_NORMAL 0 -1 -2 2",
        "A UNKNOWN 0 1",
        "A UNIFORM nan 1",
        "A UNIFORM 0 1\nA UNIFORM 1 2",
    ],
)
def test_bad_priors_fail_closed(tmp_path, text):
    design = load("make_design")
    path = tmp_path / "bad.dist"
    path.write_text(text)
    with pytest.raises(ValueError):
        design.read_priors([path])


def test_egg_design_real():
    """Opt in to nine public Egg runs, with two concurrent Flow processes."""
    import os
    import shutil
    import subprocess
    import tempfile

    if os.environ.get("DESIGN_REAL") != "1":
        pytest.skip("set DESIGN_REAL=1, DESIGN_CASE and DESIGN_SCRATCH for public Egg runs")
    require_design_backend()
    import pandas as pd

    source = Path(os.environ["DESIGN_CASE"]).resolve()
    scratch = Path(os.environ["DESIGN_SCRATCH"]).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="egg-design-", dir=scratch))
    for folder in ["deck", "ert", "jobs"]:
        shutil.copytree(source / folder, root / folder)
    shutil.copy(source / "perm/permx_0.grdecl", root / "deck/permx.grdecl")
    job = root / "jobs/egg_params.py"
    job.write_text(
        job.read_text()
        .replace('p[0].startswith("EGG:")', 'p[0] in {"KVKH", "PORO_MULT"}')
        .replace("p[0][4:]", "p[0]")
    )
    original = (root / "ert/egg_ahm.ert").read_text()
    design = load("make_design")
    tornado = load("tornado")
    priors = design.read_priors([root / "ert/egg.dist"])
    worklog = [
        sys.executable,
        str(SCRIPTS.parents[1] / "earned-autonomy/scripts/worklog.py"),
        "--log",
        str(root / "worklog.jsonl"),
    ]
    subprocess.run(
        worklog + ["start", "egg-design", "--title", "Public Egg design validation"], cwd=root, check=True
    )
    for mode, count in [("onebyone", 5), ("attribution", 4)]:
        out = root / mode
        xlsx = design.write_design(priors, mode, "static=PORO_MULT;dynamic=KVKH", 42, out)
        lines = []
        replacements = {
            "NUM_REALIZATIONS": f"NUM_REALIZATIONS {count}",
            "MIN_REALIZATIONS": f"MIN_REALIZATIONS {count}",
            "RUNPATH": f"RUNPATH {out}/realization-<IENS>/iter-<ITER>",
            "ENSPATH": f"ENSPATH {out}/storage",
            "UPDATE_LOG_PATH": f"UPDATE_LOG_PATH {out}/update_log",
            "RUNPATH_FILE": f"RUNPATH_FILE {out}/runpath_file",
            "QUEUE_OPTION": "QUEUE_OPTION LOCAL MAX_RUNNING 2",
            "FIELD": "FORWARD_MODEL COPY_FILE(<FROM>=<DECK>/permx.grdecl, <TO>=permx.grdecl)",
        }
        for line in original.splitlines():
            key = line.split()[0] if line.split() else ""
            if key in {"OBS_CONFIG", "GEN_KW"}:
                continue
            lines.append(replacements.get(key, line))
        lines.append(f"DESIGN_MATRIX {xlsx} DESIGN_SHEET:DesignSheet DEFAULT_SHEET:DefaultValues")
        config = root / f"ert/{mode}.ert"
        config.write_text("\n".join(lines) + "\n")
        command = worklog + [
            "run",
            "--task",
            "egg-design",
            "--kind",
            "sim",
            "--label",
            mode,
            "--sims",
            str(count),
            "--",
            sys.executable,
            "-m",
            "ert",
            "ensemble_experiment",
            str(config),
        ]
        print("COMMAND:", " ".join(command), flush=True)
        with (out / "ert.log").open("w") as log:
            run = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        assert run.returncode == 0, (out / "ert.log").read_text()[-5000:]
        frame = pd.read_excel(xlsx, sheet_name="DesignSheet")
        rows = frame.to_dict("records")
        values, metadata = tornado.read_values(
            str(out / "realization-*/iter-0/model/EGG-*"), rows, "FOPT", "last"
        )
        assert len(values) == count
        for row in rows:
            real = int(row["REAL"])
            parameters = out / f"realization-{real}/iter-0/parameters.txt"
            text = parameters.read_text()
            for name in priors:
                import re

                match = re.search(rf"^{name}\s+(\S+)", text, re.MULTILINE)
                assert match, text
                assert float(match[1]) == pytest.approx(row[name], rel=1e-5)
        assert (
            tornado.main(
                [
                    "--runpath-glob",
                    str(out / "realization-*/iter-0/model/EGG-*"),
                    "--design",
                    str(xlsx),
                    "--vector",
                    "FOPT",
                    "--out",
                    str(out / "tornado"),
                ]
            )
            == 0
        )
        result = tornado.contrasts(rows, values, False)
        if mode == "onebyone":
            assert len(result) == 2
        else:
            assert sum(r["delta"] for r in result) == pytest.approx(values[3] - values[0])
    subprocess.run(
        worklog + ["end", "--task", "egg-design", "--status", "done", "--outputs", str(root)],
        cwd=root,
        check=True,
    )
    print("EGG_DESIGN_OUTPUT:", root, flush=True)


def test_summary_cli_outputs_and_failures(tmp_path):
    pytest.importorskip("res2df")
    pytest.importorskip("matplotlib")
    import json

    import pandas as pd
    from synth_fmu import make_ensemble

    tornado = load("tornado")
    scratch = tmp_path / "runs"
    make_ensemble(scratch, 3)
    rows = [
        dict(REAL=i, SENSNAME=n, SENSCASE=c)
        for i, n, c in [(0, "base", "ref"), (1, "A", "low"), (2, "A", "high")]
    ]
    design = tmp_path / "design.xlsx"
    pd.DataFrame(rows).to_excel(design, sheet_name="DesignSheet", index=False)
    out = tmp_path / "report"
    args = [
        "--runpath-glob",
        str(scratch / "realization-*/iter-0/eclipse/model/TOY"),
        "--design",
        str(design),
        "--vector",
        "FOPT",
        "--out",
        str(out),
    ]
    assert tornado.main(args) == 0
    table = pd.read_csv(out / "tornado.csv")
    assert table.iloc[0]["low_delta"] > 0
    assert table.iloc[0]["high_delta"] == pytest.approx(2 * table.iloc[0]["low_delta"])
    assert (out / "tornado.png").read_bytes().startswith(b"\x89PNG")
    assert "One-by-one ignores interactions." in (out / "tornado.md").read_text()
    assert len(pd.read_csv(out / "responses.csv")) == 3
    assert tornado.main(args + ["--date", "1900-01-01"]) == 2
    assert tornado.main(args + ["--vector", "MISSING"]) == 2
    (scratch / "realization-2/iter-0/OK").rename(scratch / "realization-2/iter-0/PENDING")
    assert tornado.main(args) == 1
    assert json.loads((out / "missing_realizations.json").read_text()) == [2]
    # Existing summaries without an ERT success marker must not enter a contrast.
    assert tornado.main(args + ["--allow-partial"]) == 2
    (scratch / "realization-0/iter-0/ERROR").write_text("failed")
    assert tornado.main(args) == 1
    assert json.loads((out / "missing_realizations.json").read_text()) == [0, 2]


def test_attribution_zero_total_and_missing():
    tornado = load("tornado")
    rows = [
        dict(REAL=i, SENSNAME=n, SENSCASE=c)
        for i, n, c in [
            (0, "base", "ref"),
            (1, "static", "static"),
            (2, "dynamic", "dynamic"),
            (3, "all", "all"),
        ]
    ]
    result = tornado.contrasts(rows, {0: 100, 1: 120, 2: 130, 3: 100}, False)
    assert [r["delta"] for r in result] == [20, 30, -50]
    assert all(r["share"] is None for r in result)
    with pytest.raises(ValueError, match="complete"):
        tornado.contrasts(rows, {0: 100, 1: 120, 3: 100}, True)


def test_missing_fmudesign_exits_2(tmp_path, monkeypatch, capsys):
    design = load("make_design")
    block_imports(
        monkeypatch,
        {
            "fmudesign": ModuleNotFoundError("No module named 'fmudesign'", name="fmudesign"),
            "semeio": ModuleNotFoundError("No module named 'semeio'", name="semeio"),
        },
    )
    prior = tmp_path / "prior"
    prior.write_text("A UNIFORM 0 1")
    assert design.main(["--dist", str(prior), "--mode", "onebyone", "--out", str(tmp_path / "out")]) == 2
    error = capsys.readouterr().err
    assert "fmudesign is required" in error
    assert "semeio.fmudesign" in error
    assert not (tmp_path / "out").exists()


def test_missing_pandas_exits_2(tmp_path, monkeypatch, capsys):
    design = load("make_design")
    block_imports(monkeypatch, {"pandas": ModuleNotFoundError("No module named 'pandas'", name="pandas")})
    prior = tmp_path / "prior"
    prior.write_text("A UNIFORM 0 1")
    assert design.main(["--dist", str(prior), "--mode", "onebyone", "--out", str(tmp_path / "out")]) == 2
    assert "pandas" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("namespace", ["fmudesign", "semeio.fmudesign"])
def test_backend_exports_with_existing_api(tmp_path, monkeypatch, namespace):
    design = load("make_design")
    _, _, calls = fake_backend(monkeypatch, namespace)
    failures = (
        {"fmudesign": ModuleNotFoundError("No module named 'fmudesign'", name="fmudesign")}
        if namespace == "semeio.fmudesign"
        else {"semeio": AssertionError("top-level backend must take priority")}
    )
    block_imports(monkeypatch, failures)
    result = design.write_design({"A": ("UNIFORM", [0, 1])}, "onebyone", "", 42, tmp_path)
    assert result == tmp_path / "design.xlsx"
    assert result.is_file()
    assert calls == [
        ("read", "fmudesign.xlsx"),
        ("generate", "fmudesign.xlsx"),
        ("write", "DesignSheet", "DefaultValues"),
    ]


@pytest.mark.parametrize("namespace", ["fmudesign", "semeio.fmudesign"])
@pytest.mark.parametrize("failure", ["dependency", "submodule", "symbol", "generate", "export"])
def test_broken_backend_fails_without_fallback(tmp_path, monkeypatch, capsys, namespace, failure):
    design = load("make_design")
    excel, create, _ = fake_backend(monkeypatch, namespace)
    failures = {}
    if namespace == "semeio.fmudesign":
        failures["fmudesign"] = ModuleNotFoundError("No module named 'fmudesign'", name="fmudesign")
    if failure == "dependency":
        detail = "missing_backend_dependency"
        failures[namespace + "._excel_to_dict"] = ModuleNotFoundError(detail, name=detail)
    elif failure == "submodule":
        detail = namespace + ".create_design"
        failures[detail] = ModuleNotFoundError(detail, name=detail)
    elif failure == "symbol":
        detail = "excel_to_dict"
        del excel.excel_to_dict
    else:
        detail = "incompatible backend signature"

        def incompatible(*args, **kwargs):
            raise TypeError(detail)

        monkeypatch.setattr(
            create.DesignMatrix, "generate" if failure == "generate" else "to_xlsx", incompatible
        )
    attempts = block_imports(monkeypatch, failures)
    prior = tmp_path / "prior"
    prior.write_text("A UNIFORM 0 1")
    out = tmp_path / "out"
    assert design.main(["--dist", str(prior), "--mode", "onebyone", "--out", str(out)]) == 2
    error = capsys.readouterr().err
    assert namespace in error
    assert detail in error
    assert "Check" in error
    assert "is required" not in error
    assert not (out / "design.xlsx").exists()
    if namespace == "fmudesign":
        assert not any(name.startswith("semeio") for name in attempts)


@pytest.mark.parametrize("mode", ["onebyone", "attribution"])
def test_fixed_controls_do_not_change_sampling_or_sensitivities(tmp_path, mode):
    design = load("make_design")
    prior = tmp_path / "prior.dist"
    prior.write_text("PRED_BHP CONST 220\nPORO LOGUNIF .8 1.2\nKVKH LOGUNIF .01 1\nMULTFLT LOGUNIF .1 10\n")
    priors = design.read_priors([prior])
    assert priors["PRED_BHP"] == ("CONST", [220])
    assert [design.quantile(priors["PRED_BHP"], p) for p in (0.1, 0.5, 0.9)] == [220] * 3
    groups = "static=PORO;dynamic=KVKH,MULTFLT"
    rows = design.design_rows(priors, mode, groups, 42)
    without_fixed = design.design_rows({n: p for n, p in priors.items() if n != "PRED_BHP"}, mode, groups, 42)
    assert len(rows) == (7 if mode == "onebyone" else 4)
    assert all(row["PRED_BHP"] == 220 for row in rows)
    assert [{n: v for n, v in row.items() if n != "PRED_BHP"} for row in rows] == without_fixed
    assert all(row["SENSNAME"] != "PRED_BHP" for row in rows)


@pytest.mark.parametrize("text", ["A CONST", "A CONST 1 2", "A CONST nan", "A CONST inf"])
def test_bad_fixed_controls_fail_closed(tmp_path, text):
    prior = tmp_path / "prior.dist"
    prior.write_text(text)
    with pytest.raises(ValueError):
        load("make_design").read_priors([prior])


@pytest.mark.parametrize("mode,count", [("onebyone", 7), ("attribution", 4)])
def test_cli_exports_sampled_parameters_and_fixed_controls(tmp_path, capsys, mode, count):
    require_design_backend()
    import pandas as pd
    from ert.config.design_matrix import DesignMatrix

    design = load("make_design")
    prior = tmp_path / "prior.dist"
    prior.write_text("PORO LOGUNIF .8 1.2\nPRED_BHP CONST 220\nKVKH LOGUNIF .01 1\nMULTFLT LOGUNIF .1 10\n")
    out = tmp_path / "out"
    assert (
        design.main(
            [
                "--dist",
                str(prior),
                "--mode",
                mode,
                "--groups",
                "static=PORO;dynamic=KVKH,MULTFLT",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert f"NUM_REALIZATIONS {count}\n" in output
    assert (
        f"DESIGN_MATRIX {out / 'design.xlsx'} DESIGN_SHEET:DesignSheet DEFAULT_SHEET:DefaultValues" in output
    )
    native = DesignMatrix.from_config_list(
        [str(out / "design.xlsx"), {"DESIGN_SHEET": "DesignSheet", "DEFAULT_SHEET": "DefaultValues"}]
    )
    assert native.active_realizations == [True] * count
    frame = pd.read_excel(out / "design.xlsx", sheet_name="DesignSheet")
    assert frame["PRED_BHP"].tolist() == [220] * count
    defaults = pd.read_excel(out / "design.xlsx", sheet_name="DefaultValues", header=None, index_col=0)
    assert defaults.loc["PRED_BHP", 1] == 220
    assert set(frame["SENSNAME"]) == (
        {"base", "PORO", "KVKH", "MULTFLT"} if mode == "onebyone" else {"base", "static", "dynamic", "all"}
    )
