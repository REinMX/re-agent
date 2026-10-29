#!/usr/bin/env python3
"""A typed ResInsight session for agents. Each measured rips gotcha is handled here, once.

Run it with the Python that has rips (contract `resinsight.python`). rips is imported lazily,
so the module imports anywhere and fails closed (RipsMissing, exit 2) only when a session starts.

Gotchas encoded (ResInsight 2026.09.1, rips 2026.9.1.2, measured 2026-10-17):
 1. Own the process: launch with port 0 and a port file, never Instance.find() (it can attach to another
    agent's ResInsight on a shared host). Kill only our own process group, never `pkill -f ResInsight`.
 2. ResInsight output goes to a log file, not a pipe: an orphan holds the pipe and hangs `cmd | grep`.
 3. exit() in a finally block, then a bounded wait, then SIGTERM/SIGKILL of the group. SIGTERM to Python
    (for example from `timeout`) becomes SystemExit, so the cleanup still runs.
 4. Console mode does data; it refuses every image export. GUI mode needs a display: the session starts a
    private Xvfb. The Linux builds ship only the xcb plugin, so `-platform offscreen` is never used.
 5. rips refuses a server with another major.minor version: raise VersionMismatch, with both versions.
 6. Every export: create the folder first (a missing folder gives a silent success), then check that a new,
    non-empty file is there. The return value of an export proves nothing.
 7. Plot PNG exports are cropped: export PDF and convert with pdftoppm.
 8. new_summary_plot returns id -1: find the real plot by the before/after difference of project.plots().
 9. case.export_property defaults are the builtin `property`: always pass eclipse_keyword and export_file.
10. The summary case loads with the grid: reuse it; a second import raises "No result returned".
11. available_properties raises on an empty list: return [].
12. simulation_wells() is empty until the case has a view; console load_case makes no view.
13. filtered_cells is all-cell ordered; visible_cells is all zero in console. Map masks to active cells.
14. Grid statistics compute PRESSURE/PERMX/PORO only unless you set the lists and call update().
    Results are *_MEAN/*_P10... under the NATIVE types, not GENERATED.
15. rips cannot create a summary ensemble: write the project XML (one isolated function), then open it.
    ResInsight renames each ensemble from the realization-*/iter-* path; the written name is not kept.
16. set_active_cell_property needs exactly one value per active cell: check the length first.
17. The --portnumberfile of an earlier run in the same workdir is stale: delete it before the launch.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

Mode = Literal["console", "gui"]
EXIT_MISSING = 2


class RiError(RuntimeError):
    """A ResInsight session failure with a message an engineer can act on."""


class RipsMissing(RiError):
    pass


class LaunchError(RiError):
    pass


class VersionMismatch(RiError):
    pass


class ModeError(RiError):
    pass


class ExportError(RiError):
    pass


def import_rips():
    try:
        import rips  # noqa: PLC0415 - lazy on purpose: check scripts must not need rips
    except ImportError as error:
        raise RipsMissing(
            f"rips is not importable with {sys.executable} ({error}). "
            "Use the Python in contract resinsight.python, or install with install_resinsight.sh."
        ) from error
    return rips


def major_minor(version: str) -> tuple[int, int]:
    parts = version.strip().lstrip("v").split(".")
    return int(parts[0]), int(parts[1])


@dataclass(frozen=True)
class SessionConfig:
    executable: Path
    mode: Mode = "console"
    workdir: Path | None = None  # log, port file; default: a fresh temporary folder
    init_timeout: float = 90.0
    exit_timeout: float = 15.0
    expected_version: str | None = None  # contract resinsight.version; prefix match
    window: tuple[int, int] = (1600, 1200)
    threads: int | None = None

    def __post_init__(self):
        if self.mode not in ("console", "gui"):
            raise ValueError(f"mode must be console or gui, not {self.mode!r}")


@dataclass
class Launched:
    process: Any  # subprocess.Popen or a test double with pid/poll/wait
    port: int
    log_file: Path
    display: Any = None  # the private Xvfb, if any


def _read_port(port_file: Path, process, timeout: float, log_file: Path) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_file.is_file():
            text = port_file.read_text().strip()
            if text.isdigit() and int(text) > 0:
                return int(text)
        if process.poll() is not None:
            raise LaunchError(
                f"ResInsight exited with code {process.returncode} before it opened a port. {_tail(log_file)}"
            )
        time.sleep(0.1)
    raise LaunchError(f"ResInsight did not write a port in {timeout} s. {_tail(log_file)}")


def _tail(log_file: Path, chars: int = 600) -> str:
    try:
        return "Log tail: " + log_file.read_text(errors="replace")[-chars:].strip()
    except OSError:
        return ""


def _kill_group(process, timeout: float) -> None:
    """Stop our own ResInsight: wait, then SIGTERM, then SIGKILL its process group."""
    if process is None or process.poll() is not None:
        return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, sig)
        try:
            process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


class PrivateDisplay:
    """A private Xvfb for GUI mode, so images never use another agent's display."""

    def __init__(self, size: tuple[int, int]):
        if not shutil.which("Xvfb"):
            raise LaunchError("GUI mode needs Xvfb on PATH (images cannot be made in console mode)")
        read_fd, write_fd = os.pipe()
        self.process = subprocess.Popen(
            [
                "Xvfb",
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                f"{size[0]}x{size[1]}x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.close(write_fd)
        with os.fdopen(read_fd) as pipe:
            number = pipe.readline().strip()
        if not number.isdigit():
            _kill_group(self.process, 0.1)
            raise LaunchError("Xvfb did not report a display number")
        self.display = f":{number}"

    def stop(self) -> None:
        _kill_group(self.process, 0.1)


def launch_process(config: SessionConfig, workdir: Path) -> Launched:
    """Start ResInsight as a gRPC server on a free port. Output to a log file, own process group."""
    executable = Path(config.executable)
    if not (executable.is_file() and os.access(executable, os.X_OK)):
        raise LaunchError(f"ResInsight executable not found or not executable: {executable}")
    port_file = workdir / "port.txt"
    log_file = workdir / "resinsight.log"
    command = [str(executable), "--server", "0", "--portnumberfile", str(port_file)]
    env = dict(os.environ)
    env.pop("RESINSIGHT_GRPC_PORT", None)
    display = None
    if config.mode == "console":
        command.append("--console")
    else:
        display = PrivateDisplay(config.window)
        env["DISPLAY"] = display.display
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        command += ["--size", str(config.window[0]), str(config.window[1])]
    if config.threads:
        command += ["--threadcount", str(config.threads)]
    port_file.unlink(missing_ok=True)  # a port file of an earlier run in this workdir is stale
    with open(log_file, "ab") as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=workdir,
            start_new_session=True,
        )
    try:
        port = _read_port(port_file, process, config.init_timeout, log_file)
    except BaseException:
        _kill_group(process, 0.1)
        if display:
            display.stop()
        raise
    return Launched(process=process, port=port, log_file=log_file, display=display)


@dataclass
class Session:
    """Context manager: `with Session(SessionConfig(...)) as ri: ri.load_case(...)`."""

    config: SessionConfig
    rips: Any = None  # injected in tests; else imported lazily
    launcher: Callable[[SessionConfig, Path], Launched] = launch_process
    instance: Any = field(default=None, init=False)
    launched: Launched | None = field(default=None, init=False)
    version: str | None = field(default=None, init=False)
    _tmp: Any = field(default=None, init=False)
    _old_sigterm: Any = field(default=None, init=False)

    # lifecycle -----------------------------------------------------------------------------------------
    def __enter__(self) -> Session:
        self.rips = self.rips or import_rips()
        if self.config.workdir:
            workdir = Path(self.config.workdir)
            workdir.mkdir(parents=True, exist_ok=True)
        else:
            self._tmp = tempfile.TemporaryDirectory(prefix="ri-session-")
            workdir = Path(self._tmp.name)
        self._trap_sigterm()
        try:
            self.launched = self.launcher(self.config, workdir)
            try:
                self.instance = self.rips.Instance(port=self.launched.port, launched=True)
            except Exception as error:  # rips raises RipsError on a version mismatch
                if "version" in str(error).lower():
                    raise VersionMismatch(
                        f"{error}. rips and ResInsight need the same major.minor."
                    ) from error
                raise LaunchError(
                    f"rips could not connect: {error}. {_tail(self.launched.log_file)}"
                ) from error
            self.version = str(self.instance.version_string())
            want = (self.config.expected_version or "").strip()
            if want and not (self.version == want or self.version.startswith(want + ".")):
                raise VersionMismatch(f"contract resinsight.version is {want}, the server is {self.version}")
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self.instance is not None:
            with contextlib.suppress(Exception):
                self.instance.exit()
            self.instance = None
        if self.launched is not None:
            _kill_group(self.launched.process, self.config.exit_timeout)
            if self.launched.display:
                self.launched.display.stop()
            self.launched = None
        if self._old_sigterm is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGTERM, self._old_sigterm)
            self._old_sigterm = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def _trap_sigterm(self) -> None:
        def raise_exit(signum, frame):
            raise SystemExit(128 + signum)

        with contextlib.suppress(ValueError):  # not the main thread
            self._old_sigterm = signal.signal(signal.SIGTERM, raise_exit)

    @property
    def project(self):
        if self.instance is None:
            raise RiError("the session is not open")
        return self.instance.project

    @property
    def log_file(self) -> Path | None:
        return self.launched.log_file if self.launched else None

    def require_gui(self, what: str) -> None:
        if self.config.mode != "gui":
            raise ModeError(f"{what} needs GUI mode; console mode refuses every image export")

    # cases ---------------------------------------------------------------------------------------------
    def load_case(self, path: str | Path):
        path = Path(path)
        grid = path if path.suffix.upper() in (".EGRID", ".GRID") else path.with_suffix(".EGRID")
        if not grid.is_file():
            raise RiError(f"grid file not found: {grid}")
        return self.project.load_case(str(grid.resolve()))

    def ensure_view(self, case):
        views = case.views()
        return views[0] if views else case.create_view()

    def simulation_wells(self, case) -> list:
        self.ensure_view(case)
        return list(case.simulation_wells())

    def properties(self, case, result_type: str) -> list[str]:
        try:
            return list(case.available_properties(result_type))
        except self.rips.RipsError as error:
            if "could not find any results" in str(error).lower():
                return []
            raise

    def summary_case(self, smspec: str | Path):
        """The summary case for this SMSPEC. Reuse the one that loaded with the grid."""
        smspec = Path(smspec).resolve()
        for case in self.project.summary_cases():
            if Path(str(case.summary_header_filename)).resolve() == smspec:
                return case
        return self.project.import_summary_case(str(smspec))

    def active_count(self, case) -> int:
        return int(case.cell_count().active_cell_count)

    def set_generated(self, case, name: str, values: Sequence[float], time_step: int = 0) -> None:
        expected = self.active_count(case)
        if len(values) != expected:
            raise RiError(f"{name}: {len(values)} values, the case has {expected} active cells")
        case.set_active_cell_property([float(v) for v in values], "GENERATED", name, time_step)

    def active_mask(self, case, all_cell_mask: Sequence[int]) -> list[bool]:
        """filtered_cells gives one 0/1 per grid cell (i fastest). Return one bool per active cell."""
        dims = case.grid(0).dimensions()
        if len(all_cell_mask) != dims.i * dims.j * dims.k:
            raise RiError(
                f"mask has {len(all_cell_mask)} entries, the grid has {dims.i * dims.j * dims.k} cells"
            )
        out = []
        for cell in case.cell_info_for_active_cells():
            ijk = cell.local_ijk
            out.append(bool(all_cell_mask[ijk.i + dims.i * (ijk.j + dims.j * ijk.k)]))
        return out

    # exports -------------------------------------------------------------------------------------------
    def export_property(
        self, case, time_step: int, name: str, path: Path, keyword: str | None = None, undefined: float = 0.0
    ) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        case.export_property(
            time_step=time_step,
            property_name=name,
            eclipse_keyword=keyword or name,
            undefined_value=undefined,
            export_file=str(path.resolve()),
        )
        return _check_file(path, f"export_property {name}")

    def export_view_property(self, view, folder: Path) -> Path:
        """Export the current cell result of a view (also flow diagnostics results) as GRDECL text."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.instance.set_export_folder(
            export_type="PROPERTIES", path=str(folder.resolve()), create_folder=True
        )
        before = _listing(folder)
        view.export_property()
        return _one_new(folder, before, "view.export_property")

    def snapshot(self, view, folder: Path, prefix: str, width: int = 1400, height: int = 1000) -> Path:
        self.require_gui("A 3D view snapshot")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        before = _listing(folder)
        view.export_snapshot(prefix=prefix, export_folder=str(folder.resolve()), width=width, height=height)
        return _one_new(folder, before, f"snapshot {prefix}", suffix=".png")

    def new_summary_plot(self, summary_cases: list, address: str):
        """The real plot object. new_summary_plot itself returns an object with id -1."""
        before = {plot.id for plot in self.project.plots()}
        collection = self.project.descendants(self.rips.SummaryPlotCollection)[0]
        collection.new_summary_plot(summary_cases=summary_cases, address=address)
        new = [plot for plot in self.project.plots() if plot.id not in before]
        if len(new) != 1:
            raise RiError(f"expected one new plot for {address!r}, found {len(new)}")
        return new[0]

    def export_plot(self, plot, folder: Path, prefix: str, dpi: int = 110) -> Path:
        """PNG of a plot, through PDF (direct PNG export crops the plot)."""
        self.require_gui("A plot export")
        if getattr(plot, "id", -1) == -1:
            raise RiError("plot id is -1: use new_summary_plot() of this session to get the real plot")
        if not shutil.which("pdftoppm"):
            raise ExportError("pdftoppm (poppler-utils) is needed to make an uncropped plot PNG")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        before = _listing(folder)
        plot.export_snapshot(export_folder=str(folder.resolve()), file_prefix=prefix, output_format="PDF")
        pdf = _one_new(folder, before, f"plot {prefix}", suffix=".pdf")
        png = pdf.with_suffix("")
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-singlefile", str(pdf), str(png)], check=True)
        return _check_file(png.with_suffix(".png"), f"pdftoppm {pdf.name}")

    def save_project(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.project.save(str(path.resolve()))
        return _check_file(path, "project.save")

    # ensembles -----------------------------------------------------------------------------------------
    def grid_statistics(
        self, egrids: Sequence[Path], dynamic: Iterable[str] = (), static: Iterable[str] = ()
    ):
        """Grid case group statistics for views. Names: <PROP>_MEAN, _P10, ... under DYNAMIC/STATIC_NATIVE."""
        paths = [str(Path(p).resolve()) for p in egrids]
        missing = [p for p in paths if not Path(p).is_file()]
        if missing:
            raise RiError(f"grid files not found: {missing[:3]}")
        group = self.project.create_grid_case_group(case_paths=paths)
        stat = group.statistics_cases()[0]
        stat.dynamic_properties_to_calculate = list(dynamic)
        stat.static_properties_to_calculate = list(static)
        stat.update()
        group.compute_statistics()
        return group, stat

    def open_summary_ensembles(self, path: Path, ensembles: dict[str, Sequence[Path]]):
        write_ensemble_project(path, ensembles)
        self.project.open(str(Path(path).resolve()))
        found = [s for s in self.project.descendants(self.rips.SummaryCaseSubCollection) if s.is_ensemble]
        if len(found) != len(ensembles):
            raise RiError(f"{len(ensembles)} ensembles written, ResInsight opened {len(found)}")
        return found


def write_ensemble_project(
    path: Path, ensembles: dict[str, Sequence[Path]], version: str = "2026.09.1"
) -> Path:
    """Project XML with summary ensembles. rips has no create-ensemble call (2026.9.1.2).

    Undocumented schema, isolated here. Class keyword is ResInsightOilField (RimOilField loads nothing).
    """
    if not ensembles:
        raise RiError("no ensembles to write")
    refs, groups, number = [], [], 0
    for group_id, (name, members) in enumerate(ensembles.items()):
        if not members:
            raise RiError(f"ensemble {name!r} has no members")
        cases = []
        for member in members:
            member = Path(member).resolve()
            if not member.is_file():
                raise RiError(f"summary file not found: {member}")
            number += 1
            refs.append(f"        $PathId_{number:03d}$ {escape(str(member))};")
            cases.append(
                f"<FileSummaryCase><ShortName>{escape(member.stem)}</ShortName>"
                f"<SummaryHeaderFilename>$PathId_{number:03d}$</SummaryHeaderFilename><Id>{number}</Id>"
                "<IncludeRestartFiles>False </IncludeRestartFiles></FileSummaryCase>"
            )
        groups.append(
            f"<SummaryCaseSubCollection><SummaryCollectionName>{escape(name)}</SummaryCollectionName>"
            f"<CreateAutoName>False </CreateAutoName><Id>{group_id}</Id><IsEnsemble>True </IsEnsemble>"
            f"<SummaryCases>{''.join(cases)}</SummaryCases></SummaryCaseSubCollection>"
        )
    newline = "\n"
    xml = f"""<?xml version="1.0"?>
