"""resinsight-views: GRDECL arrays, ViewSpec, render_views against the fake rips, well_views. No ResInsight.

Real runs: test_resinsight_real.py.
"""

from __future__ import annotations

import csv
import functools
import json
import math
import sys
from pathlib import Path

import fake_rips
import pytest

ROOT = Path(__file__).resolve().parents[1]
for _scripts in (ROOT / "skills/resinsight-views/scripts", ROOT / "skills/resinsight-session/scripts"):
    if str(_scripts) not in sys.path:
        sys.path.insert(0, str(_scripts))

import grdecl  # noqa: E402
import render_views as rv  # noqa: E402
import view_spec as vs  # noqa: E402
import well_views as wv  # noqa: E402

ri = rv.ri  # the ri_session module that render_views uses

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ResInsight work is Linux only")


# grdecl -----------------------------------------------------------------------------------------------
def test_grdecl_round_trip(tmp_path):
    values = [0.25, 1.0, 100.0, 0.0, 3.5, 7.0, 2.0]
    path = grdecl.write_grdecl(tmp_path / "sub/A.grdecl", "SDRATIO", values, per_line=3)
    assert grdecl.read_grdecl(path, expected=7) == ("SDRATIO", values)
    assert path.read_text().splitlines()[1] == "SDRATIO"


def test_grdecl_expands_repeats_and_skips_comments(tmp_path):
    path = tmp_path / "r.grdecl"
    path.write_text("-- head\nPORO\n3*0.5 1 -- tail\n2*7\n/\n")
    assert grdecl.read_grdecl(path) == ("PORO", [0.5, 0.5, 0.5, 1.0, 7.0, 7.0])


def test_grdecl_skips_the_resinsight_second_header_line(tmp_path):
    path = tmp_path / "tof.grdecl"
    path.write_text("-- Exported from ResInsight\nTOF\nINJ1\n1 2 3 /\n")
    assert grdecl.read_grdecl(path, expected=3) == ("TOF", [1.0, 2.0, 3.0])


def test_grdecl_wrong_count(tmp_path):
    path = grdecl.write_grdecl(tmp_path / "a.grdecl", "A", [1, 2, 3])
    with pytest.raises(grdecl.GrdeclError, match="has 3 values, expected 4"):
        grdecl.read_grdecl(path, expected=4)


@pytest.mark.parametrize("keyword", ["poro", "TOOLONGNAME", "1A", "A-B", ""])
def test_grdecl_bad_keyword(tmp_path, keyword):
    with pytest.raises(grdecl.GrdeclError, match="keyword"):
        grdecl.write_grdecl(tmp_path / "a.grdecl", keyword, [1.0])


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_grdecl_write_refuses_non_finite(tmp_path, value):
    with pytest.raises(grdecl.GrdeclError, match="not finite"):
        grdecl.write_grdecl(tmp_path / "a.grdecl", "A", [1.0, value])
    assert not (tmp_path / "a.grdecl").exists()


@pytest.mark.parametrize("body", ["1 nan 2 /", "2*inf /", "1 NaN /"])
def test_grdecl_read_refuses_non_finite(tmp_path, body):
    path = tmp_path / "a.grdecl"
    path.write_text(f"A\n{body}\n")
    with pytest.raises(grdecl.GrdeclError, match="not finite"):
        grdecl.read_grdecl(path)


@pytest.mark.parametrize("body", ["1 abc /", "1 3*x /"])
def test_grdecl_read_refuses_text_after_numbers(tmp_path, body):
    path = tmp_path / "a.grdecl"
    path.write_text(f"A\n{body}\n")
    with pytest.raises(grdecl.GrdeclError, match="not a number"):
        grdecl.read_grdecl(path)


def test_grdecl_empty_file_has_no_keyword(tmp_path):
    path = tmp_path / "a.grdecl"
    path.write_text("-- only a comment\n")
    with pytest.raises(grdecl.GrdeclError, match="no keyword"):
        grdecl.read_grdecl(path)


# view_spec --------------------------------------------------------------------------------------------
def _spec(**over) -> dict:
    data = {
        "case": "/abs/CASE.EGRID",
        "out": "/abs/out",
        "arrays": [{"name": "SDRATIO", "file": "/abs/sdratio.grdecl"}],
        "views": [{"name": "v1", "property": "STATIC_NATIVE:PORO"}],
    }
    data.update(over)
    return data


