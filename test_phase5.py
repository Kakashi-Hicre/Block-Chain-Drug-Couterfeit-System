"""
test_phase5.py - tests for Phase 5 (controller + Tkinter interface).

Part A (always runs): controller tests - no display needed.
Part B (needs tkinter + a display): drives the real widgets - fills the form fields, calls the
        same methods the buttons call, and reads the labels / tables back.
Part C (--screenshots, needs a display): saves one PNG per tab to ./screenshots/.

Run:  python test_phase5.py                (tests)
      python test_phase5.py --screenshots  (tests + screenshots of all eight screens)
Writes: logs/phase5_unit_log.csv
"""
import csv
import os
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date

import supply_chain as sc
import verifier as vf
from controller import Controller
from test_phase2 import GOOD, LoggingResult

LOG_DIR = "logs"
REF = date(2026, 6, 1)
TMP = tempfile.mkdtemp(prefix="phase5_")

try:
    import tkinter as tk
    HAVE_TK = True
except ImportError:
    HAVE_TK = False


def new_ctl(name):
    path = os.path.join(TMP, name)
    for p in (path, path + ".anchor.json"):
        if os.path.exists(p):
            os.remove(p)
    return Controller(db_path=path, reference_date=REF)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.c = new_ctl("c.db")

    def tearDown(self):
        self.c.close()

    def reg(self, **kw):
        p = dict(GOOD)
        p.update(kw)
        return self.c.register(p, "Manufacturer_A", "2026-02-01 10:00:00")

    def test_01_starts_with_reference_data(self):
        self.assertEqual(len(self.c.participant_ids()), 10)
        self.assertEqual(len(self.c.participant_ids("Manufacturer", authorized_only=True)), 2)
        self.assertEqual(len(self.c.batches()), 20)
        self.assertEqual(self.c.stats(), dict(products=0, events=0, blocks=1, rejected=0))

    def test_02_register_and_transfer_reach_ledger(self):
        self.assertTrue(self.reg().ok)
        self.assertTrue(self.c.transfer("SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x",
                                        "2026-02-02 10:00:00").ok)
        self.assertEqual(self.c.stats(), dict(products=1, events=2, blocks=3, rejected=0))
        events, blocks = self.c.history("SN000001")
        self.assertEqual((len(events), len(blocks)), (2, 2))
        self.assertTrue(self.c.ledger_status()["ok"])

    def test_03_rejections_logged_not_on_ledger(self):
        self.reg()
        self.reg()                                                       # duplicate
        self.c.transfer("SN000001", "Rogue_Actor", "Pharmacy_A", "NONE")
        self.c.transfer("SN999999", "Distributor_A", "Wholesaler_A", "AUTH-x")
        s = self.c.stats()
        self.assertEqual((s["products"], s["events"], s["blocks"], s["rejected"]), (1, 1, 2, 3))
        codes = [r["code"] for r in self.c.rejected_attempts()]
        self.assertEqual(sorted(codes), sorted([sc.DUPLICATE_SERIAL, sc.UNAUTHORIZED_SENDER, sc.UNKNOWN_PRODUCT]))

    def test_04_next_serial(self):
        self.assertEqual(self.c.next_serial(), "SN000001")
        self.reg()
        self.assertEqual(self.c.next_serial(), "SN000002")

    def test_05_product_filter(self):
        self.reg()
        self.reg(serial_number="SN000002", product_name="Sample Painkiller B")
        self.assertEqual(len(self.c.products()), 2)
        self.assertEqual([p["serial_number"] for p in self.c.products("Painkiller")], ["SN000002"])
        self.assertEqual(self.c.products("zzz"), [])

    def test_06_verify_through_controller(self):
        self.reg()
        self.assertEqual(self.c.verify("SN000001")["status"], vf.RECORD_VERIFIED)
        self.assertEqual(self.c.verify("SN404040")["status"], vf.UNREGISTERED)

    def test_07_ledger_anchor_roundtrip(self):
        self.reg()
        self.assertEqual(self.c.ledger_status(use_anchor=True)["message"], "No saved anchor found - save one first")
        a = self.c.save_anchor()
        self.assertEqual(a["length"], 2)
        self.assertTrue(self.c.ledger_status(use_anchor=True)["ok"])
        self.assertTrue(os.path.exists(self.c.anchor_path))

    def test_08_truncation_caught_only_with_anchor(self):
        self.reg()
        self.c.transfer("SN000001", "Manufacturer_A", "Distributor_A", "AUTH-x", "2026-02-02 10:00:00")
        self.c.save_anchor()
        self.c.conn.execute("DELETE FROM ledger_blocks WHERE block_index = (SELECT MAX(block_index) FROM ledger_blocks)")
        self.c.conn.commit()
        self.c.ledger.reload()
        self.assertTrue(self.c.ledger_status(use_anchor=False)["ok"])        # chain alone cannot see it
        res = self.c.ledger_status(use_anchor=True)
        self.assertFalse(res["ok"])
        self.assertTrue(any(i["code"] == "LENGTH_MISMATCH" for i in res["issues"]))

    def test_09_tamper_demo_does_not_touch_real_ledger(self):
        self.reg()
        before = self.c.ledger.head.hash
        res = self.c.tamper_demo(1)
        self.assertTrue(res["before_ok"])
        self.assertFalse(res["after_ok"])
        self.assertEqual(self.c.ledger.head.hash, before)
        self.assertTrue(self.c.ledger_status()["ok"])
        with self.assertRaises(ValueError):
            self.c.tamper_demo(0)
        with self.assertRaises(ValueError):
            self.c.tamper_demo(99)

    def test_10_direct_database_tampering_shows_in_alerts(self):
        self.reg()
        self.c.conn.execute("UPDATE products SET expiry_date='2035-01-01'")
        self.c.conn.commit()
        reps = self.c.alerts("app")
        self.assertEqual([(r["serial_number"], r["status"]) for r in reps], [("SN000001", vf.MISMATCH)])

    def test_11_reset(self):
        self.reg()
        self.c.save_anchor()
        self.c.reset()
        self.assertEqual(self.c.stats(), dict(products=0, events=0, blocks=1, rejected=0))
        self.assertEqual(len(self.c.batches()), 20)
        self.assertIsNone(self.c.load_anchor())

    def test_12_load_synthetic_dataset(self):
        if not os.path.exists("dataset/events.csv"):
            self.skipTest("dataset/ missing - run generate_dataset.py")
        res = self.c.load_synthetic_dataset()
        self.assertEqual((res["accepted"], res["rejected"], res["blocks"]), (2791, 150, 2792))
        s = self.c.stats()
        self.assertEqual((s["products"], s["events"], s["rejected"]), (920, 2791, 150))
        self.assertTrue(self.c.ledger_status()["ok"])
        # The application ENFORCES the rules at entry: duplicate, unregistered, unauthorized and
        # skipped-stage events are rejected (150 attempts logged) and never enter the database or
        # ledger. Only conditions that are not entry errors remain to be flagged by a scan:
        # 30 expired products + 20 data mismatches.
        reps = self.c.alerts("app")
        self.assertEqual(Counter(r["status"] for r in reps),
                         Counter({vf.EXPIRY: 30, vf.MISMATCH: 20}))
        by_code = Counter(r["code"] for r in self.c.rejected_attempts())
        self.assertEqual(sum(by_code.values()), 150)
        self.assertEqual(by_code[sc.DUPLICATE_SERIAL], 40)
        self.assertEqual(by_code[sc.UNKNOWN_PRODUCT], 40)
        self.assertEqual(by_code[sc.UNAUTHORIZED_SENDER], 40)
        self.assertEqual(by_code[sc.INVALID_TRANSITION] + by_code[sc.INVALID_OWNERSHIP], 30)
        self.assertEqual(self.c.verify("SN000001")["serial_number"], "SN000001")

    def test_13_alerts_from_raw_dataset(self):
        if not os.path.exists("pharma_traceability.db"):
            self.skipTest("pharma_traceability.db missing")
        reps = self.c.alerts("dataset")
        self.assertEqual(len(reps), 200)
        self.assertEqual(reps[0]["status"], vf.DUPLICATE)              # priority order

    def test_14_results_files(self):
        files = self.c.results_files()
        if not files:
            self.skipTest("no results files in logs/")
        header, rows = self.c.read_table(files[0])
        self.assertTrue(header and rows)


