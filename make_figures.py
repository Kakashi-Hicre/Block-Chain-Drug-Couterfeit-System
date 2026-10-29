"""
make_figures.py - rebuild the Phase 6 charts from the saved logs (no experiments are re-run).

Run:  python make_figures.py        (needs logs/phase6_*.csv from a previous `python experiments.py`)
"""
import csv

import figures

LOG_DIR, FIG_DIR = "logs", "figures"
FLOAT_COLS = {"latency_ms_mean", "latency_ms_std", "latency_ms_p95", "throughput_eps_mean", "throughput_eps_std",
              "history_sql_ms", "history_ledger_scan_ms", "ledger_verify_ms", "db_size_kb", "ledger_payload_kb"}


def read(name):
    with open(f"{LOG_DIR}/{name}", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    perf = []
    for r in read("phase6_performance_summary.csv"):
        r["n_events"] = int(r["n_events"])
        for k in FLOAT_COLS:
            r[k] = float(r[k]) if r.get(k) not in (None, "") else ""
        perf.append(r)
    tamper = read("phase6_tamper_comparison.csv")
    with open(f"{LOG_DIR}/phase6_confusion_matrix.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    labels = rows[0][1:]
    matrix = {(r[0], labels[j]): int(v) for r in rows[1:] for j, v in enumerate(r[1:])}
    figures.make_all(perf, None, tamper, labels, matrix, FIG_DIR)
    print("figures written to", FIG_DIR)


if __name__ == "__main__":
    main()