def _view(**over) -> dict:
    return _spec(views=[{"name": "v1", "property": "STATIC_NATIVE:PORO", **over}])


def test_spec_defaults_and_types():
    spec = vs.parse_spec(
        _spec(
            views=[
                {
                    "name": "a",
                    "property": "INPUT_PROPERTY:SDRATIO",
                    "time_step": "last",
                    "range": {"j": [2, 2]},
                    "z_scale": 5,
                    "camera": "north",
                    "zoom": 3,
                },
                {"name": "b", "property": "FLOW_DIAGNOSTICS:TOF", "flow": {"injectors": ["I1"]}},
            ]
        )
    )
    a, b = spec.views
    assert spec.case == Path("/abs/CASE.EGRID") and spec.arrays[0].file == Path("/abs/sdratio.grdecl")
    assert (spec.width, spec.height, spec.project) == (1400, 1000, True)
    assert (
        a.property == vs.PropertyRef("INPUT_PROPERTY", "SDRATIO")
        and str(a.property) == "INPUT_PROPERTY:SDRATIO"
    )
    assert (a.time_step, a.range, a.z_scale, a.camera, a.zoom) == (
        "last",
        vs.Range(j=(2, 2)),
        5,
        "north",
        3.0,
    )
    assert (b.time_step, b.camera, b.zoom, b.flow) == (0, "top", 1.0, {"injectors": ["I1"]})


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "spec: must be an object"),
        (_spec(colour="red"), r"spec: unknown keys \['colour'\]"),
        ({"case": "/abs/C.EGRID", "views": []}, r"spec: missing keys \['out'\]"),
        (_spec(case="rel/C.EGRID"), "case: must be an absolute path"),
        (_spec(out="out"), "out: must be an absolute path"),
        (_spec(arrays=[{"name": "A", "file": "/a", "unit": "m"}]), r"arrays\[0\]: unknown keys"),
        (_spec(arrays=[{"name": "A"}]), r"arrays\[0\]: missing keys \['file'\]"),
        (_spec(arrays=[{"name": "sd", "file": "/a"}]), "GRDECL keyword"),
        (_spec(arrays=[{"name": "A", "file": "a.grdecl"}]), r"arrays\[0\].file: must be an absolute path"),
        (_spec(views=[]), "at least one view"),
        (_view(value=[0.5, 1.0]), r"views\[0\]: unknown keys \['value'\]"),
        (_spec(views=[{"name": "v1"}]), r"views\[0\]: missing keys \['property'\]"),
        (_spec(views=[{"name": "a b", "property": "STATIC_NATIVE:PORO"}]), "name must be unique"),
        (
            _spec(views=[{"name": "a", "property": "STATIC_NATIVE:PORO"}] * 2),
            r"views\[1\].name must be unique",
        ),
        (_view(property="PORO"), "property must be TYPE:NAME"),
        (_view(property="NATIVE:PORO"), "property must be TYPE:NAME"),
        (_view(property="STATIC_NATIVE: "), "property must be TYPE:NAME"),
        (_view(property="INPUT_PROPERTY:NTG"), "names no array"),
        (_view(range={"x": [1, 1]}), r"views\[0\].range: unknown keys \['x'\]"),
        (_view(range={"k": [1]}), r"range.k: must be \[first, last\]"),
        (_view(range={"k": [True, 2]}), r"range.k: must be \[first, last\]"),
        (_view(range={"k": [1.0, 2]}), r"range.k: must be \[first, last\]"),
        (_view(range={"k": [0, 2]}), "1 <= first <= last"),
        (_view(range={"k": [3, 2]}), "1 <= first <= last"),
        (_view(flow={"tracers": []}), r"views\[0\].flow: unknown keys"),
        (_view(flow={"injectors": []}), "flow needs a FLOW_DIAGNOSTICS property"),
        (_view(z_scale=0), "z_scale"),
        (_view(z_scale=101), "z_scale"),
        (_view(z_scale=True), "z_scale"),
        (_view(z_scale="5"), "z_scale"),
        (_view(camera="south"), "camera must be one of top, north, east"),
        (_view(camera="north", zoom=0.5), r"zoom must be in \[1, 20\]"),
        (_view(camera="north", zoom=21), r"zoom must be in \[1, 20\]"),
        (_view(camera="north", zoom=True), r"zoom must be in \[1, 20\]"),
        (_view(zoom=2), "zoom needs camera north or east"),
        (_view(time_step=-1), "time_step must be"),
        (_view(time_step=True), "not a time step"),
        (_view(time_step="middle"), "time_step must be"),
        (_view(time_step="2020/01/01"), "time_step must be"),
        (_spec(width=100), "width and height"),
        (_spec(height=5000), "width and height"),
        (_spec(width="1400"), "width and height"),
        (_spec(project="false"), "project must be true or false"),
    ],
)
def test_spec_errors(data, message):
    with pytest.raises(vs.SpecError, match=message):
        vs.parse_spec(data)


