"""Figures of ahm_status.py. Each function returns a matplotlib Figure, or None when it has nothing to show."""

from __future__ import annotations

import math
import textwrap
import warnings
from dataclasses import dataclass
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.ticker import NullFormatter, ScalarFormatter  # noqa: E402

POSTERIOR = "#2a78d6"
PRIOR = "#6250d6"
OBSERVED = "#e34948"
HISTORY = "#0b0b0b"
MEMBER = "#b5b4ae"
MUTED = "#52514e"
BAND_LABEL = "P10/P90 numerical: P10 is the low value"

plt.rcParams.update(
    {
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#e6e5e0",
        "grid.linewidth": 0.6,
    }
)


@dataclass
class Context:
    title: str
    eoh: datetime | None
    observations: list[dict]
    prior_frames: dict | None = None
    prior_label: str | None = None


def close(figure) -> None:
    plt.close(figure)


PRESSURE = ("FPR", "WBHP", "WBP9", "WBP4", "WBP")
PRESSURE_MEANING = {
    "WBP9": "9-point well-block average reservoir pressure",
    "WBP4": "4-point well-block average (fallback, not WBP9)",
    "WBP": "well-block pressure (fallback, not WBP9)",
    "WBHP": "flowing bottom-hole pressure (WBHP), not average reservoir pressure",
}
FRACTIONS = ("WWCT", "FWCT")
PILOT_PANELS = 9


def _stack(frames: dict, key: str):
    """Members on the date axis of the longest member. None when no member has the vector.
    A pressure of 0 is a shut well, not a pressure, so it is not drawn."""
    have = [f for f in frames.values() if key in f.columns]
    if not have:
        return None, None
    reference = max(have, key=len).index
    x = reference.as_unit("ns").asi8.astype(float)
    rows = np.vstack(
        [np.interp(x, f.index.as_unit("ns").asi8.astype(float), f[key].to_numpy(dtype=float)) for f in have]
    )
    if key.split(":")[0] in PRESSURE:
        rows[rows == 0] = np.nan
    return reference, rows


def is_flat_zero(frames: dict, key: str) -> bool:
    _, values = _stack(frames, key)
    return values is not None and not np.nanmax(np.abs(values), initial=0) > 0


def unit_of(frames: dict, key: str) -> str:
    for frame in frames.values():
        if key in frame.attrs.get("units", {}):
            return frame.attrs["units"][key]
    return ""


def _history_key(key: str) -> str:
    family, sep, rest = key.partition(":")
    return f"{family}H{sep}{rest}"


