"""
figures.py - Phase 6 charts for the report (matplotlib). Colours follow the validated default palette
(blue / orange / aqua for up to three series, one-hue blue ramp for the confusion matrix).
Every chart has a title that states what it shows, labelled axes with units, a legend when there is
more than one series, and light recessive gridlines.
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, PowerNorm

SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SEQ = ["#ffffff", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SHORT = {"RECORD_VERIFIED": "Verified", "DUPLICATE_SERIAL_ALERT": "Duplicate serial",
         "UNREGISTERED_PRODUCT_ALERT": "Unregistered", "UNAUTHORIZED_TRANSFER_ALERT": "Unauthorized transfer",
         "DATA_MISMATCH_ALERT": "Data mismatch", "TRACEABILITY_WARNING": "Traceability gap",
         "EXPIRY_WARNING": "Expired"}
SYSTEMS = {"baseline": ("Baseline (SQLite only)", BLUE), "blockchain": ("Blockchain prototype (SQLite + hash chain)", ORANGE)}


def _ax(figsize=(8.2, 4.8)):
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})
    fig, ax = plt.subplots(figsize=figsize, facecolor=SURF)
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    return fig, ax


def _save(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURF, bbox_inches="tight")
    plt.close(fig)


def _series(perf, system, key, err=None):
    rows = sorted((r for r in perf if r["system"] == system), key=lambda r: r["n_events"])
    return [r["n_events"] for r in rows], [r[key] for r in rows], ([r[err] for r in rows] if err else None)


def latency(perf, out):
    fig, ax = _ax()
    for system, (label, color) in SYSTEMS.items():
        x, y, e = _series(perf, system, "latency_ms_mean", "latency_ms_std")
        ax.errorbar(x, y, yerr=e, color=color, lw=2, marker="o", ms=8, mec=SURF, mew=2, capsize=3,
                    elinewidth=1, label=label)
        ax.annotate(f"{y[-1]:.2f} ms", (x[-1], y[-1]), textcoords="offset points", xytext=(8, -3),
                    color=INK, fontsize=9)
    ax.set_xscale("log")
    xs = _series(perf, "baseline", "latency_ms_mean")[0]
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{n:,}" for n in xs])
    ax.minorticks_off()
    ax.set_xlim(xs[0] * 0.8, xs[-1] * 1.6)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Events recorded (log scale)")
    ax.set_ylabel("Mean latency per event (ms)")
    ax.set_title("Event-recording latency vs number of events", loc="left", fontweight="bold")
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.legend(frameon=False, loc="lower center")             # the lower half of the plot is empty
    fig.text(0.01, -0.02, "Mean of repeated runs; error bars = 1 standard deviation across runs.",
             color=INK2, fontsize=8)
    _save(fig, os.path.join(out, "fig_latency_vs_events.png"))


def grouped_bars(perf, key, ylabel, title, fname, out, fmt, note=None):
    fig, ax = _ax()
    sizes = sorted({r["n_events"] for r in perf})
    w = 0.38
    ymax = max(r[key] for r in perf)
    ax.set_ylim(0, ymax * 1.3)                                # headroom so the legend never sits on a bar
    for k, (system, (label, color)) in enumerate(SYSTEMS.items()):
        x, y, _ = _series(perf, system, key)
        pos = [i + (k - 0.5) * (w + 0.03) for i in range(len(sizes))]
        ax.bar(pos, y, width=w, color=color, label=label, zorder=3)
        ax.annotate(fmt(y[-1]), (pos[-1], y[-1]), textcoords="offset points", xytext=(0, 4), ha="center",
                    fontsize=9, color=INK)
    ax.set_xticks(range(len(sizes)))
    ax.set_xticklabels([f"{n:,}" for n in sizes])
    ax.set_xlabel("Events recorded")
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", fontweight="bold")
    ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
    ax.legend(frameon=False, loc="upper left")
    if note:
        fig.text(0.01, -0.02, note, color=INK2, fontsize=8)
    _save(fig, os.path.join(out, fname))


def confusion(labels, matrix, out):
    fig, ax = _ax((8.4, 6.2))
    n = len(labels)
    data = [[matrix[(e, a)] for a in labels] for e in labels]
    cmap = LinearSegmentedColormap.from_list("blue", SEQ)
    top = max(max(r) for r in data)
    norm = PowerNorm(gamma=0.45, vmin=0, vmax=top)         # square-root-like scale keeps small counts visible
    ax.imshow(data, cmap=cmap, norm=norm)
    for i in range(n):
        for j in range(n):
            v = data[i][j]
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=10, fontweight="bold",
                        color="#ffffff" if norm(v) > 0.6 else INK)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels([SHORT[l] for l in labels], rotation=35, ha="right")
    ax.set_yticklabels([SHORT[l] for l in labels])
    ax.set_xlabel("Status reported by the verification engine")
    ax.set_ylabel("Expected status (ground truth)")
    ax.set_title("Dataset evaluation: expected vs reported status (serial numbers)", loc="left", fontweight="bold")
    ax.set_xticks([i - 0.5 for i in range(n + 1)], minor=True)
    ax.set_yticks([i - 0.5 for i in range(n + 1)], minor=True)
    ax.grid(which="minor", color=SURF, lw=2)
    ax.tick_params(which="minor", length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    _save(fig, os.path.join(out, "fig_confusion_matrix.png"))


def tamper(rows, out):
    rows = [r for r in rows if r["scenario"] != "CONTROL"]
    short = {"TB1": "Edit a middle event", "TB2": "Shift an event timestamp", "TB3": "Replace authorization evidence",
             "TB4": "Delete a middle event", "TB5": "Forge a consistent extra transfer",
             "TB6": "Rewrite newest event + owner", "TB7": "TB6 + rewrite ledger hashes"}
    fig, ax = _ax((9, 5.2))
    series = [("baseline_rate", "Baseline (SQLite only)", BLUE),
              ("ledger_rate", "Ledger (compared with database)", ORANGE),
              ("ledger_anchor_rate", "Ledger + external anchor", AQUA)]
    h = 0.24
    for k, (key, label, color) in enumerate(series):
        ys = [i + (k - 1) * (h + 0.03) for i in range(len(rows))]
        vals = [float(r[key].rstrip("%")) for r in rows]
        ax.barh(ys, vals, height=h, color=color, label=label, zorder=3)
        for y, v in zip(ys, vals):                           # visible labels (aqua is below 3:1 contrast)
            ax.text(v + 1, y, f"{v:.0f}%", va="center", fontsize=8, color=INK)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([f"{r['scenario']}  {short[r['scenario']]}" for r in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("Tampered records flagged (%)")
    ax.set_title("Detection of database-level tampering", loc="left", fontweight="bold")
    ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.45, -0.13), ncol=3, fontsize=9)
    _save(fig, os.path.join(out, "fig_tamper_detection_comparison.png"))


def make_all(perf, comp, tamper_rows, labels, matrix, out):
    os.makedirs(out, exist_ok=True)
    latency(perf, out)
    grouped_bars(perf, "throughput_eps_mean", "Events processed per second",
                 "Throughput vs number of events", "fig_throughput_vs_events.png", out, lambda v: f"{v:,.0f}/s",
                 "Mean of repeated runs. Every event is committed to disk before the next one starts.")
    grouped_bars(perf, "db_size_kb", "Database file size (KB)", "Storage after recording the events",
                 "fig_storage_overhead.png", out, lambda v: f"{v:,.0f} KB")
    confusion(labels, matrix, out)
    tamper(tamper_rows, out)