def test_load_spec_bad_json(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("{not json")
    with pytest.raises(vs.SpecError, match="spec.json"):
        vs.load_spec(path)
    with pytest.raises(vs.SpecError, match="missing.json"):
        vs.load_spec(tmp_path / "missing.json")


def test_range_bounds_are_one_based_start_and_count():
    rng = vs.Range(i=(2, 3), k=(4, 4))
    assert rng.bounds("i", 5) == (2, 2)
    assert rng.bounds("j", 7) == (1, 7)
    assert rng.bounds("k", 4) == (4, 1)
    with pytest.raises(vs.SpecError, match=r"range k=\[4, 4\] is outside 1..3"):
        rng.bounds("k", 3)


DATES = ["2020-01-01", "2020-02-01", "2020-03-01"]


@pytest.mark.parametrize(("token", "index"), [(0, 0), (2, 2), ("first", 0), ("last", 2), ("2020-02-01", 1)])
def test_resolve_time_step(token, index):
    assert vs.resolve_time_step(token, DATES) == index


@pytest.mark.parametrize(
    ("token", "dates", "message"),
    [
        (3, DATES, r"outside 0..2"),
        ("2020-01-15", DATES, "2020-01-15 is not a report step. Steps: 2020-01-01 .. 2020-03-01 \\(3\\)"),
        ("first", [], "no time steps"),
    ],
)
def test_resolve_time_step_errors(token, dates, message):
    with pytest.raises(vs.SpecError, match=message):
        vs.resolve_time_step(token, dates)


# render_views with the fake rips ----------------------------------------------------------------------
class FakeProcess:
    pid = -1
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


def _launcher(config, workdir):
    return ri.Launched(process=FakeProcess(), port=1234, log_file=workdir / "log")


@pytest.fixture
def gui(monkeypatch):
    """The fake rips in GUI mode; cases have dims 3x2x2, SWAT, and three monthly steps."""
    monkeypatch.setattr(fake_rips.Instance, "console", False)
    monkeypatch.setattr(fake_rips.Instance, "fail_connect", None)
    monkeypatch.setattr(fake_rips.Instance, "wells", [])
    monkeypatch.setattr(fake_rips.Instance, "case_kwargs", {"dynamic": ("SWAT",)})
    return functools.partial(rv.render, rips=fake_rips, launcher=_launcher)


def _render_case(tmp_path: Path, views: list[dict], arrays: bool = True) -> Path:
    egrid = tmp_path / "CASE.EGRID"
    egrid.write_bytes(b"grid")
    data = {"case": str(egrid), "out": str(tmp_path / "out"), "views": views}
    if arrays:
        array = grdecl.write_grdecl(tmp_path / "sdratio.grdecl", "SDRATIO", [float(g) for g in range(12)])
        data["arrays"] = [{"name": "SDRATIO", "file": str(array)}]
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(data))
    return path


VIEWS = [
    {"name": "sd_top", "property": "INPUT_PROPERTY:SDRATIO"},
    {
        "name": "swat_north",
        "property": "DYNAMIC_NATIVE:SWAT",
        "time_step": "2020-03-01",
        "range": {"j": [2, 2]},
        "camera": "north",
        "zoom": 2,
        "z_scale": 5,
    },
    {
        "name": "poro_east",
        "property": "STATIC_NATIVE:PORO",
        "range": {"i": [2, 3], "k": [2, 2]},
        "camera": "east",
    },
    {"name": "tof", "property": "FLOW_DIAGNOSTICS:TOF", "flow": {"injectors": ["I1"]}, "time_step": "last"},
]


