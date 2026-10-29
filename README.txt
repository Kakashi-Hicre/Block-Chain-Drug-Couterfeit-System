Blockchain-Based Counterfeit Drug Detection and Traceability - prototype (SYNTHETIC DATA ONLY)

HOW TO RUN (keep all files and the dataset/ and logs/ folders together; Python 3.13 + matplotlib + customtkinter: pip install customtkinter)
  1. python generate_dataset.py      -> builds dataset/ and pharma_traceability.db (run once)
  2. python gui.py                   -> starts the Tkinter application (Phase 5)
     In the window click "Load synthetic dataset" to fill it with the 1,000 test packages.
     Tip: run gui.py from the Anaconda Prompt (python gui.py) rather than inside Spyder's console.
  3. python experiments.py           -> runs experiments E1-E8 + SQLite baseline comparison (about 3 minutes)
     python experiments.py --quick   -> tiny trial counts, only to check the script works
     python make_figures.py          -> rebuilds the charts from the saved logs without re-running anything
  4. python test_phase2.py ... test_phase6.py   -> tests (results are written to logs/)

FILES
  database.py          SQLite schema
  generate_dataset.py  synthetic dataset + ground-truth labels                 (Phase 1)
  supply_chain.py      register_product(), transfer_product(), get_history()   (Phase 2)
  ledger.py            SHA-256 hash-linked ledger, verify_chain()              (Phase 3)
  verifier.py          rule-based verification engine and reports              (Phase 4)
  controller.py, gui.py  application logic and Tkinter interface               (Phase 5)
  baseline.py          conventional SQLite-only system used as the baseline    (Phase 6)
  experiments.py       E1-E8, tamper comparison, metrics, performance, charts  (Phase 6)
  figures.py, make_figures.py   chart code                                     (Phase 6)
  screenshots/         one screenshot per screen of the application (1-8)
  figures/             charts for the report
  logs/                every test log and result table (CSV)

RESULT FILES FOR THE REPORT (logs/)
  phase6_test_cases.csv           every test case: experiment, test case, expected, actual, PASS/FAIL
  phase6_experiment_summary.csv   tests passed per experiment
  phase6_metrics.csv              detection rate, false-positive rate, integrity rate, completeness, ...
  phase6_confusion_matrix.csv     dataset confusion matrix
  phase6_tamper_comparison.csv    baseline vs hash-linked ledger on database-level tampering
  phase6_performance_runs.csv     every performance run;   phase6_performance_summary.csv  mean/std/p95
  phase6_baseline_comparison.csv  overhead of the ledger (latency, throughput, storage)
  phase6_environment.txt          machine and library versions the timings were measured on
  phase6_run_output.txt           console output of the full run

IMPORTANT WORDING
  "Record verified" means the registered data and custody history are consistent. It does not
  prove that a medicine is physically authentic. A suspicious record needs investigation; it is
  not proof of counterfeiting. This is a single-node hash chain (a blockchain prototype), not a
  distributed blockchain. Timings depend on the machine; re-run experiments.py on your own computer
  and report the numbers from your run, together with phase6_environment.txt.