<ResInsightProject>
    <ProjectFileVersionString>{escape(version)}</ProjectFileVersionString>
    <ReferencedExternalFiles>
{newline.join(refs)}
    </ReferencedExternalFiles>
    <OilFields><ResInsightOilField>
        <SummaryCaseCollection><SummaryCaseCollection>
            <SummaryCases/>
            <SummaryCaseCollections>{"".join(groups)}</SummaryCaseCollections>
        </SummaryCaseCollection></SummaryCaseCollection>
    </ResInsightOilField></OilFields>
</ResInsightProject>
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    return path


def _listing(folder: Path) -> dict[str, float]:
    return {p.name: p.stat().st_mtime for p in folder.iterdir() if p.is_file()}


def _one_new(folder: Path, before: dict[str, float], what: str, suffix: str | None = None) -> Path:
    after = _listing(folder)
    new = [
        folder / name
        for name, mtime in after.items()
        if (name not in before or mtime > before[name]) and (suffix is None or name.lower().endswith(suffix))
    ]
    new = [p for p in new if p.stat().st_size > 0]
    if not new:
        raise ExportError(f"{what}: ResInsight reported success but wrote no new file in {folder}")
    if len(new) > 1:
        raise ExportError(f"{what}: expected one new file, found {sorted(p.name for p in new)}")
    if new[0].name.startswith("."):
        raise ExportError(f"{what}: the file has no name ({new[0].name}); give the object a name")
    return new[0]