def test_render_views_manifest_arrays_ranges_cameras(gui, tmp_path):
    spec = vs.load_spec(_render_case(tmp_path, VIEWS))
    returned = gui(spec, tmp_path / "ri")
    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert manifest == json.loads(json.dumps(returned))
    assert manifest["failed"] == [] and manifest["resinsight_version"] == "2026.9.1"
    assert manifest["arrays"] == {"SDRATIO": str(tmp_path / "sdratio.grdecl")}
    assert manifest["rule"].startswith("Pictures only")
    assert Path(manifest["project"]) == tmp_path / "out/views.rsp" and Path(manifest["project"]).is_file()
    by_name = {v["name"]: v for v in manifest["views"]}
    assert list(by_name) == ["sd_top", "swat_north", "poro_east", "tof"]
    assert (by_name["swat_north"]["time_step"], by_name["swat_north"]["date"]) == (2, "2020-03-01")
    assert by_name["swat_north"]["range"] == {"j": [2, 2]} and by_name["sd_top"]["range"] is None
    assert by_name["tof"]["date"] == "2020-03-01" and by_name["sd_top"]["date"] == "2020-01-01"
    for entry in manifest["views"]:
        png = Path(entry["png"])
        assert png.parent == tmp_path / "out/views" and png.name.startswith(entry["name"] + "_")
        assert png.read_bytes()[:4] == b"\x89PNG"

    case = fake_rips.Instance.last.project.cases[0]
    assert case.input == {"SDRATIO": str(tmp_path / "sdratio.grdecl")}
    sd_top, swat, poro, tof = case.views()[1:]  # views()[0] is the view the GUI makes on load
    assert sd_top.result == ("INPUT_PROPERTY", "SDRATIO") and sd_top.cell_filters.filters == []
    assert sd_top.camera_matrix[11] == -fake_rips.TOP_DISTANCE  # top: untouched

    (rng,) = swat.cell_filters.filters
    assert isinstance(rng, fake_rips.CellRangeFilter) and rng.updated == 1 and rng.name == "swat_north range"
    starts = [getattr(rng, f"start_index_{a}") for a in "ijk"]
    counts = [getattr(rng, f"cell_count_{a}") for a in "ijk"]
    assert (starts, counts) == ([1, 2, 1], [3, 1, 2])
    assert swat.time_step == 2 and swat.grid_z_scale == 5
    assert swat.camera_matrix == rv.side_camera("north", fake_rips.TOP_DISTANCE / 2)
    assert swat.camera_matrix[11] == -500.0 and swat.camera_matrix[4:8] == [0.0, 0.0, 1.0, 0.0]

    (rng,) = poro.cell_filters.filters
    starts = [getattr(rng, f"start_index_{a}") for a in "ijk"]
    counts = [getattr(rng, f"cell_count_{a}") for a in "ijk"]
    assert (starts, counts) == ([2, 1, 2], [2, 2, 1])
    assert poro.camera_matrix == rv.side_camera("east", fake_rips.TOP_DISTANCE)

    assert tof.flow == ("TOF", "FLOW_TR_BY_SELECTION", ["I1"], []) and tof.time_step == 2


def test_render_views_failed_list_and_exit_1(gui, tmp_path, monkeypatch, capsys):
    views = [
        {"name": "ok", "property": "STATIC_NATIVE:PORO"},
        {"name": "no_ntg", "property": "STATIC_NATIVE:NTG"},
        {"name": "bad_date", "property": "DYNAMIC_NATIVE:SWAT", "time_step": "2020-01-15"},
    ]
    path = _render_case(tmp_path, views, arrays=False)
    monkeypatch.setattr(rv, "render", gui)
    assert rv.main(["--spec", str(path), "--bin", str(tmp_path / "ri")]) == 1
    manifest = json.loads((tmp_path / "out/manifest.json").read_text())
    assert [v["name"] for v in manifest["views"]] == ["ok"]
    failed = {f["name"]: f["error"] for f in manifest["failed"]}
    assert failed["no_ntg"] == "SpecError: no_ntg: STATIC_NATIVE:NTG is not in the case"
    assert failed["bad_date"].startswith("SpecError: 2020-01-15 is not a report step")
    assert (tmp_path / "out/views.rsp").is_file()  # the project is written even when a view fails
    assert json.loads(capsys.readouterr().out)["failed"] == manifest["failed"]


