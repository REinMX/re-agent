"""Site templates and their renderer. Synthetic contract and decks only; no simulator, ERT or Everest run."""

import codecs
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "templates/site"
NAMES = [
    "01-fmu-prior-design",
    "02-ahm-esmda-qc",
    "03-everest-robust-well-control",
    "04-everest-drill-order",
    "05-everest-algorithm-check",
]
SKILLS = ROOT / "skills"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = load(ROOT / "tools/render_template.py", "render_template")


@pytest.fixture(autouse=True)
def no_contract_env(monkeypatch):
    for name in ("RE_AGENT_CONTRACT", "RE_AGENT_PROFILE", "RE_AGENT_ALLOW_PROJECT_PROFILES"):
        monkeypatch.delenv(name, raising=False)


def profile(tmp_path):
    return {
        "python": "/opt/synthetic/bin/python",
        "activate": "source /opt/synthetic/enable",
        "activate_shell": "bash",
        "ert": {"executable": "ert"},
        "simulator": {"name": "FLOW", "executable": "/opt/synthetic/bin/flow", "forward_model": "FLOW"},
        "queue": {"system": "LSF", "name": "synthetic_queue", "max_running": 40},
        "scratch_root": str(tmp_path / "scratch"),
        "output_root": str(tmp_path / "deliverables"),
        "everest_executable": "everest",
    }


def write_contract(tmp_path, **changes):
    prof = profile(tmp_path)
    prof.update(changes)
    path = tmp_path / "contract.yml"
    data = {"schema_version": 2, "active_profile": "synthetic", "profiles": {"synthetic": prof}}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def sets(tmp_path, name):
    deck = tmp_path / "deck"
    values = {"agent_root": str(ROOT)}
    if name.startswith(("01", "02")):
        values["deck_dir"] = str(deck)
    else:
        values["pred_deck"] = str(deck / "model/PRED.DATA")
    return [f"--set={k}={v}" for k, v in values.items()]


def render(tmp_path, name, out, contract):
    return renderer.main(
        [str(TEMPLATES / name), str(out), "--contract", str(contract), *sets(tmp_path, name)]
    )


@pytest.fixture
def pack(tmp_path):
    """All templates rendered as siblings, the prior written, and a synthetic observation file."""
    (tmp_path / "deck/model").mkdir(parents=True)
    (tmp_path / "deck/include").mkdir()
    (tmp_path / "deck/model/DROGON_AHM.DATA").write_text("-- synthetic\n")
    (tmp_path / "deck/model/PRED.DATA").write_text("-- synthetic\n")
    contract = write_contract(tmp_path)
    out = tmp_path / "pack"
    for name in NAMES:
        assert render(tmp_path, name, out / name, contract) == 0
    prior = out / "01-fmu-prior-design"
    subprocess.run(
        [sys.executable, str(prior / "bin/prior_design.py"), "dist"], check=True, capture_output=True
    )
    (prior / "obs/history.obs").write_text(
        "SUMMARY_OBSERVATION WOPR_A1_20180401 { VALUE = 100.0; ERROR = 20.0; DATE = 2018-04-01; KEY = WOPR:A1; };\n"
    )
    return out


def test_there_are_five_templates_each_with_the_readme_sections():
    assert sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir()) == NAMES
    for name in NAMES:
        text = (TEMPLATES / name / "README.md").read_text(encoding="utf-8")
        for heading in (
            "## What it is",
            "## Files",
            "## How to run at your site",
            "## Checks before you run",
            "## What the Drogon run showed",
        ):
            assert heading in text, (name, heading)
        assert "{{site:activate}}" in text
        assert "{{site:queue.system}}" in text and "{{site:queue.name}}" in text
        assert "{{site:queue.max_running}}" in text and "{{site:scratch_root}}" in text
        results = text.split("## What the Drogon run showed", 1)[1].strip()
        assert results, (name, "empty Drogon results section")
        # Every template has its Drogon results filled; the pending placeholder must not remain.
        assert "<!-- results:" not in results, name


def test_every_template_renders_without_tokens(pack):
    for name in NAMES:
        files = [p for p in (pack / name).rglob("*") if p.is_file()]
        assert files, name
        assert not (pack / name / "params.yml").exists()
        for path in files:
            assert "{{" not in path.read_text(encoding="utf-8"), path
    readme = (pack / "02-ahm-esmda-qc/README.md").read_text(encoding="utf-8")
    assert "source /opt/synthetic/enable" in readme
    assert "Queue: `LSF`, queue `synthetic_queue`, at most 40 realizations at a time." in readme