def _check_file(path: Path, what: str) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise ExportError(f"{what}: no file or an empty file at {path}")
    return path


# CLI: a smoke run ---------------------------------------------------------------------------------------
def smoke(args) -> dict:
    config = SessionConfig(
        executable=Path(args.bin), mode=args.mode, expected_version=args.expect_version, workdir=args.workdir
    )
    t0 = time.perf_counter()
    result: dict[str, Any] = {"mode": args.mode}
    with Session(config) as ri:
        result["version"] = ri.version
        result["pid"] = ri.launched.process.pid
        result["launch_s"] = round(time.perf_counter() - t0, 2)
        if args.case:
            case = ri.load_case(args.case)
            dims = case.grid(0).dimensions()
            result["case"] = {
                "name": case.name,
                "dims": [dims.i, dims.j, dims.k],
                "active": ri.active_count(case),
            }
            if args.out and args.mode == "gui":
                view = ri.ensure_view(case)
                static = ri.properties(case, "STATIC_NATIVE")
                if "PORO" in static:
                    view.apply_cell_result(result_type="STATIC_NATIVE", result_variable="PORO")
                result["snapshot"] = str(ri.snapshot(view, Path(args.out), "smoke_"))
            if args.out:
                result["project"] = str(ri.save_project(Path(args.out) / "smoke.rsp"))
        result["log"] = str(ri.log_file)
    result["total_s"] = round(time.perf_counter() - t0, 2)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ResInsight session smoke run (launch, load, snapshot, exit)."
    )
    parser.add_argument(
        "--bin", default=os.environ.get("RESINSIGHT_EXECUTABLE"), help="ResInsight or launcher"
    )
    parser.add_argument("--mode", choices=("console", "gui"), default="console")
    parser.add_argument("--case", default=None, help="EGRID (or BASE) to load")
    parser.add_argument("--out", default=None, help="folder for the snapshot (gui) and smoke.rsp")
    parser.add_argument("--expect-version", default=None)
    parser.add_argument("--workdir", type=Path, default=None, help="keep the log here (default: temporary)")
    args = parser.parse_args(argv)
    if not args.bin:
        sys.stderr.write("error: --bin or RESINSIGHT_EXECUTABLE is required\n")
        return EXIT_MISSING
    try:
        rips = import_rips()
        print(json.dumps(smoke(args), indent=2))
    except RipsMissing as error:
        sys.stderr.write(f"error: {error}\n")
        return EXIT_MISSING
    except (RiError, rips.RipsError) as error:
        sys.stderr.write(f"error: {type(error).__name__}: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