def test_render_views_case_without_time_steps(gui, tmp_path, monkeypatch):
    """A grid without restart has no steps; setTimeStep(0) fails there (measured), so no step is set."""
    monkeypatch.setattr(fake_rips.Instance, "case_kwargs", {"steps": 0})
    views = [
        {"name": "poro", "property": "STATIC_NATIVE:PORO", "time_step": "last"},
        {"name": "dated", "property": "STATIC_NATIVE:PORO", "time_step": "2020-01-01"},
    ]
    manifest = gui(vs.load_spec(_render_case(tmp_path, views, arrays=False)), tmp_path / "ri")
    (poro,) = manifest["views"]
    assert (poro["time_step"], poro["date"]) == (None, None)
    assert fake_rips.Instance.last.project.cases[0].views()[1].time_step == 0
    assert manifest["failed"] == [
        {"name": "dated", "error": "SpecError: dated: time_step 2020-01-01 but the case has no time steps"}
    ]


def test_render_views_array_errors_exit_2(gui, tmp_path, monkeypatch):
    path = _render_case(tmp_path, VIEWS[:1])
    monkeypatch.setattr(rv, "render", gui)
    grdecl.write_grdecl(tmp_path / "sdratio.grdecl", "SDRATIO", [1.0] * 5)
    assert rv.main(["--spec", str(path), "--bin", "x"]) == 2
    grdecl.write_grdecl(tmp_path / "sdratio.grdecl", "OTHER", [1.0] * 12)
    assert rv.main(["--spec", str(path), "--bin", "x"]) == 2
    (tmp_path / "sdratio.grdecl").unlink()
    assert rv.main(["--spec", str(path), "--bin", "x"]) == 2


def test_render_views_spec_and_case_errors_exit_2(tmp_path, capsys):
    path = _render_case(tmp_path, [{"name": "v", "property": "STATIC_NATIVE:PORO", "value": [0.5, 1]}])
    assert rv.main(["--spec", str(path), "--bin", "x"]) == 2
    assert "unknown keys ['value']" in capsys.readouterr().err
    path = _render_case(tmp_path, VIEWS[:1])
    (tmp_path / "CASE.EGRID").unlink()
    assert rv.main(["--spec", str(path), "--bin", "x"]) == 2
    assert "case not found" in capsys.readouterr().err


def test_render_views_without_resinsight_exit_2(tmp_path, monkeypatch, capsys):
    path = _render_case(tmp_path, VIEWS[:1])
    monkeypatch.delenv("RESINSIGHT_EXECUTABLE", raising=False)
    assert rv.main(["--spec", str(path)]) == 2
    assert "no ResInsight" in capsys.readouterr().err


def test_render_views_rips_missing_exit_2(tmp_path, monkeypatch, capsys):
    path = _render_case(tmp_path, VIEWS[:1])
    monkeypatch.setitem(sys.modules, "rips", None)
    assert rv.main(["--spec", str(path), "--bin", str(tmp_path / "ri")]) == 2
    assert "resinsight.python" in capsys.readouterr().err
    assert not (tmp_path / "out/manifest.json").exists()


class _StubView:
    def __init__(self, matrix):
        self.camera_matrix = matrix
        self.updated = 0

    def update(self):
        self.updated += 1


def test_apply_camera_refuses_a_matrix_it_does_not_know():
    view = _StubView([1.0] * 16)  # index 11 >= 0: not a fitted top view
    with pytest.raises(ri.RiError, match="cannot make a north view"):
        rv.apply_camera(view, "north")
    with pytest.raises(ri.RiError, match="cannot make a east view"):
        rv.apply_camera(_StubView([0.0] * 12), "east")
    top = _StubView([1.0] * 16)
    rv.apply_camera(top, "top")
    assert top.updated == 0 and top.camera_matrix == [1.0] * 16