def test_rendered_scripts_are_executable(pack):
    for script in (
        "01-fmu-prior-design/bin/prior_design.py",
        "03-everest-robust-well-control/jobs/well_control.py",
    ):
        assert os.access(pack / script, os.X_OK) or sys.platform == "win32"


def test_rendered_yaml_parses_and_holds_site_values(pack, tmp_path):
    yml = sorted(pack.rglob("*.yml"))
    assert [p.relative_to(pack).as_posix() for p in yml] == [
        "01-fmu-prior-design/ert/design.yml",
        "01-fmu-prior-design/obs/obs_spec.yml",
        "02-ahm-esmda-qc/qc.yml",
        "03-everest-robust-well-control/case.yml",
        "04-everest-drill-order/case.yml",
        "05-everest-algorithm-check/case.yml",
    ]
    for path in yml:
        assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict), path
    case = yaml.safe_load((pack / "03-everest-robust-well-control/case.yml").read_text())
    assert case["everest"]["queue"] == {"system": "LSF", "name": "synthetic_queue", "max_running": 40}
    assert case["wells"]["injectors"] == {"A5": 6500.0, "A6": 6500.0}
    assert case["schedule"]["periods"] == ["2020-07-01", "2021-07-01", "2022-07-01", "2023-07-01"]
    assert case["schedule"]["start"] == dt.date(2020, 7, 1)
    assert case["posterior"] == f"{tmp_path}/scratch/drogon_ahm/realization-<REALIZATION_ID>/iter-4"
    design = yaml.safe_load((pack / "01-fmu-prior-design/ert/design.yml").read_text())
    assert design["zones"] == {"VALYSAR": 1, "THERYS": 2, "VOLON": 3}