@unittest.skipUnless(HAVE_TK, "tkinter not available")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui import App
        try:
            cls.app = App(controller=new_ctl("gui.db"))
        except tk.TclError as e:
            raise unittest.SkipTest(f"no display available: {e}")
        cls.app.confirm = lambda *a, **k: True
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "app"):
            cls.app.ctl.close()
            cls.app.destroy()

    def setUp(self):
        self.app.ctl.reset()
        self.app.refresh_all()
        self.app.reg_registrant.set("Manufacturer_A")

    def fill_register(self, serial="SN000001", batch="BATCH2026A"):
        a = self.app
        a.reg_batch.set(batch)
        a.autofill_batch()
        a.reg_serial.set(serial)

    def test_01_eight_tabs_match_brief(self):
        titles = [self.app.nb.tab(t, "text") for t in self.app.nb.tabs()]
        self.assertEqual(titles[0], "0  Home")                            # dashboard (extra, not in the brief)
        self.assertEqual(titles[1:], ["1  Register", "2  Products", "3  Transfer", "4  History",
                                      "5  Verify", "6  Blockchain", "7  Alerts", "8  Results"])

    def test_02_register_via_form(self):
        a = self.app
        self.fill_register()
        self.assertEqual(a.reg_mfr.get(), "Manufacturer_A")
        self.assertTrue(a.do_register().ok)
        self.assertIn("Registered", a.reg_result.cget("text"))
        self.assertEqual(a.reg_serial.get(), "SN000002")                 # next serial suggested
        self.assertEqual(len(a.prod_tree.get_children()), 1)
        self.assertIn("1 products", a.status_var.get())

    def test_03_duplicate_and_missing_field_rejected_in_form(self):
        a = self.app
        self.fill_register()
        a.do_register()
        self.fill_register()                                             # same serial again
        res = a.do_register()
        self.assertEqual(res.code, sc.DUPLICATE_SERIAL)
        self.assertIn("REJECTED", a.reg_result.cget("text"))
        a.reg_name.set("")
        a.reg_serial.set("SN000009")
        self.assertEqual(a.do_register().code, sc.MISSING_FIELD)

    def test_04_transfer_flow_and_rejections(self):
        a = self.app
        self.fill_register()
        a.do_register()
        a.tr_serial.set("SN000001")
        a.do_lookup_holder()
        self.assertEqual(a.tr_sender.get(), "Manufacturer_A")
        a.tr_receiver.set("Distributor_A")
        a.do_generate_evidence()
        self.assertEqual(a.tr_evidence.get(), "AUTH-Manufacturer_A-SN000001")
        self.assertTrue(a.do_transfer().ok)
        a.tr_sender.set("Distributor_A")
        a.tr_receiver.set("Pharmacy_A")                                  # skips the wholesaler
        a.do_generate_evidence()
        self.assertEqual(a.do_transfer().code, sc.INVALID_TRANSITION)
        a.tr_sender.set("Rogue_Actor")
        self.assertEqual(a.do_transfer().code, sc.UNAUTHORIZED_SENDER)
        self.assertIn("REJECTED", a.tr_result.cget("text"))

    def test_05_history_shows_events_and_blocks(self):
        a = self.app
        self.fill_register()
        a.do_register()
        a.tr_serial.set("SN000001")
        a.tr_sender.set("Manufacturer_A")
        a.tr_receiver.set("Distributor_A")
        a.tr_evidence.set("AUTH-x")
        a.do_transfer()
        a.hist_serial.set("SN000001")
        a.do_history()
        self.assertEqual(len(a.hist_events.get_children()), 2)
        self.assertEqual(len(a.hist_blocks.get_children()), 2)
        self.assertIn("2 event(s), 2 ledger block(s)", a.hist_summary.cget("text"))
        a.hist_serial.set("SN404040")
        a.do_history()
        self.assertIn("not in the product register", a.hist_summary.cget("text"))

    def test_06_verify_tab(self):
        a = self.app
        self.fill_register()
        a.do_register()
        a.ver_serial.set("SN000001")
        rep = a.do_verify()
        self.assertEqual(a.ver_status.cget("text"), vf.RECORD_VERIFIED)
        self.assertIn("not prove", a.ver_note.cget("text"))
        self.assertIn("Event history", a.ver_text.get("1.0", "end"))
        a.ver_serial.set("SN404040")
        a.do_verify()
        self.assertEqual(a.ver_status.cget("text"), vf.UNREGISTERED)
        self.assertEqual(rep["status"], vf.RECORD_VERIFIED)

    def test_07_ledger_tab(self):
        a = self.app
        self.fill_register()
        a.do_register()
        self.assertEqual(a.do_check_ledger()["ok"], True)
        self.assertEqual(a.led_status.cget("text"), "LEDGER INTEGRITY VERIFIED")
        a.do_save_anchor()
        self.assertIn("Anchor saved", a.led_info.cget("text"))
        self.assertTrue(a.do_check_ledger(use_anchor=True)["ok"])
        a.tamper_block.set("1")
        res = a.do_tamper_demo()
        self.assertFalse(res["after_ok"])
        self.assertIn("TAMPERING DETECTED", a.led_status.cget("text"))
        self.assertGreater(len(a.led_issues.get_children()), 0)
        self.assertTrue(a.do_check_ledger()["ok"])                       # real ledger untouched
        a.tamper_block.set("abc")
        self.assertIsNone(a.do_tamper_demo())
        self.assertEqual(a.led_status.cget("text"), "Cannot run demo")

    def test_08_alerts_tab_and_row_detail(self):
        a = self.app
        a.alert_source.set("Raw synthetic dataset (Phase 1)")
        if not os.path.exists("pharma_traceability.db"):
            self.skipTest("pharma_traceability.db missing")
        reps = a.do_scan()
        self.assertEqual(len(reps), 200)
        self.assertEqual(len(a.alert_tree.get_children()), 200)
        self.assertIn("200 flagged record(s)", a.alert_summary.cget("text"))
        first = a.alert_tree.get_children()[0]
        a.alert_tree.selection_set(first)
        a.update()
        self.assertIn("Verification report", a.alert_text.get("1.0", "end"))

    def test_09_alerts_tab_lists_rejected_attempts(self):
        a = self.app
        self.fill_register()
        a.do_register()
        self.fill_register()                                             # same serial again
        self.assertEqual(a.do_register().code, sc.DUPLICATE_SERIAL)      # -> rejected attempt
        a.alert_source.set("Application database")
        self.assertEqual(a.do_scan(), [])
        self.assertIn("No flagged records", a.alert_summary.cget("text"))
        self.assertEqual(len(a.rej_tree.get_children()), 1)

    def test_10_results_tab(self):
        a = self.app
        a.refresh_results_files()
        if not a._res_paths:
            self.skipTest("no results files")
        a.res_file.set(sorted(a._res_paths)[0])
        header, rows = a.do_show_results()
        self.assertEqual(len(a.res_tree.get_children()), len(rows))
        self.assertEqual(len(a.res_tree["columns"]), len(header))

    def test_11_load_dataset_and_reset_buttons(self):
        a = self.app
        if not os.path.exists("dataset/events.csv"):
            self.skipTest("dataset/ missing")
        res = a.do_load_dataset()
        self.assertEqual((res["accepted"], res["rejected"]), (2791, 150))
        self.assertIn("920 products", a.status_var.get())
        a.alert_source.set("Application database")
        self.assertEqual(len(a.do_scan()), 50)                           # 30 expired + 20 mismatch
        self.assertEqual(len(a.rej_tree.get_children()), 150)            # blocked at entry
        a.prod_filter.set("SN00000")
        a.update()
        self.assertEqual(len(a.prod_tree.get_children()), len(a.ctl.products("SN00000")))
        a.prod_filter.set("")
        a.confirm = lambda *x, **k: False
        self.assertFalse(a.do_reset())                                   # declined: nothing erased
        self.assertIn("920 products", a.status_var.get())
        a.confirm = lambda *x, **k: True
        self.assertTrue(a.do_reset())
        self.assertIn("0 products", a.status_var.get())

    def test_12_double_click_product_opens_history(self):
        a = self.app
        self.fill_register()
        a.do_register()
        a.select_tab("products")
        a.update()
        row = a.prod_tree.get_children()[0]
        a.prod_tree.selection_set(row)
        a._on_product_double_click(None)
        self.assertEqual(a.hist_serial.get(), "SN000001")
        self.assertEqual(a.nb.tab(a.nb.select(), "text"), "4  History")
        self.assertEqual(len(a.hist_events.get_children()), 1)