def test_side_camera_layout():
    north, east = rv.side_camera("north", 800), rv.side_camera("east", 800)
    assert len(north) == len(east) == 16 and north[11] == east[11] == -800.0
    assert north[12:] == east[12:] == [0.0, 0.0, 0.0, 1.0]
    assert north[0:3] == [1.0, 0.0, 0.0] and east[0:3] == [0.0, 1.0, 0.0]  # camera x: east / north
    with pytest.raises(vs.SpecError):
        rv.side_camera("south", 800)


# well_views -------------------------------------------------------------------------------------------
DIMS = (4, 3, 2)
ROWS = [
    ("W1", "2020-01-01", 1, 2, 1, "ok"),
    ("W1", "2021-01-01", 1, 2, 1, "COMP-LOW-PERM"),  # the same cell later: the worst flag wins
    ("W1", "2020-01-01", 2, 2, 2, "ok"),
    ("W1", "2020-01-01", 3, 1, 1, "ok"),
    ("W2", "2020-01-01", 4, 3, 1, "COMP-ZERO-PORV"),
    ("W2", "2020-01-01", 4, 3, 2, "COMP-INACTIVE"),
    ("W2", "2020-01-01", "", "", "", "COMP-OUTSIDE"),  # completion_in_grid: I, J, K not resolved
    ("W3", "2020-01-01", 2, 1, 2, "ok"),
    ("W4", "2020-01-01", 1, 1, 2, "COMP-INACTIVE"),  # no drawn cell: in the notes, no view
]


def _completions(tmp_path: Path, rows=ROWS, dims=DIMS) -> Path:
    folder = tmp_path / "cig"
    folder.mkdir()
    (folder / "completion_in_grid.json").write_text(json.dumps({"dims": list(dims)}))
    with (folder / "connections.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["well", "date", "i", "j", "k", "status", "active", "porv", "permx", "permy", "poro", "flag"]
        )
        for well, date, i, j, k, flag in rows:
            writer.writerow([well, date, i, j, k, "OPEN", "", "", "", "", "", flag])
    return folder


def _wells(tmp_path: Path, *extra: str, folder: Path | None = None) -> int:
    egrid = tmp_path / "CASE.EGRID"
    egrid.write_bytes(b"grid")
    folder = folder or _completions(tmp_path)
    return wv.main(
        ["--completions", str(folder), "--case", str(egrid), "--out", str(tmp_path / "wv"), *extra]
    )


def _g(i, j, k):
    return (i - 1) + DIMS[0] * ((j - 1) + DIMS[1] * (k - 1))


def test_well_views_compflag_array(tmp_path):
    assert _wells(tmp_path) == 0
    keyword, values = grdecl.read_grdecl(tmp_path / "wv/COMPFLAG.grdecl", expected=24)
    expected = [0.0] * 24
    expected[_g(1, 2, 1)] = 2.0  # ok, then COMP-LOW-PERM
    expected[_g(2, 2, 2)] = expected[_g(3, 1, 1)] = expected[_g(2, 1, 2)] = 1.0
    expected[_g(4, 3, 1)] = 3.0
    assert (keyword, values) == ("COMPFLAG", expected)  # COMP-INACTIVE (4,3,2) and (1,1,2) stay 0


def test_well_views_spec_notes_and_rows(tmp_path):
    assert _wells(tmp_path, "--time-step", "2") == 0
    data = json.loads((tmp_path / "wv/spec.json").read_text())
    spec = vs.parse_spec(data)
    assert [v.name for v in spec.views] == [
        "W1_section",
        "W1_compflag",
        "W2_section",
        "W2_compflag",
        "W3_section",
        "W3_compflag",
    ]
    assert spec.arrays == (vs.ArrayInput("COMPFLAG", tmp_path / "wv/COMPFLAG.grdecl"),)
    w1 = spec.views[0]
    assert (w1.range, w1.camera, w1.zoom, w1.z_scale, w1.time_step) == (
        vs.Range(j=(2, 2)),
        "north",
        2.0,
        10.0,
        2,
    )
    assert spec.views[1].property == vs.PropertyRef("INPUT_PROPERTY", "COMPFLAG")
    assert spec.views[2].range == vs.Range(j=(3, 3)) and spec.views[4].range == vs.Range(j=(1, 1))
    assert spec.notes == (
        "W2: COMP-INACTIVE at (4,3,2) is not drawn",
        "W2: COMP-OUTSIDE at (?,?,?) is not drawn",
        "W4: COMP-INACTIVE at (1,1,2) is not drawn",
        "COMPFLAG: 0 no connection, 1 ok, 2 warning flag, 3 error flag",
    )
    wells = json.loads((tmp_path / "wv/wells.json").read_text())
    assert wells["W1"] == {"row_j": 2, "cells": 3, "hidden": []}
    assert wells["W4"] == {"row_j": None, "cells": 0, "hidden": [["1", "1", "2", "COMP-INACTIVE"]]}