def band(ax, frames: dict, key: str, context: Context, *, legend: bool = False) -> bool:
    dates, values = _stack(frames, key)
    if values is None:
        ax.text(
            0.5,
            0.5,
            f"{key} missing in SUMMARY",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=MUTED,
            fontsize=10,
        )
        ax.set_title(key)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        return False
    if context.prior_frames:
        prior_dates, prior = _stack(context.prior_frames, key)
        if prior is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                p10, p90 = np.nanpercentile(prior, [10, 90], axis=0)
            ax.fill_between(
                prior_dates,
                p10,
                p90,
                color=PRIOR,
                alpha=0.12,
                linewidth=0,
                label=f"{context.prior_label} P10-P90",
            )
    for index, row in enumerate(values):
        ax.plot(
            dates,
            row,
            color=MEMBER,
            linewidth=0.4,
            alpha=0.6,
            zorder=1,
            label="members" if index == 0 else None,
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        p10, p50, p90 = np.nanpercentile(values, [10, 50, 90], axis=0)
    ax.fill_between(
        dates, p10, p90, color=POSTERIOR, alpha=0.25, linewidth=0, label="posterior P10-P90", zorder=2
    )
    ax.plot(dates, p50, color=POSTERIOR, linewidth=2, label="posterior P50", zorder=3)
    history = _history_key(key)
    hist_frames = [f for f in frames.values() if history in f.columns]
    if hist_frames:
        ax.plot(
            hist_frames[0].index,
            hist_frames[0][history],
            color=HISTORY,
            linewidth=1.2,
            label="history (*H)",
            zorder=4,
        )
    obs = [o for o in context.observations if o["key"] == key]
    if obs:
        ax.errorbar(
            [datetime.fromisoformat(o["date"]) for o in obs],
            [o["value"] for o in obs],
            yerr=[o["error"] for o in obs],
            fmt="o",
            color=OBSERVED,
            markersize=3.5,
            elinewidth=0.8,
            capsize=1.5,
            label="observed",
            zorder=5,
        )
    if context.eoh is not None:
        ax.axvline(context.eoh, color=MUTED, linestyle="--", linewidth=0.9)
    if key.split(":")[0] in FRACTIONS:
        data = [values.ravel(), [o["value"] for o in obs]]
        data += [f[history].to_numpy(dtype=float) for f in hist_frames[:1]]
        pooled = np.concatenate([np.asarray(d, dtype=float) for d in data])
        pooled = pooled[np.isfinite(pooled)]
        low, high = float(pooled.min()), float(pooled.max())
        pad = 0.05 * (high - low) or 0.01
        ax.set_ylim(max(0.0, low - pad), high + pad)
    note = [] if hist_frames or key in PRESSURE else [f"no {history}"]
    ax.set_title(key + (f"  ({', '.join(note)})" if note else ""))
    ax.set_ylabel(unit_of(frames, key))
    ax.tick_params(axis="x", rotation=0)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("center")
    if legend:
        ax.legend(loc="best", frameon=False)
    return True


def _grid(n: int, columns: int, width: float = 5.0, height: float = 3.3):
    rows = max(1, math.ceil(n / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(width * columns, height * rows + 0.8), squeeze=False)
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    return figure, axes.flat


def _finish(figure, title: str, context: Context, note: str = "") -> None:
    height = figure.get_figheight()
    figure.suptitle(
        f"{context.title}: {title}", fontsize=13, x=0.01, y=1 - 0.12 / height, ha="left", va="top"
    )
    figure.text(
        0.01,
        1 - 0.45 / height,
        " ".join([BAND_LABEL + ". Dashed line: end of history.", note]).strip(),
        ha="left",
        va="top",
        fontsize=9,
        color=MUTED,
    )
    handles, labels = [], []
    for ax in figure.axes:
        for handle, label in zip(*ax.get_legend_handles_labels(), strict=True):
            if label not in labels:
                handles.append(handle)
                labels.append(label)
    if handles:
        figure.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)
    figure.tight_layout(rect=(0, 0.45 / height, 1, 1 - 0.7 / height))


def field_grid(frames: dict, panels, context: Context, columns: int, title: str):
    figure, axes = _grid(len(panels), columns, 5.0, 3.4)
    columns_present = set().union(*(f.columns for f in frames.values()))
    for ax, choices in zip(axes, panels, strict=False):
        key = next((c for c in choices if c in columns_present), choices[0])
        band(ax, frames, key, context)
        if key not in columns_present and len(choices) > 1:
            ax.set_title(" / ".join(choices))
            ax.texts[0].set_text(f"{' and '.join(choices)} missing in SUMMARY")
    _finish(figure, title, context)
    return figure


def well_grid(frames: dict, mnemonic: str, wells: list[str], context: Context):
    columns_present = set().union(*(f.columns for f in frames.values()))
    present = [f"{mnemonic}:{w}" for w in wells if f"{mnemonic}:{w}" in columns_present]
    observed = {o["key"] for o in context.observations}
    keys = [k for k in present if k in observed or not is_flat_zero(frames, k)]
    if not keys:
        return None
    figure, axes = _grid(len(keys), min(3, len(keys)), 4.6, 3.0)
    for ax, key in zip(axes, keys, strict=False):
        band(ax, frames, key, context)
    skipped = [k.split(":", 1)[1] for k in present if k not in keys]
    _finish(
        figure,
        f"{mnemonic} per well",
        context,
        f"Not shown (zero in all members, no observation): {', '.join(skipped)}." if skipped else "",
    )
    return figure


def pressure_grid(
    frames: dict, choice: dict[str, str], wells: list[str], context: Context, rft, pilot: list[str] = ()
):
    keys = [f"{choice[w]}:{w}" for w in wells if w in choice]
    pilot = list(pilot)[:PILOT_PANELS]
    has_rft = rft is not None and not rft.empty
    panels = len(keys) + len(pilot) + (1 if has_rft or not pilot else 0)
    figure, axes = _grid(panels, min(3, panels), 4.6, 3.0)
    axes = list(axes)
    for ax, key in zip(axes, keys, strict=False):
        band(ax, frames, key, context)
        ax.set_title(f"{key}  (mnemonic used: {key.split(':')[0]})")
    for ax, key in zip(axes[len(keys) :], pilot, strict=False):
        band(ax, frames, key, context)
        ax.set_title(f"{key}  (pilot pressure, block)")
    if has_rft:
        ax = axes[len(keys) + len(pilot)]
        post = rft[rft["ENSEMBLE"] == "posterior"]
        points = post.groupby("OBS").agg(
            OBSERVED=("OBSERVED", "first"),
            ERROR=("ERROR", "first"),
            SIM=("SIMULATED", "mean"),
            LOW=("SIMULATED", "min"),
            HIGH=("SIMULATED", "max"),
        )
        ax.errorbar(
            points["OBSERVED"],
            points["SIM"],
            yerr=[points["SIM"] - points["LOW"], points["HIGH"] - points["SIM"]],
            xerr=points["ERROR"],
            fmt="o",
            color=POSTERIOR,
            ecolor=MEMBER,
            markersize=4,
        )
        low, high = (
            np.nanmin(points[["OBSERVED", "LOW"]].to_numpy()),
            np.nanmax(points[["OBSERVED", "HIGH"]].to_numpy()),
        )
        ax.plot([low, high], [low, high], color=HISTORY, linewidth=0.8)
        ax.set_xlabel("observed (error bar = obs error)")
        ax.set_ylabel("simulated mean (bar = min-max)")
        ax.set_title(f"RFT points ({len(points)})")
    elif not pilot:
        ax = axes[len(keys)]
        ax.text(
            0.5,
            0.5,
            "no RFT observations\n(no GENERAL_OBSERVATION in the misfit)",
            ha="center",
            va="center",
            transform=ax.transAxes,
            color=MUTED,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        ax.set_title("RFT")
    used = sorted(set(choice.values()))
    title = "pressure per well: " + ("; ".join(f"{m} = {PRESSURE_MEANING[m]}" for m in used) or "none")
    _finish(
        figure, title, context, "Order: WBP9 > WBP4 > WBP > WBHP. A pressure of 0 (shut well) is not drawn."
    )
    return figure


def heatmap(table, context: Context):
    if table is None or table.empty:
        return None
    values = table.to_numpy(dtype=float)
    figure, ax = plt.subplots(figsize=(1.4 * table.shape[1] + 3, 0.5 * table.shape[0] + 2.2))
    top = max(4.0, float(np.nanmax(values)) if np.isfinite(values).any() else 4.0)
    norm = TwoSlopeNorm(vmin=0.0, vcenter=1.0, vmax=top)
    image = ax.imshow(values, cmap="RdBu_r", norm=norm, aspect="auto")
    ax.set_xticks(range(table.shape[1]), table.columns)
    ax.set_yticks(range(table.shape[0]), table.index)
    ax.grid(False)
    for (i, j), value in np.ndenumerate(values):
        if np.isfinite(value):
            ax.text(
                j,
                i,
                f"{value:.2f}",
                ha="center",
                va="center",
                fontsize=9,
                color="white" if norm(value) > 0.8 or norm(value) < 0.15 else HISTORY,
            )
        else:
            ax.text(j, i, "no obs", ha="center", va="center", fontsize=8, color=MUTED)
    figure.colorbar(image, ax=ax, label="mean squared normalized residual (1 = at the error)")
    ax.set_title(f"{context.title}: misfit per well and vector family (posterior)", loc="left")
    figure.tight_layout()
    return figure


def worst_wells(table, context: Context, limit: float = 4.0):
    if table is None or table.empty:
        return None
    top = table.head(15).iloc[::-1]
    figure, ax = plt.subplots(figsize=(8, 0.4 * len(top) + 1.8))
    colors = [OBSERVED if v > limit else POSTERIOR for v in top["MEAN_SQ_NORM_RESIDUAL"]]
    ax.set_axisbelow(True)
    ax.barh(top["WELL"], top["MEAN_SQ_NORM_RESIDUAL"], color=colors, height=0.6)
    ax.axvline(1, color=MUTED, linewidth=0.8, linestyle=":")
    ax.axvline(limit, color=OBSERVED, linewidth=0.8, linestyle="--")
    for y, value in enumerate(top["MEAN_SQ_NORM_RESIDUAL"]):
        ax.text(value, y, f" {value:.2f}", va="center", fontsize=8, color=HISTORY)
    ax.set_xlabel(f"mean squared normalized residual (dotted 1 = at the error, dashed {limit:g} = caveat)")
    ax.set_title(f"{context.title}: worst wells (posterior)", loc="left")
    ax.grid(axis="y", visible=False)
    figure.tight_layout()
    return figure


def misfit_hist(real_misfit, medians, context: Context, outliers: list[dict] = ()):
    several = medians is not None and len(medians) > 1
    figure, axes = plt.subplots(1, 2 if several else 1, figsize=(11 if several else 7, 3.8), squeeze=False)
    ax = axes[0][0]
    data = {label: group["MISFIT"].dropna() for label, group in real_misfit.groupby("ENSEMBLE")}
    pooled = np.concatenate([v.to_numpy() for v in data.values()])
    wide = pooled.min() > 0 and pooled.max() / pooled.min() > 50
    edges = (
        np.logspace(np.log10(pooled.min()), np.log10(pooled.max()), 25)
        if wide
        else np.histogram_bin_edges(pooled, bins=20)
    )
    for label, color in (("prior", PRIOR), ("posterior", POSTERIOR)):
        if label in data:
            name = context.prior_label if label == "prior" else "posterior"
            ax.hist(
                data[label],
                bins=edges,
                color=color,
                alpha=0.55,
                label=f"{name} (median {data[label].median():.2f})",
                edgecolor="white",
                linewidth=0.8,
            )
    ax.axvline(1, color=MUTED, linestyle=":", linewidth=0.9)
    if outliers:
        ax.axvline(outliers[0]["limit"], color=OBSERVED, linestyle="--", linewidth=0.9)
        ax.plot([o["misfit"] for o in outliers], [0] * len(outliers), "|", color=OBSERVED, markersize=14)
        ax.text(
            0.99,
            0.6,
            textwrap.fill(
                "outliers (> median + 3 * 1.4826 MAD): real "
                + ", ".join(str(o["realization"]) for o in outliers),
                40,
            ),
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            color=OBSERVED,
        )
    if wide:
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.set_xlabel("misfit per realization (mean squared normalized residual)")
    ax.set_ylabel("realizations")
    ax.legend(frameon=False)
    ax.set_title("Misfit per realization", loc="left")
    if several:
        ax = axes[0][1]
        ax.plot(medians["ITERATION"], medians["MEDIAN_MISFIT"], marker="o", color=POSTERIOR, linewidth=2)
        for _, row in medians.iterrows():
            ax.annotate(
                f"{row['MEDIAN_MISFIT']:.2f}",
                (row["ITERATION"], row["MEDIAN_MISFIT"]),
                textcoords="offset points",
                xytext=(0, 7),
                ha="center",
                fontsize=8,
            )
        ax.set_xticks(medians["ITERATION"], [f"iter-{int(i)}" for i in medians["ITERATION"]])
        ax.set_ylabel("median misfit")
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_title("Median misfit by iteration", loc="left")
    figure.suptitle(context.title, x=0.01, ha="left")
    figure.tight_layout()
    return figure


def params(post: dict[str, list], prior: dict[str, list] | None, at_bound: set[str], context: Context):
    if not post:
        return None
    names = list(post)
    figure, ax = plt.subplots(figsize=(9, 0.38 * len(names) + 1.8))
    rng = np.random.default_rng(0)
    for y, name in enumerate(names):
        if prior and prior.get(name):
            values = np.array(prior[name])
            ax.scatter(
                values,
                y + 0.18 + rng.uniform(-0.06, 0.06, len(values)),
                s=9,
                color=PRIOR,
                alpha=0.4,
                linewidths=0,
            )
        values = np.array(post[name])
        ax.scatter(
            values,
            y - 0.1 + rng.uniform(-0.1, 0.1, len(values)),
            s=10,
            color=POSTERIOR,
            alpha=0.6,
            linewidths=0,
        )
        ax.plot(
            [np.percentile(values, 10), np.percentile(values, 90)], [y - 0.32] * 2, color=HISTORY, linewidth=2
        )
        ax.plot(values.mean(), y - 0.32, marker="|", color=HISTORY, markersize=9)
    ax.set_yticks(range(len(names)), [f"{n}  [AT BOUND]" if n in at_bound else n for n in names])
    for tick in ax.get_yticklabels():
        if "AT BOUND" in tick.get_text():
            tick.set_color(OBSERVED)
    for bound in (0, 1):
        ax.axvline(bound, color=HISTORY, linewidth=1.2)
    for edge in (0.1, 0.9):
        ax.axvline(edge, color=MUTED, linewidth=0.7, linestyle=":")
    ax.set_xlim(-0.05, 1.05)
    ax.set_ylim(-0.7, len(names) - 0.3)
    ax.invert_yaxis()
    ax.set_xlabel("position in the prior range from the .dist bounds (0 = min, 1 = max; log for LOGUNIF)")
    legend = [
        plt.Line2D([], [], marker="o", linestyle="", color=POSTERIOR, label="posterior members"),
        plt.Line2D([], [], color=HISTORY, linewidth=2, label="posterior P10-P90, | mean"),
    ]
    if prior:
        legend.insert(0, plt.Line2D([], [], marker="o", linestyle="", color=PRIOR, label="prior members"))
    figure.legend(handles=legend, loc="lower center", ncol=3, frameon=False)
    ax.set_title(
        f"{context.title}: parameters" + ("" if prior else " (posterior only, iter-0 gone)"), loc="left"
    )
    ax.grid(axis="y", visible=False)
    figure.tight_layout(rect=(0, 0.5 / figure.get_figheight(), 1, 1))
    return figure


def _text_page(pdf, title: str, blocks: list[str]) -> None:
    figure = plt.figure(figsize=(11.69, 8.27))
    figure.text(0.04, 0.95, title, fontsize=15, weight="bold", va="top")
    y = 0.9
    for block in blocks:
        for line in block.splitlines() or [""]:
            if y < 0.04:
                pdf.savefig(figure)
                plt.close(figure)
                figure = plt.figure(figsize=(11.69, 8.27))
                y = 0.95
            figure.text(0.04, y, line, fontsize=8.5, family="monospace", va="top")
            y -= 0.018
        y -= 0.012
    pdf.savefig(figure)
    plt.close(figure)


def _wrap(text: str, width: int = 120) -> str:
    return "\n".join(textwrap.wrap(text, width)) or text


def _frame_text(frame, columns=None) -> str:
    if frame is None or len(frame) == 0:
        return "(none)"
    columns = [c for c in (columns or frame.columns) if c in frame.columns]
    return frame[columns].to_string(index=False, float_format=lambda v: f"{v:.4g}", max_colwidth=40)


def _number(value) -> str:
    if isinstance(value, float | int | np.floating | np.integer) and math.isfinite(value):
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.3g}"
    return str(value)


def _table(figure, rect, title: str, frame, columns: list[str], headers: list[str], widths=None) -> None:
    ax = figure.add_axes(rect)
    ax.axis("off")
    ax.set_title(title, loc="left", fontsize=11, weight="bold")
    if frame is None or len(frame) == 0:
        ax.text(0, 0.9, "(none)", fontsize=10, va="top")
        return
    rows = [[_number(row[c]) for c in columns] for _, row in frame[columns].iterrows()]
    table = ax.table(
        cellText=rows, colLabels=headers, colWidths=widths, loc="upper left", cellLoc="right", colLoc="right"
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9.5)
    table.scale(1, 1.45)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if row == 0:
            cell.set_facecolor("#eef2f7")
            cell.set_text_props(weight="bold")


def _headline_page(document, pages: dict, figures: dict, context: Context) -> None:
    figure = plt.figure(figsize=(11.69, 8.27))
    colour = {"Not ready": OBSERVED, "Use with caveats": "#c98500", "Fit for use": "#008300"}[
        pages["verdict"]
    ]
    figure.text(0.025, 0.97, context.title, fontsize=13, weight="bold", va="top")
    figure.text(
        0.025, 0.935, f"Verdict: {pages['verdict']}", fontsize=20, weight="bold", color=colour, va="top"
    )
    counted = [_wrap("- " + e, 150) for e in pages["evidence"]] or ["- No rule fired."]
    codes = [e.split("]")[0].lstrip("[") for e in pages.get("not_counted", [])]
    shown = (
        [_wrap(f"- Shown in the report, not counted: {', '.join(dict.fromkeys(codes))}", 150)]
        if codes
        else []
    )
    warnings_ = [_wrap("- " + w, 150) for w in pages.get("warnings", [])]
    lines = "\n".join(
        counted + warnings_ + shown + [_wrap("- " + pages["outliers"], 150), _wrap(pages["inventory"], 150)]
    )
    text = figure.text(0.025, 0.87, lines, fontsize=8.8, va="top", linespacing=1.35)
    figure.canvas.draw()
    box = text.get_window_extent().transformed(figure.transFigure.inverted())
    top = box.y0 - 0.02
    headline = figures.get("headline_field")
    if headline is not None:
        headline.canvas.draw()
        ax = figure.add_axes((0.0, 0.05, 0.6, top - 0.05))
        ax.imshow(np.asarray(headline.canvas.buffer_rgba()))
        ax.axis("off")
    worst = pages["worst"]
    _table(
        figure,
        (0.61, top - 0.27, 0.37, 0.25),
        "Worst 5 wells",
        worst,
        ["WELL", "OBS", "MEAN_SQ_NORM_RESIDUAL"],
        ["Well", "Obs", "Mean sq. residual"],
        widths=[0.25, 0.2, 0.55],
    )
    forecast = pages["forecast"]
    if forecast is not None and len(forecast) and "AT" in forecast:
        forecast = forecast[forecast["AT"] == "end of history"]
    _table(
        figure,
        (0.61, 0.1, 0.37, top - 0.42),
        f"End of history {pages['eoh']}",
        forecast,
        ["VECTOR", "UNIT", "MEMBERS", "P10", "P50", "P90"],
        ["Vector", "Unit", "N", "P10", "P50", "P90"],
        widths=[0.14, 0.12, 0.08, 0.22, 0.22, 0.22],
    )
    if pages.get("history_controlled"):
        figure.text(
            0.61, 0.098, _wrap(pages["history_controlled"], 70), fontsize=8.5, va="bottom", color=MUTED
        )
    figure.text(0.61, 0.075, pages["convention"], fontsize=9, weight="bold", va="top")
    figure.text(0.025, 0.025, pages["advisory"], fontsize=8.5, style="italic", color=MUTED, va="bottom")
    document.savefig(figure)
    plt.close(figure)


def pdf(path, pages: dict, figures: dict, context: Context) -> None:
    with PdfPages(path) as document:
        _headline_page(document, pages, figures, context)
        for figure in figures.values():
            if figure is not None:
                document.savefig(figure)
        _text_page(
            document,
            "Tables",
            [
                "Vector families\n" + _frame_text(pages["families"]),
                "Forecast\n" + _frame_text(pages["forecast"]) + "\n" + pages["convention"],
                "Parameters\n"
                + _frame_text(
                    pages["parameters"],
                    [
                        "PARAMETER",
                        "DISTRIBUTION",
                        "STATUS",
                        "POST_MEAN_POS",
                        "POST_SD_POS",
                        "SD_RATIO",
                        "SHARE_NEAR_BOUND",
                        "AT_BOUND",
                    ],
                ),
                "Review items\n"
                + _wrap(pages["caveat"])
                + "\n"
                + "\n".join(_wrap("- " + r) for r in pages["review"]),
            ],
        )