class LR5(LoggingResult):
    rows = []


def run_tests():
    suite = unittest.TestSuite()
    for cls in (ControllerTests, GuiTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    result = unittest.TextTestRunner(verbosity=1, resultclass=LR5).run(suite)
    skipped = {t._testMethodName: reason for t, reason in result.skipped}
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(f"{LOG_DIR}/phase5_unit_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["test_case", "status"])
        w.writerows(LR5.rows)
        for name in skipped:
            w.writerow([name, "SKIPPED"])
    if skipped:
        print("Skipped:", {k: v[:60] for k, v in skipped.items()})
    return result.wasSuccessful()


# ------------------------------------------------------------------ screenshots
def take_screenshots():
    """One PNG per tab, from a live application loaded with the synthetic dataset."""
    import subprocess
    from gui import App
    os.makedirs("screenshots", exist_ok=True)               # needs ImageMagick's `import` command
    ctl = new_ctl("shots.db")
    ctl.load_synthetic_dataset()
    app = App(controller=ctl)
    app.confirm = lambda *a, **k: True
    app.geometry("1180x740+0+0")

    def grab(name):
        app.update_idletasks()
        app.update()
        app.after(150)
        app.update()
        path = f"screenshots/{name}.png"
        subprocess.run(["import", "-window", "root", "-crop", "1180x740+0+0", "+repage", path],
                       check=True)

    # 1 register: a deliberately rejected duplicate
    app.reg_batch.set("BATCH2026A")
    app.autofill_batch()
    app.reg_serial.set("SN000001")
    app.do_register()
    app.select_tab("register")
    grab("1_register_medicine")
    # 2 products
    app.prod_filter.set("")
    app.select_tab("products")
    grab("2_view_products")
    # 3 transfer: a distributor tries to skip the wholesaler (rejected)
    in_dist = next(p for p in ctl.products() if p["current_status"] == "In distribution")
    app.tr_serial.set(in_dist["serial_number"])
    app.do_lookup_holder()
    app.tr_receiver.set("Pharmacy_A")
    app.do_generate_evidence()
    app.do_transfer()
    app.select_tab("transfer")
    grab("3_transfer_product")
    # 4 history
    app.hist_serial.set("SN000001")
    app.do_history()
    app.select_tab("history")
    grab("4_track_history")
    # 5 verify (a data-mismatch case: duplicates never enter the application database)
    bad = next(r for r in ctl.alerts("app") if r["status"] == vf.MISMATCH)
    app.ver_serial.set(bad["serial_number"])
    app.do_verify()
    app.select_tab("verify")
    grab("5_verify_serial")
    # 6 ledger tamper demo
    app.do_save_anchor()
    app.tamper_block.set("120")
    app.do_tamper_demo()
    app.select_tab("ledger")
    grab("6_blockchain_integrity")
    # 7 alerts
    app.alert_source.set("Application database")
    app.do_scan()
    first = app.alert_tree.get_children()[0]
    app.select_tab("alerts")
    app.alert_tree.selection_set(first)
    grab("7_suspicious_alerts")
    # 8 results
    app.select_tab("results")
    app.res_file.set("phase4_summary.csv")
    app.do_show_results()
    grab("8_experimental_results")
    ctl.close()
    app.destroy()
    print("Saved screenshots/:", ", ".join(sorted(os.listdir("screenshots"))))


if __name__ == "__main__":
    ok = run_tests()
    if "--screenshots" in sys.argv:
        take_screenshots()
    print("\nRESULT:", "ALL PASSED" if ok else "FAILURES PRESENT")
    sys.exit(0 if ok else 1)