def test_well_views_row_is_the_most_common_j():
    entry = wv.WellCells("W")
    for j in (1, 3, 3, 2, 3, 1):
        entry.rows[j] += 1
    assert entry.row() == 3


def test_well_views_only_flagged(tmp_path, capsys):
    assert _wells(tmp_path, "--only-flagged") == 0
    assert json.loads(capsys.readouterr().out)["wells"] == ["W1", "W2", "W4"]
    names = [v["name"] for v in json.loads((tmp_path / "wv/spec.json").read_text())["views"]]
    assert names == ["W1_section", "W1_compflag", "W2_section", "W2_compflag"]


def test_well_views_selected_wells(tmp_path):
    assert _wells(tmp_path, "--wells", "W3") == 0
    names = [v["name"] for v in json.loads((tmp_path / "wv/spec.json").read_text())["views"]]
    assert names == ["W3_section", "W3_compflag"]


def test_well_views_unknown_well_exit_2(tmp_path, capsys):
    assert _wells(tmp_path, "--wells", "W3,NOPE") == 2
    assert "wells not in connections.csv: NOPE" in capsys.readouterr().err


def test_well_views_max_wells_exit_2(tmp_path, capsys):
    assert _wells(tmp_path, "--max-wells", "2") == 2
    assert "3 wells; the limit is 2" in capsys.readouterr().err
    assert not (tmp_path / "wv/spec.json").exists()


def test_well_views_only_hidden_cells_exit_2(tmp_path, capsys):
    folder = _completions(tmp_path, rows=[ROWS[-1]])
    assert _wells(tmp_path, folder=folder) == 2
    assert "no well to show" in capsys.readouterr().err


def test_well_views_missing_inputs_exit_2(tmp_path, capsys):
    folder = _completions(tmp_path)
    (folder / "connections.csv").unlink()
    assert _wells(tmp_path, folder=folder) == 2
    assert "Run grid-property-qc completion_in_grid.py first" in capsys.readouterr().err
    (folder / "completion_in_grid.json").unlink()
    assert _wells(tmp_path, folder=folder) == 2


def test_well_views_bad_rows_exit_2(tmp_path, capsys):
    folder = _completions(tmp_path, rows=[("W1", "2020-01-01", "x", 1, 1, "ok")])
    assert _wells(tmp_path, folder=folder) == 2
    assert "connections.csv:2: i, j, k are not integers" in capsys.readouterr().err
    (folder / "connections.csv").write_text("well,i,j,k\nW1,1,1,1\n")
    assert _wells(tmp_path, folder=folder) == 2
    assert "missing columns ['flag']" in capsys.readouterr().err


def test_well_views_bad_property_exit_2(tmp_path):
    assert _wells(tmp_path, "--property", "PERMX") == 2


def test_well_views_relative_paths_and_odd_well_names(tmp_path, monkeypatch):
    folder = _completions(tmp_path, rows=[("A-1 H", "2020-01-01", 1, 1, 1, "ok")])
    (tmp_path / "CASE.EGRID").write_bytes(b"grid")
    monkeypatch.chdir(tmp_path)
    assert wv.main(["--completions", str(folder), "--case", "CASE.EGRID", "--out", "wv"]) == 0
    spec = vs.load_spec(tmp_path / "wv/spec.json")
    assert spec.case == tmp_path / "CASE.EGRID" and spec.out == tmp_path / "wv/render"
    assert [v.name for v in spec.views] == ["A-1_H_section", "A-1_H_compflag"]
