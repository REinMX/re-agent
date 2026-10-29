"""A small stand-in for the rips module: just enough surface for ri_session and the view scripts.

It copies the measured behaviour of rips 2026.9.1.2, including its gotchas (plot id -1,
available_properties raising on empty, no sim wells without a view, silent export into a missing folder,
a CellPropertyFilter that filters nothing, a fitted top camera that is the identity with -distance at index 11).
"""

from __future__ import annotations

import types
from pathlib import Path


class RipsError(Exception):
    pass


class _Obj:
    _next = 1

    def __init__(self):
        self.id = _Obj._next
        _Obj._next += 1


class Dims:
    def __init__(self, i, j, k):
        self.i, self.j, self.k = i, j, k


class Grid:
    def __init__(self, dims):
        self._dims = dims

    def dimensions(self):
        return self._dims


class CellRangeFilter(_Obj):
    """1-based start and count per axis, as in rips."""

    def __init__(self):
        super().__init__()
        self.name = ""
        for axis in "ijk":
            setattr(self, f"start_index_{axis}", 1)
            setattr(self, f"cell_count_{axis}", 1)
        self.updated = 0

    def update(self):
        self.updated += 1


class CellPropertyFilter(CellRangeFilter):
    """Measured: it shows a label in the view but filters no cell."""


class CellFilterCollection(_Obj):
    def __init__(self):
        super().__init__()
        self.filters = []

    def add_new_object(self, cls):
        item = cls()
        self.filters.append(item)
        return item


TOP_DISTANCE = 1000.0


class View(_Obj):
    def __init__(self, case):
        super().__init__()
        self.case = case
        self.result = None
        self.time_step = 0
        self.flow = None
        self.grid_z_scale = 1.0
        self.camera_matrix = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, -TOP_DISTANCE, 0, 0, 0, 1.0]
        self.cell_filters = CellFilterCollection()
        self.updated = 0

    def update(self):
        self.updated += 1

    def descendants(self, cls):
        return [self.cell_filters] if cls is CellFilterCollection else []

    def apply_cell_result(self, result_type, result_variable):
        self.result = (result_type, result_variable)

    def apply_flow_diagnostics_cell_result(
        self, result_variable="TOF", selection_mode="", injectors=None, producers=None
    ):
        self.flow = (result_variable, selection_mode, list(injectors or []), list(producers or []))
        self.result = ("FLOW_DIAGNOSTICS", result_variable)

    def set_time_step(self, time_step):
        if time_step >= self.case.steps:  # measured message for a grid without restart
            raise RipsError(
                f"setTimeStep: Step {time_step} is larger than the maximum of {self.case.steps - 1}"
            )
        self.time_step = time_step

    def export_snapshot(self, prefix="", export_folder="", width=-1, height=-1):
        if self.case.instance.console:
            raise RipsError("RicfExportSnapshot: Command cannot run without a GUI")
        folder = Path(export_folder)
        if folder.is_dir():  # a missing folder is a silent success in rips
            name = (
                f"{prefix}{self.case.name}_View_{(self.result or ('', 'none'))[1]}_{self.time_step:02d}.png"
            )
            (folder / name).write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def export_property(self, undefined_value=0.0):
        """Measured: one value per grid cell (not per active cell); a second header line names the
        tracers of a flow diagnostics result. instance.view_values: {result: values} or a function
        of the view (it may raise, as a failed export)."""
        folder = self.case.instance.export_folders.get("PROPERTIES")
        source = self.case.instance.view_values
        values = source(self) if callable(source) else source.get(self.result, [0.5] * len(self.case.actnum))
        name = f"{self.case.name}-View-T{self.time_step}-{'_'.join(map(str, self.result or ('none',)))}"
        text = f"-- Exported from ResInsight\n{(self.result or ('', 'NONE'))[1]}\n"
        if self.flow:
            text += (" ".join(self.flow[2] + self.flow[3]) or "All") + "\n"
        text += " ".join(str(v) for v in values) + "\n/\n"
        Path(folder, name).write_text(text)