def test_rendered_ert_configs_pass_the_ahm_check(pack):
    for config in ("01-fmu-prior-design/ert/prior.ert", "02-ahm-esmda-qc/ert/ahm.ert"):
        result = subprocess.run(
            [
                sys.executable,
                str(SKILLS / "ert-config-author-lint/scripts/check_ert_config.py"),
                "--ahm-only",
                str(pack / config),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout
        assert "AHM review: 0 error(s)" in result.stdout
    ahm = subprocess.run(
        [
            sys.executable,
            str(SKILLS / "ert-config-author-lint/scripts/check_ert_config.py"),
            "--ahm-only",
            str(pack / "02-ahm-esmda-qc/ert/ahm.ert"),
        ],
        capture_output=True,
        text=True,
    ).stdout
    assert "ES-MDA weights 15, 7.5, 3.75, 1.875 (comment at ahm.ert:2): sum(1/w) = 1" in ahm
    assert "ANALYSIS_SET_VAR PARAMETERS GEN_KW GLOBAL" in ahm


def posterior_link(tmp_path):
    rows = "realization,PERM\n" + "".join(f"{r},{1.0 + r * 0.1}\n" for r in range(8))
    (tmp_path / "post.csv").write_text(rows)
    (tmp_path / "prior.csv").write_text(
        "realization,PERM\n" + "".join(f"{r},{1.0 + r * 0.2}\n" for r in range(8))
    )
    out = tmp_path / "posterior_link.yml"
    source = f"{tmp_path}/scratch/drogon_ahm/realization-<REALIZATION_ID>/iter-4"
    result = subprocess.run(
        [
            sys.executable,
            str(SKILLS / "everest-config-author-lint/scripts/ahm_posterior_link.py"),
            "--posterior",
            str(tmp_path / "post.csv"),
            "--prior",
            str(tmp_path / "prior.csv"),
            "--data-source",
            source,
            "--target",
            "posterior",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout
    return out


def test_generated_everest_configs_pass_lint_without_errors(pack, tmp_path):
    link = posterior_link(tmp_path)
    generator = pack / "03-everest-robust-well-control/bin/make_configs.py"
    expected = {
        "03-everest-robust-well-control": ["robust_cvar.yml", "robust_mean.yml"],
        "04-everest-drill-order": ["drill_order.yml"],
        "05-everest-algorithm-check": ["algo_cobyla.yml", "algo_optpp.yml", "algo_slsqp.yml"],
    }
    for name, configs in expected.items():
        case = pack / name / "case.yml"
        data = yaml.safe_load(case.read_text())
        data["jobs"] = "custom"
        case.write_text(yaml.safe_dump(data))
        subprocess.run(
            [sys.executable, str(generator), str(case), "--posterior-link", str(link)],
            check=True,
            capture_output=True,
        )
        assert sorted(p.name for p in (pack / name / "everest").glob("*.yml")) == configs
        for config in configs:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SKILLS / "everest-config-author-lint/scripts/everest_lint.py"),
                    str(pack / name / "everest" / config),
                    "--with-everest",
                    "never",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)
            assert [f for f in report["findings"] if f["severity"] == "error"] == [], (config, report)
            assert result.returncode == 0
    cvar = yaml.safe_load((pack / "03-everest-robust-well-control/everest/robust_cvar.yml").read_text())
    assert cvar["optimization"]["cvar"] == {"percentile": 0.25}
    assert cvar["model"]["realizations"] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert cvar["simulator"]["queue_system"] == {
        "name": "lsf",
        "max_running": 40,
        "lsf_queue": "synthetic_queue",
    }
    assert cvar["input_constraints"][3] == {
        "weights": {"inj.A5.4": 1.0, "inj.A6.4": 1.0},
        "upper_bound": 14000.0,
        "scale": 14000.0,
    }
    assert cvar["output_constraints"] == [
        {"name": "max_fwpr", "upper_bound": 15000.0, "scale": 15000.0},
        {"name": "vrr", "lower_bound": 0.9, "scale": 1.0},
    ]
    assert cvar["install_data"] == [
        {
            "source": f"{tmp_path}/scratch/drogon_ahm/realization-<REALIZATION_ID>/iter-4",
            "target": "posterior",
            "link": True,
        }
    ]
    cobyla = yaml.safe_load((pack / "05-everest-algorithm-check/everest/algo_cobyla.yml").read_text())
    assert cobyla["model"]["realizations"] == [0, 1, 2, 3]
    assert cobyla["controls"][0]["variables"] == [
        {"name": "A5", "initial_guess": 6500.0},
        {"name": "A6", "initial_guess": 6500.0},
    ]


def test_make_configs_refuses_without_the_posterior_gate(pack):
    generator = pack / "03-everest-robust-well-control/bin/make_configs.py"
    result = subprocess.run(
        [sys.executable, str(generator), str(pack / "03-everest-robust-well-control/case.yml")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "one of the arguments --posterior-link --unchecked-posterior is required" in result.stderr


def test_missing_site_key_fails_with_file_and_line_and_writes_nothing(tmp_path, capsys):
    prof = profile(tmp_path)
    prof["queue"] = {"system": "LSF", "max_running": 40, "name": ""}
    contract = write_contract(tmp_path, queue=prof["queue"])
    out = tmp_path / "out"
    code = renderer.main(
        [str(TEMPLATES / NAMES[0]), str(out), "--contract", str(contract), f"--set=agent_root={ROOT}"]
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "Nothing was written." in err
    assert "  ert/prior.ert:14: site:queue.name has no value" in err
    assert "  README.md:63: site:queue.name has no value" in err
    assert "  ert/prior.ert:4: param:deck_dir has no value" in err
    assert not out.exists()


def test_placeholder_counts_as_missing(tmp_path, capsys):
    contract = write_contract(tmp_path, activate="<ASK: full activate command>")
    code = renderer.main(
        [
            str(TEMPLATES / NAMES[1]),
            str(tmp_path / "out"),
            "--contract",
            str(contract),
            *sets(tmp_path, NAMES[1]),
        ]
    )
    assert code == 1
    assert "  README.md:39: site:activate has no value" in capsys.readouterr().err


@pytest.mark.parametrize("inside", ["repo", "template"])
def test_output_inside_the_repo_or_template_is_refused(tmp_path, capsys, inside):
    contract = write_contract(tmp_path)
    out = ROOT / "rendered-test-output" if inside == "repo" else TEMPLATES / NAMES[0] / "out"
    code = renderer.main(
        [str(TEMPLATES / NAMES[0]), str(out), "--contract", str(contract), *sets(tmp_path, NAMES[0])]
    )
    assert code == 2
    assert "is inside this repository; choose a folder outside it" in capsys.readouterr().err
    assert not out.exists()


def synthetic_template(tmp_path):
    t = tmp_path / "tpl"
    (t / "bin").mkdir(parents=True)
    (t / "params.yml").write_text("name: demo\nwells: [A, B]\nrates: {A: 1.5}\nflag: true\nrequired: null\n")
    (t / "a.txt").write_text(
        "{{param:name}} {{param:wells}} {{param:rates}} {{param:flag}} {{param:required}}\n"
    )
    (t / "bin/run.sh").write_text("#!/bin/sh\necho {{param:name}}\n")
    os.chmod(t / "bin/run.sh", 0o644)
    (t / "data.bin").write_bytes(b"\x00\xff{{param:name}}")
    return t


def test_values_binary_files_and_modes(tmp_path):
    t = synthetic_template(tmp_path)
    out = tmp_path / "out"
    assert renderer.main([str(t), str(out), "--set", "required=x", "--set", "name=case one"]) == 0
    assert (out / "a.txt").read_text() == 'case one ["A", "B"] {"A": 1.5} true x\n'
    assert (out / "data.bin").read_bytes() == b"\x00\xff{{param:name}}"
    assert (out / "bin/run.sh").read_text() == "#!/bin/sh\necho case one\n"
    assert os.access(out / "bin/run.sh", os.X_OK) or sys.platform == "win32"
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "a.txt",
        "bin/run.sh",
        "data.bin",
    ]


def test_usage_errors(tmp_path, capsys):
    t = synthetic_template(tmp_path)
    assert renderer.main([str(t), str(tmp_path / "o1"), "--set", "unknown=1"]) == 2
    assert f"--set unknown: not declared in {t / 'params.yml'}" in capsys.readouterr().err
    (tmp_path / "full").mkdir()
    (tmp_path / "full/keep.txt").write_text("x")
    assert renderer.main([str(t), str(tmp_path / "full"), "--set", "required=x"]) == 2
    assert "exists and is not an empty folder" in capsys.readouterr().err
    (t / "b.txt").write_text("{{site:queue.name}} {{other:x}}\n")
    assert (
        renderer.main(
            [str(t), str(tmp_path / "o2"), "--set", "required=x", "--contract", str(tmp_path / "none.yml")]
        )
        == 2
    )
    assert "contract: contract file not found" in capsys.readouterr().err


def test_unknown_token_is_listed(tmp_path, capsys):
    t = synthetic_template(tmp_path)
    (t / "b.txt").write_text("ok\n{{other:x}}\n")
    assert renderer.main([str(t), str(tmp_path / "o"), "--set", "required=x"]) == 1
    assert "  b.txt:2: unknown token {{other:x}}" in capsys.readouterr().err


def test_cli_exit_code(tmp_path):
    t = synthetic_template(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/render_template.py"), str(t), str(tmp_path / "o")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "  a.txt:1: param:required has no value" in result.stderr


def test_templates_hold_no_site_paths_or_hosts():
    terms = json.loads((ROOT / "tests/data/public_hygiene_terms.json").read_text(encoding="utf-8"))
    words = ["/workspace", "/prog/"]
    words += [
        codecs.decode(t, "rot13") for t in (*terms["company"], *terms["domains"], *terms["personal_paths"])
    ]
    for path in TEMPLATES.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            for word in words:
                assert word not in text, (path, word)


# ---- the scripts that the templates ship, on rendered copies ----


def test_prior_design_dist_and_apply(pack, tmp_path):
    prior = pack / "01-fmu-prior-design"
    dist = (prior / "ert/prior.dist").read_text().splitlines()
    assert len(dist) == 20
    assert dist[0].split() == ["PERM_VALYSAR", "LOGUNIF", "0.33", "3.0"]
    assert dist[13].split() == ["MULTREGT_VALYSAR_THERYS", "LOGUNIF", "0.0001", "1.0"]
    tool = load(prior / "bin/prior_design.py", "prior_design")
    design = tool.load_design(prior / "ert/design.yml")
    names = [name for name, _ in tool.parameters(design)]
    params = {name: 1.0 for name in names} | {
        "PERM_VALYSAR": 2.0,
        "KVKH_MULT": 0.5,
        "OWC_FLANK": 3.0,
        "KRW_EXP": 2.0,
    }
    (tmp_path / "parameters.txt").write_text("".join(f"PRIOR:{k} {v}\n" for k, v in params.items()))
    include = tmp_path / "include"
    (include / "props").mkdir(parents=True)
    (include / "solution").mkdir()
    (include / "props/drogon.sattab").write_text("SWOF\n0.2 0.0 1.0 0\n0.6 0.25 0.2 0\n1.0 1.0 0.0 0 /\n")
    (include / "solution/drogon.equil").write_text(
        "EQUIL\n" + "".join("  1700 250 1800.00 0 1600 0 1 1 0 /\n" for _ in range(7))
    )
    written = tool.apply(design, tmp_path / "parameters.txt", include)
    assert sorted(p.name for p in written) == [
        "aqufetp.inc",
        "equil.inc",
        "gridmult.inc",
        "multregt.inc",
        "sattab.inc",
    ]
    grid = (include / "ert/gridmult.inc").read_text()
    assert " PERMX 2 1 M /\n PERMY 2 1 M /\n PERMZ 1 1 M /\n PORO 1 1 M /" in grid
    assert (include / "ert/multregt.inc").read_text() == "MULTREGT\n 1 2 1 'Z' /\n 2 3 1 'Z' /\n/\n"
    assert (include / "ert/sattab.inc").read_text().splitlines()[
        2
    ] == "0.6000000 0.0625000 0.2000000 0.0000000"
    equil = (include / "ert/equil.inc").read_text().splitlines()
    assert equil[1].split()[2] == "1803.00" and equil[2].split()[2] == "1801.00"


def test_obs_builder_skips_shut_wells(pack):
    tool = load(pack / "01-fmu-prior-design/bin/make_obs.py", "make_obs")
    spec = yaml.safe_load((pack / "01-fmu-prior-design/obs/obs_spec.yml").read_text())
    spec.update(producers=["P1"], injectors=["I1"], dates=["2020-01-01", "2020-02-01"], noise_seed=None)

    def value(key, day):
        if key.startswith("WWIR"):
            return 0.0
        if key.startswith(("WOPR", "WWPR", "WGPR")):
            return 0.0 if day == "2020-02-01" else 500.0
        return 0.5

    blocks, skipped = tool.build(spec, value)
    assert skipped == 4 + 2
    assert blocks[0] == (
        "SUMMARY_OBSERVATION WOPR_P1_20200101 { VALUE = 500.0000; ERROR = 50.0000; DATE = 2020-01-01; KEY = WOPR:P1; };"
    )
    assert [b.split()[1] for b in blocks] == [
        "WOPR_P1_20200101",
        "WWCT_P1_20200101",
        "WGOR_P1_20200101",
        "WBHP_P1_20200101",
    ]


def test_well_control_drill_plan_and_schedule(pack, tmp_path):
    folder = pack / "04-everest-drill-order"
    (folder / "wells").mkdir(exist_ok=True)
    for well in ("OP5", "OP6", "OP7", "OP8"):
        (folder / f"wells/{well}.sch").write_text(f"WELSPECS\n '{well}' 'OP' 1 1 1* OIL /\n/\n")
    job = load(pack / "03-everest-robust-well-control/jobs/well_control.py", "well_control")
    case = job.load_case(folder / "case.yml")
    plan = job.drill_plan(case, {"OP7": 0.9, "OP5": 0.7, "OP6": 0.5, "OP8": 0.2})
    assert {w: d.isoformat() for w, d in plan.items()} == {
        "OP7": "2020-07-01",
        "OP5": "2021-01-01",
        "OP6": "2021-07-01",
        "OP8": "2022-01-01",
    }
    assert list(job.drill_plan(case, {})) == ["OP5", "OP6", "OP7", "OP8"]
    text = job.schedule_text(case, {"A5": {"1": 8000, "2": 7000, "3": 6000, "4": 5000}}, {}, plan)
    lines = text.splitlines()
    assert lines[0] == "WELSPECS" and lines[3] == "WELSPECS"
    assert (
        " 'OP7' OPEN LRAT 1* 1* 1* 3000.00 1* 150.0 /"
        in lines[lines.index("WCONPROD") + 1 : lines.index("WCONINJE")]
    )
    assert lines[lines.index("WCONINJE") + 1] == " 'A5' WATER OPEN RATE 8000.00 1* 500.0 /"
    assert lines[-3:] == ["DATES", " 1 'JAN' 2025 /", "/"]
    assert text.count("DATES") == 18


def test_scan_grid_and_table(pack, tmp_path):
    tool = load(pack / "05-everest-algorithm-check/bin/scan.py", "scan")
    case = yaml.safe_load((pack / "05-everest-algorithm-check/case.yml").read_text())
    points = tool.grid(case, [0, 1, 2, 3, 4])
    assert len(points) == 5 * 5 * 4
    values = tool.table([(1.0, 2.0, 0), (1.0, 2.0, 1), (3.0, 4.0, 0), (3.0, 4.0, 1)], [1.0, 3.0, 5.0, 7.0])
    best, mean = tool.write_csv(tmp_path / "scan.csv", ["A5", "A6"], values)
    assert (best, mean) == ((3.0, 4.0), 6.0)
    assert (tmp_path / "scan.csv").read_text().splitlines() == [
        "A5,A6,npv_r0,npv_r1,npv_mean",
        "1.0,2.0,1.0,3.0,2.0",
        "3.0,4.0,5.0,7.0,6.0",
    ]


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_well_control_prepare_is_idempotent(pack, tmp_path, monkeypatch):
    job = load(pack / "03-everest-robust-well-control/jobs/well_control.py", "well_control_prepare")
    post = tmp_path / "post"
    (post / "include").mkdir(parents=True)
    (post / "model").mkdir()
    (post / "model/CASE-0.UNRST").write_text("restart")
    deck = tmp_path / "PRED.DATA"
    deck.write_text("RUNSPEC\n")
    case = {
        "deck": {
            "include_dir": "include",
            "data": str(deck),
            "restart_glob": "model/CASE-*.UNRST",
            "restart_link": "HIST/CASE",
        }
    }
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.chdir(run)
    job.prepare(case, str(post))
    job.prepare(case, str(post))
    assert os.readlink(run / "include") == str((post / "include").resolve())
    assert (run / "HIST/CASE.UNRST").read_text() == "restart"
    assert sorted(p.name for p in post.rglob("*")) == ["CASE-0.UNRST", "include", "model"]
    (run / "include").unlink()
    (run / "include").mkdir()
    with pytest.raises(SystemExit, match="is a folder"):
        job.prepare(case, str(post))


def test_sattab_keeps_tables_whole_across_comments(pack):
    tool = load(pack / "01-fmu-prior-design/bin/prior_design.py", "prior_design")
    params = {"KRW_EXP": 2.0, "KROW_EXP": 1.0}
    plain = "SWOF\n0.2 0.0 1.0 0\n0.6 0.25 0.2 0\n1.0 1.0 0.0 0 /\n"
    commented = (
        "SWOF\n-- SW KRW KROW PC\n0.2 0.0 1.0 0\n-- mid-table note\n\n0.6 0.25 0.2 0 -- inline\n"
        "1.0 1.0 0.0 0 /\nSGOF\n0.0 0.0 1.0 0\n1.0 0.5 0.0 0 /\nDENSITY\n 800 1000 0.9 /\n"
    )
    numbers = [line for line in tool.sattab(commented, params).splitlines() if line[:1].isdigit()]
    assert numbers[:3] == [line for line in tool.sattab(plain, params).splitlines() if line[:1].isdigit()]
    assert numbers[1] == "0.6000000 0.0625000 0.2000000 0.0000000"
    out = tool.sattab(commented, params)
    assert "-- mid-table note" in out and "-- SW KRW KROW PC" in out
    assert "1.0 0.5 0.0 0 /" in out and " 800 1000 0.9 /" in out


def test_set_placeholder_counts_as_missing():
    values = renderer.read_params(TEMPLATES / NAMES[1], ["deck_dir=<ASK: deck directory>"])
    assert values["deck_dir"] is None
    assert renderer.read_params(TEMPLATES / NAMES[1], ["deck_dir="])["deck_dir"] is None
    assert renderer.read_params(TEMPLATES / NAMES[1], ["deck_dir=/data/deck"])["deck_dir"] == "/data/deck"


def model_generator(pack):
    return load(pack / "03-everest-robust-well-control/bin/make_configs.py", "model_generator")


def test_everest_models_missing_jobs_fail_before_writing(pack, tmp_path, monkeypatch, capsys):
    generator = model_generator(pack)
    monkeypatch.setattr(generator, "installed_steps", lambda: set())
    out = tmp_path / "missing-models"
    assert (
        generator.main([str(pack / NAMES[2] / "case.yml"), "--unchecked-posterior", "--out", str(out)]) == 2
    )
    assert "unavailable" in capsys.readouterr().err
    assert not out.exists()


def test_everest_models_unknown_selection_refused(pack):
    generator = model_generator(pack)
    with pytest.raises(ValueError, match="jobs must be"):
        generator.everest_models({"jobs": "typo"})


@pytest.mark.parametrize("name", NAMES[2:4])
def test_everest_models_generated_inputs_and_jobs(pack, tmp_path, monkeypatch, name):
    pytest.importorskip("everest_models", reason="everest-models is not installed")
    pytest.importorskip("ert", reason="ERT is not installed")
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    generator = model_generator(pack)
    folder = pack / name
    case_file = folder / "case.yml"
    out = tmp_path / "generated" / name
    assert generator.main([str(case_file), "--unchecked-posterior", "--out", str(out)]) == 0
    cfg = yaml.safe_load(next(out.glob("*.yml")).read_text())
    commands = [step.split()[0] for step in cfg["forward_model"]]
    required = {"npv", "well_constraints"} | ({"drill_planner"} if name == NAMES[3] else set())
    assert required <= set(commands) <= generator.installed_steps() | {"well_control"}
    assert cfg["install_data"][-1] == {"source": str(out / "everest_models"), "target": "everest_models"}
    assets = out / "everest_models"
    economics = yaml.safe_load((assets / "npv.yml").read_text())
    assert economics["prices"]["FWPT"][0]["value"] == -30.0
    assert economics["prices"]["FOPT"][0]["value"] == 400.0
    assert economics["dates"]["ref_date"] == dt.date(2020, 7, 1)
    assert economics["multiplier"] == 1  # preserve currency precision before converting to millions
    from everest_models.jobs.fm_npv.npv_config import NPVConfig

    NPVConfig.model_validate(economics)
    from everest_models.jobs.fm_well_constraints.models import WellConstraintConfig

    WellConstraintConfig.model_validate(yaml.safe_load((assets / "injection.yml").read_text()))

    def run_job(job, *args):
        command = [sys.executable, "-m", f"everest_models.jobs.fm_{job}", *map(str, args)]
        result = subprocess.run(command, cwd=out, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr

    args = ["-i", assets / "injectors.json", "-c", assets / "injection.yml", "-o", "injection_events.json"]
    run_job("well_constraints", *args, "--lint")
    run_job("well_constraints", *args)
    events = json.loads((out / "injection_events.json").read_text())
    assert events[0]["ops"][0]["tokens"] == {"phase": "WATER", "rate": 6500.0}
    case = yaml.safe_load(case_file.read_text())
    if case.get("drill"):
        priorities = {w: spec["priority"] for w, spec in case["drill"]["wells"].items()}
        (out / "drill.json").write_text(json.dumps(priorities))
        args = [
            "-i",
            assets / "drill_wells.json",
            "-c",
            assets / "drill.yml",
            "-opt",
            "drill.json",
            "-o",
            "drill_dates.json",
            "-tl",
            "5",
        ]
        run_job("drill_planner", *args, "--lint")
        run_job("drill_planner", *args)
        wells = json.loads((out / "drill_dates.json").read_text())
        assert sorted(w["readydate"] for w in wells) == sorted(str(d) for d in case["drill"]["slots"])
        (folder / "wells").mkdir(exist_ok=True)
        for spec in case["drill"]["wells"].values():
            (folder / spec["definition"]).write_text("-- synthetic completion\n")
    job = load(pack / NAMES[2] / "jobs/well_control.py", "model_schedule")
    monkeypatch.chdir(out)
    job.model_schedule(job.load_case(case_file))
    schedule = (out / case["deck"]["schedule_file"]).read_text()
    assert "'A5' WATER OPEN RATE 6500.00 1* 500.0 /" in schedule
    assert "1 'JAN' 2025 /" in schedule
    # All rates and ready dates consumed by the adapter agree with the custom schedule.
    plan = job.drill_plan(case, priorities) if case.get("drill") else {}
    case["_dir"] = folder
    assert schedule == job.schedule_text(case, {}, {}, plan)