class Case(_Obj):
    def __init__(self, instance, path, dims=(3, 2, 2), inactive=(1,), dynamic=(), steps=3):
        super().__init__()
        self.instance = instance
        self.path = path
        self.name = Path(path).stem
        self.dims = Dims(*dims)
        n = dims[0] * dims[1] * dims[2]
        self.actnum = [0 if g in inactive else 1 for g in range(n)]
        self.n_active = sum(self.actnum)
        self._views = [] if instance.console else [View(self)]
        self.generated = {}
        self.static = {"PORO": [0.2] * self.n_active, "PERMX": [100.0] * self.n_active}
        self.wells = instance.wells
        self.dynamic = list(dynamic)
        self.input = {}  # INPUT_PROPERTY name -> GRDECL file
        self.steps = steps

    def grid(self, index):
        return Grid(self.dims)

    def views(self):
        return list(self._views)

    def create_view(self):
        view = View(self)
        self._views.append(view)
        return view

    def simulation_wells(self):
        return list(self.wells) if self._views else []

    def available_properties(self, result_type, porosity_model="MATRIX_MODEL"):
        names = {
            "STATIC_NATIVE": list(self.static),
            "DYNAMIC_NATIVE": self.dynamic,
            "GENERATED": list(self.generated),
            "INPUT_PROPERTY": list(self.input),
        }.get(result_type, [])
        if not names:
            raise RipsError("Could not find any results matching result type")
        return names

    def cell_count(self):
        return types.SimpleNamespace(active_cell_count=self.n_active, reservoir_cell_count=len(self.actnum))

    def cell_info_for_active_cells(self):
        out = []
        for g, a in enumerate(self.actnum):
            if a:
                i = g % self.dims.i
                j = (g // self.dims.i) % self.dims.j
                k = g // (self.dims.i * self.dims.j)
                out.append(
                    types.SimpleNamespace(local_ijk=types.SimpleNamespace(i=i, j=j, k=k), grid_index=0)
                )
        return out

    def set_active_cell_property(self, values, property_type, property_name, time_step):
        if len(values) != self.n_active:
            raise RipsError("wrong length")  # the real server garbles silently; ri_session checks first
        self.generated[(property_name, time_step)] = list(values)

    def active_cell_property(self, property_type, property_name, time_step):
        if property_type == "GENERATED":
            return self.generated[(property_name, time_step)]
        return self.static[property_name]

    def export_property(
        self, time_step, property_name, eclipse_keyword=property, undefined_value=0.0, export_file=property
    ):
        if eclipse_keyword is property or export_file is property:
            raise TypeError("bad argument type for built-in operation")
        Path(export_file).write_text(f"-- Exported from ResInsight\n{eclipse_keyword}\n1 2 3 /\n")

    def time_steps(self):
        return [types.SimpleNamespace(year=2020, month=1 + n, day=1) for n in range(self.steps)]

    def import_properties(self, file_names):
        """The keyword line of each GRDECL file becomes an INPUT_PROPERTY."""
        for name in file_names:
            lines = [line.split("--", 1)[0].strip() for line in Path(name).read_text().splitlines()]
            keyword = next(line for line in lines if line)
            self.input[keyword] = name

    def export_flow_characteristics(self, **kwargs):
        Path(kwargs["file_name"]).write_text(
            "Date,StorageCapacity,FlowCapacity,SweepEfficiency,DimensionlessTime,LorentzCoefficient\n"
        )


class SummaryCase(_Obj):
    def __init__(self, path):
        super().__init__()
        self.summary_header_filename = str(path)


class Plot(_Obj):
    def __init__(self, instance, name="Plot"):
        super().__init__()
        self.instance = instance
        self.name = name

    def export_snapshot(self, export_folder="", file_prefix="", output_format="PNG", width=-1, height=-1):
        if self.instance.console:
            raise RipsError("Command cannot run without a GUI")
        if Path(export_folder).is_dir():
            Path(export_folder, f"{file_prefix}{self.name}.{output_format.lower()}").write_bytes(
                b"%PDF-1.4 fake"
            )


class SummaryPlotCollection(_Obj):
    def __init__(self, instance):
        super().__init__()
        self.instance = instance

    def new_summary_plot(self, summary_cases=None, ensemble=None, address=""):
        self.instance.plots.append(Plot(self.instance, name=address.replace(":", "_")))
        result = Plot(self.instance)
        result.id = -1  # measured: the returned object has id -1
        return result


class SummaryCaseSubCollection(_Obj):
    def __init__(self, name):
        super().__init__()
        self.summary_collection_name = name
        self.is_ensemble = True


class GridCaseGroup(_Obj):
    def __init__(self, project, paths):
        super().__init__()
        self.paths = paths
        self.stat = types.SimpleNamespace(
            dynamic_properties_to_calculate=["PRESSURE"],
            static_properties_to_calculate=["PERMX", "PORO"],
            updated=0,
        )
        self.stat.update = lambda: setattr(self.stat, "updated", self.stat.updated + 1)
        self.computed = False

    def statistics_cases(self):
        return [self.stat]

    def compute_statistics(self):
        self.computed = True


class Project:
    def __init__(self, instance):
        self.instance = instance
        self.cases = []
        self._summary = []
        self.saved = []
        self.opened = []
        self.groups = []
        self._collection = SummaryPlotCollection(instance)

    def load_case(self, path, grid_only=False):
        case = Case(self.instance, path, **self.instance.case_kwargs)
        self.cases.append(case)
        smspec = Path(path).with_suffix(".SMSPEC")
        if smspec.is_file():
            self._summary.append(SummaryCase(smspec))  # auto-loaded with the grid
        return case

    def summary_cases(self):
        return list(self._summary)

    def import_summary_case(self, path):
        if any(Path(c.summary_header_filename) == Path(path) for c in self._summary):
            raise RipsError("No result returned from Method")
        case = SummaryCase(path)
        self._summary.append(case)
        return case

    def plots(self):
        return list(self.instance.plots)

    def descendants(self, cls):
        if cls is SummaryPlotCollection:
            return [self._collection]
        if cls is SummaryCaseSubCollection:
            return [SummaryCaseSubCollection(n) for n in self.instance.opened_ensembles]
        return []

    def save(self, path):
        Path(path).write_text("<ResInsightProject/>")
        self.saved.append(path)

    def open(self, path):
        text = Path(path).read_text()
        self.instance.opened_ensembles = [
            part.split("</SummaryCollectionName>")[0] for part in text.split("<SummaryCollectionName>")[1:]
        ]
        self.opened.append(path)

    def create_grid_case_group(self, case_paths):
        group = GridCaseGroup(self, case_paths)
        self.groups.append(group)
        return group


class Instance:
    """Constructed by ri_session with Instance(port=..., launched=True)."""

    version = "2026.9.1"
    console = True
    wells: list = []
    case_kwargs: dict = {}
    fail_connect: str | None = None
    view_function = None  # view.export_property values: a function of the view
    last = None

    def __init__(self, port=50051, launched=False):
        if Instance.fail_connect:
            raise RipsError(Instance.fail_connect)
        self.port = port
        self.exited = False
        self.plots = []
        self.export_folders = {}
        self.view_values = Instance.view_function or {}
        self.opened_ensembles = []
        self.project = Project(self)
        Instance.last = self

    def version_string(self):
        return self.version

    def set_export_folder(self, export_type, path, create_folder=False):
        if create_folder:
            Path(path).mkdir(parents=True, exist_ok=True)
        self.export_folders[export_type] = path

    def exit(self):
        self.exited = True
