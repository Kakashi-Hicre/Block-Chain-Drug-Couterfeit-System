"""
gui.py - Tkinter / CustomTkinter desktop application for the pharmaceutical traceability prototype.

Run:  python gui.py        (needs:  pip install customtkinter)

All data is SYNTHETIC. "Record verified" means the registered data and custody history are
consistent - it does NOT prove that a medicine is physically authentic.

Pages (the eight menu options of the project brief, plus a Home dashboard):
  Home        overview of the system        5 Verify      verify a serial number
  1 Register  register a medicine           6 Blockchain  check blockchain integrity
  2 Products  view registered products      7 Alerts      suspicious-record alerts
  3 Transfer  transfer a product            8 Results     experimental results
  4 History   track product history
"""
import os
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

import verifier as vf
from controller import Controller

# ----------------------------------------------------------------------------- palette
# every colour is a (light mode, dark mode) pair
PAGE = ("#f1f4f9", "#0b1220")
CARD = ("#ffffff", "#162033")
FIELD = ("#ffffff", "#0e1727")
BORDER = ("#dde3ec", "#26334d")
INK = ("#0f172a", "#e8edf7")
MUTED = ("#64748b", "#93a1bb")
ACCENT = ("#2563eb", "#3b82f6")
ACCENT_H = ("#1d4ed8", "#2563eb")
ACCENT_SOFT = ("#dbeafe", "#1e3a8a")
SIDE = "#0f1b3d"
SIDE_H = "#1b2c5e"
SIDE_TXT = "#b6c2dc"
TONES = {  # kind -> (text colour, background tint)
    "ok": (("#15803d", "#4ade80"), ("#dcfce7", "#10301d")),
    "warn": (("#b45309", "#fbbf24"), ("#fef3c7", "#3a2a0b")),
    "bad": (("#b91c1c", "#f87171"), ("#fee2e2", "#3d1414")),
    "muted": (MUTED, ("#e7ecf4", "#1c2740")),
    "none": (MUTED, "transparent"),
}
WARNING_RULES = {vf.EXPIRY, vf.TRACEABILITY}
SOURCES = {"Application database": "app", "Raw synthetic dataset (Phase 1)": "dataset"}
STAGES = ["Manufacturer", "Distributor", "Wholesaler", "Pharmacy"]
PAGES = {  # key -> (tab text, nav label, page title, subtitle)
    "home": ("0  Home", "Home", "Dashboard", "A live overview of the supply chain and the ledger."),
    "register": ("1  Register", "Register medicine", "Register a medicine",
                 "Create a batch record, then register packages as the manufacturer."),
    "products": ("2  Products", "Products", "Registered products",
                 "Every package currently in the application database."),
    "transfer": ("3  Transfer", "Transfer", "Transfer custody",
                 "Move a package one step down the chain, with authorization evidence."),
    "history": ("4  History", "History", "Track product history",
                "Custody events from the database next to the SHA-256 ledger blocks."),
    "verify": ("5  Verify", "Verify", "Verify a serial number",
               "Checks authorization, custody, batch data, expiry and ledger integrity."),
    "ledger": ("6  Blockchain", "Blockchain", "Blockchain integrity",
               "Verify the hash chain, store an anchor, or simulate tampering on a copy."),
    "alerts": ("7  Alerts", "Alerts", "Suspicious records",
               "Scan for flagged records and review refused operations."),
    "results": ("8  Results", "Results", "Experimental results",
                "Tables saved by the experiment scripts in the logs folder."),
}


def status_kind(status):
    if status == vf.RECORD_VERIFIED:
        return "ok"
    return "warn" if status in WARNING_RULES else "bad"


def _mode():
    return 1 if ctk.get_appearance_mode() == "Dark" else 0


def fill_tree(tree, rows, tags=None):
    tree.delete(*tree.get_children())
    for i, row in enumerate(rows):
        tg = ([tags[i]] if tags and tags[i] else []) + (["odd"] if i % 2 else [])
        tree.insert("", "end", values=row, tags=tuple(tg))


def set_text(text, content):
    text.configure(state="normal")
    text.delete("1.0", "end")
    text.insert("1.0", content)
    text.configure(state="disabled")


class PageStack(ctk.CTkFrame):
    """Shows one page at a time. Mimics the few ttk.Notebook methods the tests use."""

    def __init__(self, master, on_change):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._pages, self._texts, self._frames, self._cur = [], {}, {}, None
        self._on_change = on_change

    def add(self, frame, text):
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_remove()
        self._pages.append(str(frame))
        self._texts[str(frame)] = text
        self._frames[str(frame)] = frame

    def tabs(self):
        return tuple(self._pages)

    def tab(self, ident, option):
        return self._texts[str(ident)] if option == "text" else None

    def select(self, frame=None):
        if frame is None:
            return self._cur or ""
        key = str(frame)
        if self._cur and self._cur != key:
            self._frames[self._cur].grid_remove()
        self._frames[key].grid()
        self._cur = key
        self._on_change()
        return key


class App(ctk.CTk):
    def __init__(self, controller=None):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        super().__init__(fg_color=PAGE)
        self.title("PharmaTrace - Pharmaceutical Traceability Prototype (synthetic data only)")
        self.geometry("1360x820")
        self.minsize(1180, 700)
        self.ctl = controller or Controller()
        self.confirm = messagebox.askyesno           # replaceable in tests
        self._alert_reports = {}
        self._trees, self._fonts = [], {}
        import tkinter.font as tkfont
        fams = set(tkfont.families())
        self.FAM = next((f for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Arial", "DejaVu Sans")
                         if f in fams), "TkDefaultFont")
        self.MONO = next((f for f in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono") if f in fams),
                         "TkFixedFont")
        self.tabs, self._nav = {}, {}

        self._build_statusbar()
        self._build_sidebar()
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(side="left", fill="both", expand=True)
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.pack(fill="x", padx=30, pady=(22, 10))
        self.page_title = ctk.CTkLabel(head, text="", font=self.font(26, True), text_color=INK, anchor="w")
        self.page_title.pack(anchor="w")
        self.page_sub = ctk.CTkLabel(head, text="", font=self.font(13), text_color=MUTED, anchor="w")
        self.page_sub.pack(anchor="w")
        self.nb = PageStack(main, self._on_tab_changed)
        self.nb.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        self._style_trees()
        for key, builder in [("home", self._build_home), ("register", self._build_register),
                             ("products", self._build_products), ("transfer", self._build_transfer),
                             ("history", self._build_history), ("verify", self._build_verify),
                             ("ledger", self._build_ledger), ("alerts", self._build_alerts),
                             ("results", self._build_results)]:
            frame = ctk.CTkFrame(self.nb, fg_color="transparent")
            self.nb.add(frame, PAGES[key][0])
            self.tabs[key] = frame
            builder(frame)
        self.refresh_all()
        self.select_tab("home")

    # =============================================================== building blocks
    def font(self, size=13, bold=False):
        k = (size, bold)
        if k not in self._fonts:
            self._fonts[k] = ctk.CTkFont(family=self.FAM, size=size, weight="bold" if bold else "normal")
        return self._fonts[k]

    def card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16, border_width=1, border_color=BORDER, **kw)

    def label(self, parent, text, size=13, bold=False, muted=False, **kw):
        kw.setdefault("anchor", "w")
        return ctk.CTkLabel(parent, text=text, font=self.font(size, bold),
                            text_color=MUTED if muted else INK, **kw)

    def entry(self, parent, var, width=260, placeholder=None):
        return ctk.CTkEntry(parent, textvariable=var, width=width, height=38, corner_radius=10, border_width=1,
                            border_color=BORDER, fg_color=FIELD, text_color=INK, font=self.font(13),
                            placeholder_text=placeholder)

    def combo(self, parent, var, values, width=260, readonly=True, command=None):
        return ctk.CTkComboBox(parent, variable=var, values=list(values) or [""], width=width, height=38,
                               corner_radius=10, border_width=1, border_color=BORDER, fg_color=FIELD,
                               button_color=ACCENT, button_hover_color=ACCENT_H, text_color=INK,
                               dropdown_fg_color=CARD, dropdown_text_color=INK, dropdown_hover_color=ACCENT_SOFT,
                               font=self.font(13), dropdown_font=self.font(13),
                               state="readonly" if readonly else "normal", command=command)

    def button(self, parent, text, command, kind="primary", width=150):
        if kind == "primary":
            kw = dict(fg_color=ACCENT, hover_color=ACCENT_H, text_color="#ffffff")
        elif kind == "danger":
            kw = dict(fg_color="transparent", hover_color=TONES["bad"][1], text_color=TONES["bad"][0],
                      border_width=1, border_color=TONES["bad"][0])
        else:
            kw = dict(fg_color=("#e8eef9", "#1f2d4a"), hover_color=("#d6e2f6", "#2a3b60"), text_color=INK)
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=38, corner_radius=10,
                             font=self.font(13, kind == "primary"), **kw)

    def banner(self, parent, text="", size=13, bold=True, height=40):
        """A coloured message bar. cget('text') returns the text."""
        return ctk.CTkLabel(parent, text=text, height=height, corner_radius=10, anchor="w", justify="left",
                            font=self.font(size, bold), text_color=MUTED, fg_color="transparent", wraplength=900)

    def tone(self, label, text, kind):
        fg, bg = TONES[kind]
        label.configure(text=text, text_color=fg, fg_color=bg)

    def table(self, parent, columns, height=10):
        """columns: list of (id, heading, width). Returns (card frame, tree)."""
        frame = self.card(parent)
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=6, pady=6)
        tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show="headings", height=height)
        for cid, heading, width in columns:
            tree.heading(cid, text=heading, anchor="w")
            tree.column(cid, width=width, anchor="w", stretch=True)
        sb = ctk.CTkScrollbar(inner, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._trees.append(tree)
        self._tag_tree(tree)
        return frame, tree

    def textbox(self, parent, height=200):
        box = ctk.CTkTextbox(parent, height=height, corner_radius=14, border_width=1, border_color=BORDER,
                             fg_color=CARD, text_color=INK, font=(self.MONO, 12), wrap="word")
        box.configure(state="disabled")
        return box

    def _tag_tree(self, tree):
        m = _mode()
        tree.tag_configure("odd", background=("#f6f8fc", "#1a2640")[m])
        tree.tag_configure("alert", foreground=TONES["bad"][0][m])
        tree.tag_configure("bad", foreground=TONES["bad"][0][m])
        tree.tag_configure("warning", foreground=TONES["warn"][0][m])

    def _style_trees(self):
        m = _mode()
        st = ttk.Style(self)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        bg, fg = CARD[m], INK[m]
        st.configure("Treeview", background=bg, fieldbackground=bg, foreground=fg, rowheight=34,
                     borderwidth=0, relief="flat", font=(self.FAM, 11), bordercolor=bg, lightcolor=bg,
                     darkcolor=bg)
        st.configure("Treeview.Heading", background=("#eef2f8", "#1d2a44")[m], foreground=MUTED[m],
                     font=(self.FAM, 10, "bold"), padding=(12, 9), relief="flat", borderwidth=0)
        st.map("Treeview", background=[("selected", ACCENT_SOFT[m])], foreground=[("selected", fg)])
        st.map("Treeview.Heading", background=[("active", ("#e2e9f4", "#26365a")[m])])
        for t in self._trees:
            self._tag_tree(t)

    def toggle_mode(self):
        ctk.set_appearance_mode("dark" if self.mode_switch.get() else "light")
        self._style_trees()
        self.refresh_status()

    # =============================================================== chrome
    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=0, height=34, border_width=1, border_color=BORDER)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar()
        ctk.CTkLabel(bar, textvariable=self.status_var, font=self.font(12), text_color=INK).pack(
            side="left", padx=18, pady=5)
        ctk.CTkLabel(bar, font=self.font(12), text_color=MUTED,
                     text="Synthetic data only. 'Record verified' does not prove physical authenticity."
                     ).pack(side="right", padx=18)

    def _build_sidebar(self):
        side = ctk.CTkFrame(self, fg_color=SIDE, corner_radius=0, width=250)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.pack(side="bottom", fill="x")
        self.mode_switch = ctk.CTkSwitch(bottom, text="Dark mode", command=self.toggle_mode, text_color=SIDE_TXT,
                                         progress_color=ACCENT[0], font=self.font(12))
        self.mode_switch.pack(anchor="w", padx=24, pady=(0, 12))
        ctk.CTkButton(bottom, text="Load synthetic dataset", height=36, corner_radius=10, fg_color=SIDE_H,
                      hover_color="#26397a", text_color="#e2e8f0", font=self.font(13),
                      command=self.do_load_dataset).pack(fill="x", padx=18, pady=(0, 8))
        ctk.CTkButton(bottom, text="Reset application data", height=36, corner_radius=10, fg_color="transparent",
                      hover_color="#4a1d2b", text_color="#fca5a5", border_width=1, border_color="#7f2a3b",
                      font=self.font(13), command=self.do_reset).pack(fill="x", padx=18, pady=(0, 18))
        logo = ctk.CTkFrame(side, fg_color="transparent")
        logo.pack(fill="x", padx=20, pady=(24, 18))
        ctk.CTkLabel(logo, text="Rx", width=42, height=42, corner_radius=12, fg_color=ACCENT[0],
                     text_color="#ffffff", font=self.font(17, True)).pack(side="left")
        names = ctk.CTkFrame(logo, fg_color="transparent")
        names.pack(side="left", padx=12)
        ctk.CTkLabel(names, text="PharmaTrace", font=self.font(18, True), text_color="#ffffff", anchor="w").pack(anchor="w")
        ctk.CTkLabel(names, text="Drug traceability", font=self.font(11), text_color=SIDE_TXT,
                     anchor="w").pack(anchor="w")
        for key in PAGES:
            b = ctk.CTkButton(side, text="   " + PAGES[key][1], anchor="w", height=40, corner_radius=10,
                              fg_color="transparent", hover_color=SIDE_H, text_color=SIDE_TXT,
                              font=self.font(14), command=lambda k=key: self.select_tab(k))
            b.pack(fill="x", padx=14, pady=1)
            self._nav[key] = b

    def _current_key(self):
        return next((k for k, f in self.tabs.items() if str(f) == self.nb.select()), None)

    def _update_nav(self):
        cur = self._current_key()
        for key, b in self._nav.items():
            on = key == cur
            b.configure(fg_color=ACCENT[0] if on else "transparent", text_color="#ffffff" if on else SIDE_TXT,
                        font=self.font(14, on))
        if cur:
            self.page_title.configure(text=PAGES[cur][2])
            self.page_sub.configure(text=PAGES[cur][3])

    def _on_tab_changed(self):
        key = self._current_key()
        if key == "products":
            self.refresh_products()
        elif key == "results":
            self.refresh_results_files()
        elif key == "home":
            self.refresh_home()
        self._update_nav()

    def select_tab(self, key):
        self.nb.select(self.tabs[key])
        self.update_idletasks()

    def _grid_msg(self, label, ok, text):
        fg, bg = TONES["ok" if ok else "bad"]
        label.configure(text=text, text_color=fg, fg_color=bg)
        label.grid()

    def refresh_status(self):
        s = self.ctl.stats()
        self.status_var.set(f"{s['products']} products    |    {s['events']} events    |    "
                            f"{s['blocks']} ledger blocks    |    {s['rejected']} rejected attempts")
        if hasattr(self, "stat_values"):
            self.refresh_home()

    def refresh_all(self):
        self.refresh_status()
        self.refresh_products()
        self.reg_serial.set(self.ctl.next_serial())

    # =============================================================== 0 home
    def _build_home(self, f):
        f.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self.stat_values = {}
        for i, (key, caption, color) in enumerate([("products", "Registered products", ACCENT),
                                                   ("events", "Custody events", ("#0e7490", "#22d3ee")),
                                                   ("blocks", "Ledger blocks", ("#7c3aed", "#a78bfa")),
                                                   ("rejected", "Rejected attempts", TONES["bad"][0])]):
            c = self.card(f)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0 if i == 3 else 8), pady=(0, 14))
            ctk.CTkFrame(c, fg_color=color, width=5, height=44, corner_radius=3).place(x=18, y=20)
            v = ctk.CTkLabel(c, text="0", font=self.font(32, True), text_color=INK, anchor="w")
            v.pack(anchor="w", padx=(36, 12), pady=(14, 0))
            self.label(c, caption, 12, muted=True).pack(anchor="w", padx=(36, 12), pady=(0, 14))
            self.stat_values[key] = v
        sc_card = self.card(f)
        sc_card.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 14))
        top = ctk.CTkFrame(sc_card, fg_color="transparent")
        top.pack(fill="x", padx=22, pady=(16, 4))
        self.label(top, "Where the packages are now", 15, True).pack(side="left")
        self.home_ledger = ctk.CTkLabel(top, text="", corner_radius=10, height=30, font=self.font(12, True),
                                        fg_color="transparent", text_color=MUTED)
        self.home_ledger.pack(side="right")
        row = ctk.CTkFrame(sc_card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(6, 18))
        row.grid_columnconfigure((0, 2, 4, 6), weight=1, uniform="stg")
        self.stage_values = {}
        for i, role in enumerate(STAGES):
            tile = ctk.CTkFrame(row, fg_color=ACCENT_SOFT, corner_radius=14)
            tile.grid(row=0, column=i * 2, sticky="nsew")
            ctk.CTkLabel(tile, text=role, font=self.font(12, True), text_color=ACCENT, anchor="w").pack(
                anchor="w", padx=16, pady=(12, 0))
            v = ctk.CTkLabel(tile, text="0", font=self.font(26, True), text_color=INK, anchor="w")
            v.pack(anchor="w", padx=16, pady=(0, 12))
            self.stage_values[role] = v
            if i < 3:
                ctk.CTkLabel(row, text="→", font=self.font(20, True), text_color=MUTED, width=34).grid(
                    row=0, column=i * 2 + 1)
        f.grid_rowconfigure(2, weight=1)
        qs = self.card(f)
        qs.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(0, 8))
        self.label(qs, "Quick start with your own medicine", 15, True).pack(anchor="w", padx=22, pady=(16, 8))
        for n, (t, d) in enumerate([("Register medicine", "Type a new batch number, fill the details, 'Save as new batch'."),
                                    ("Register the package", "Use the same details as the batch, then 'Register medicine'."),
                                    ("Transfer it", "Manufacturer, Distributor, Wholesaler, Pharmacy: one step at a time."),
                                    ("Verify", "Enter the serial number and check the result.")], start=1):
            r = ctk.CTkFrame(qs, fg_color="transparent")
            r.pack(fill="x", padx=22, pady=5)
            ctk.CTkLabel(r, text=str(n), width=30, height=30, corner_radius=15, fg_color=ACCENT, text_color="#ffffff",
                         font=self.font(13, True)).pack(side="left", anchor="n")
            tx = ctk.CTkFrame(r, fg_color="transparent")
            tx.pack(side="left", padx=12)
            self.label(tx, t, 13, True).pack(anchor="w")
            ctk.CTkLabel(tx, text=d, font=self.font(12), text_color=MUTED, anchor="w", justify="left",
                         wraplength=380).pack(anchor="w")
        ev = self.card(f)
        ev.grid(row=2, column=2, columnspan=2, sticky="nsew", padx=(8, 0))
        self.label(ev, "Latest custody events", 15, True).pack(anchor="w", padx=22, pady=(16, 6))
        inner = ctk.CTkFrame(ev, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        self.home_tree = ttk.Treeview(inner, columns=("serial", "type", "move"), show="headings", height=7)
        for cid, h, w in (("serial", "Serial", 90), ("type", "Type", 80), ("move", "From  →  To", 220)):
            self.home_tree.heading(cid, text=h, anchor="w")
            self.home_tree.column(cid, width=w, anchor="w", stretch=True)
        self.home_tree.pack(fill="both", expand=True)
        self._trees.append(self.home_tree)
        self._tag_tree(self.home_tree)

    def refresh_home(self):
        s = self.ctl.stats()
        for k, v in self.stat_values.items():
            v.configure(text=f"{s[k]:,}")
        for role, n in self.ctl.stage_counts():
            self.stage_values[role].configure(text=f"{n:,}")
        try:
            ok = self.ctl.ledger_status()["ok"]
            self.tone(self.home_ledger, "  Ledger intact  " if ok else "  Ledger integrity FAILED  ",
                      "ok" if ok else "bad")
        except Exception:
            self.tone(self.home_ledger, "", "none")
        fill_tree(self.home_tree, [(e["serial_number"], e["event_type"], f"{e['sender']}  →  {e['receiver']}")
                                   for e in self.ctl.recent_events(7)])

    # =============================================================== 1 register
    def _build_register(self, f):
        f.grid_columnconfigure(0, weight=3)
        f.grid_columnconfigure(1, weight=2)
        card = self.card(f)
        card.grid(row=0, column=0, sticky="new", padx=(0, 10))
        self.label(card, "Medicine details", 16, True).grid(row=0, column=0, columnspan=2, sticky="w",
                                                            padx=26, pady=(22, 2))
        self.reg_registrant, self.reg_batch = tk.StringVar(), tk.StringVar()
        self.reg_serial, self.reg_drug, self.reg_name = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.reg_mfr, self.reg_mfg, self.reg_exp = tk.StringVar(), tk.StringVar(), tk.StringVar()
        mfrs = self.ctl.participant_ids("Manufacturer", authorized_only=True)
        others = [p for p in self.ctl.participant_ids() if p not in mfrs]
        self.reg_registrant.set(mfrs[0] if mfrs else "")
        self.reg_batch_box = self.combo(card, self.reg_batch, [b["batch_number"] for b in self.ctl.batches()],
                                        width=270, readonly=False, command=lambda _v: self.autofill_batch())
        fields = [("Registering participant", self.combo(card, self.reg_registrant, mfrs + others, width=270)),
                  ("Batch (pick one or type a new one)", self.reg_batch_box),
                  ("Serial number", self.entry(card, self.reg_serial, 270)),
                  ("Drug ID", self.entry(card, self.reg_drug, 270)),
                  ("Product name", self.entry(card, self.reg_name, 270)),
                  ("Manufacturer", self.entry(card, self.reg_mfr, 270)),
                  ("Manufacturing date (YYYY-MM-DD)", self.entry(card, self.reg_mfg, 270, "2026-09-01")),
                  ("Expiry date (YYYY-MM-DD)", self.entry(card, self.reg_exp, 270, "2028-09-01"))]
        for i, (text, widget) in enumerate(fields):
            r, c = 1 + (i // 2) * 2, i % 2
            self.label(card, text, 12, muted=True).grid(row=r, column=c, sticky="w", padx=(26, 8), pady=(12, 2))
            widget.grid(row=r + 1, column=c, sticky="w", padx=(26, 8), pady=(0, 2))
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=10, column=0, columnspan=2, sticky="w", padx=26, pady=(22, 24))
        self.button(bar, "Register medicine", self.do_register, "primary", 180).pack(side="left")
        self.button(bar, "Save as new batch", self.do_save_batch, "secondary", 160).pack(side="left", padx=10)
        self.button(bar, "Suggest serial", lambda: self.reg_serial.set(self.ctl.next_serial()), "secondary",
                    130).pack(side="left")
        side = self.card(f)
        side.grid(row=0, column=1, sticky="new")
        self.label(side, "How this works", 16, True).pack(anchor="w", padx=24, pady=(22, 8))
        for t in ("Only an authorized manufacturer can register a package.",
                  "A batch holds the master data. The package must match it exactly, or the verifier reports a data mismatch.",
                  "A serial number can be registered only once.",
                  "To use your own data, type a new batch number, fill the fields and press 'Save as new batch', then register.",
                  "Try wrong data on purpose to see rejections and alerts."):
            r = ctk.CTkFrame(side, fg_color="transparent")
            r.pack(fill="x", padx=24, pady=4)
            ctk.CTkFrame(r, fg_color=ACCENT, width=6, height=6, corner_radius=3).pack(side="left", anchor="n", pady=7)
            ctk.CTkLabel(r, text=t, font=self.font(12), text_color=INK, anchor="w", justify="left",
                         wraplength=330).pack(side="left", padx=10)
        ctk.CTkLabel(side, text="", height=10).pack()
        self.reg_result = self.banner(f, "")
        self.reg_result.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.reg_result.grid_remove()

    def autofill_batch(self):
        b = next((b for b in self.ctl.batches() if b["batch_number"] == self.reg_batch.get()), None)
        if b:
            self.reg_drug.set(b["drug_id"])
            self.reg_name.set(b["product_name"])
            self.reg_mfr.set(b["manufacturer"])
            self.reg_mfg.set(b["manufacturing_date"])
            self.reg_exp.set(b["expiry_date"])
            if b["manufacturer"] in self.ctl.participant_ids("Manufacturer", authorized_only=True):
                self.reg_registrant.set(b["manufacturer"])

    def do_save_batch(self):
        """Create your own batch master record from the fields (type a new batch number first)."""
        res = self.ctl.add_batch(dict(batch_number=self.reg_batch.get(), drug_id=self.reg_drug.get(),
                                      product_name=self.reg_name.get(), manufacturer=self.reg_mfr.get(),
                                      manufacturing_date=self.reg_mfg.get(), expiry_date=self.reg_exp.get()))
        self._grid_msg(self.reg_result, res.ok, f"{'Saved' if res.ok else 'NOT SAVED'}  [{res.code}]  {res.message}")
        if res.ok:
            self.reg_batch_box.configure(values=[b["batch_number"] for b in self.ctl.batches()])
            if self.reg_mfr.get() in self.ctl.participant_ids("Manufacturer", authorized_only=True):
                self.reg_registrant.set(self.reg_mfr.get())
        return res

    def do_register(self):
        product = dict(serial_number=self.reg_serial.get(), drug_id=self.reg_drug.get(),
                       product_name=self.reg_name.get(), batch_number=self.reg_batch.get(),
                       manufacturer=self.reg_mfr.get(), manufacturing_date=self.reg_mfg.get(),
                       expiry_date=self.reg_exp.get())
        res = self.ctl.register(product, self.reg_registrant.get())
        self._grid_msg(self.reg_result, res.ok,
                       f"{'Registered' if res.ok else 'REJECTED'}  [{res.code}]  {res.message}")
        if res.ok:
            self.reg_serial.set(self.ctl.next_serial())
        self.refresh_status()
        self.refresh_products()
        return res

    # =============================================================== 2 products
    def _build_products(self, f):
        top = self.card(f)
        top.pack(fill="x", pady=(0, 12))
        self.label(top, "Search", 13, True).pack(side="left", padx=(20, 10), pady=14)
        self.prod_filter = tk.StringVar()
        self.prod_filter.trace_add("write", lambda *_: self.refresh_products())
        self.entry(top, self.prod_filter, 320, "serial, product, batch, owner, status...").pack(side="left", pady=12)
        self.prod_count = self.label(top, "", 12, muted=True)
        self.prod_count.pack(side="left", padx=14)
        self.label(top, "Double-click a row to track its history", 12, muted=True).pack(side="right", padx=20)
        frame, self.prod_tree = self.table(f, [
            ("serial", "Serial", 100), ("name", "Product", 190), ("batch", "Batch", 120),
            ("mfr", "Manufacturer", 140), ("exp", "Expiry", 100), ("owner", "Current owner", 140),
            ("status", "Status", 140)], height=18)
        frame.pack(fill="both", expand=True)
        self.prod_tree.bind("<Double-1>", self._on_product_double_click)

    def refresh_products(self):
        rows = self.ctl.products(self.prod_filter.get())
        fill_tree(self.prod_tree, [(p["serial_number"], p["product_name"], p["batch_number"],
                                    p["manufacturer"], p["expiry_date"], p["current_owner"],
                                    p["current_status"]) for p in rows])
        total = self.ctl.stats()["products"]
        self.prod_count.configure(text=f"{len(rows)} shown of {total}")

    def _on_product_double_click(self, _event):
        sel = self.prod_tree.selection()
        if sel:
            self.hist_serial.set(self.prod_tree.item(sel[0], "values")[0])
            self.select_tab("history")
            self.do_history()

    # =============================================================== 3 transfer
    def _build_transfer(self, f):
        f.grid_columnconfigure(0, weight=3)
        f.grid_columnconfigure(1, weight=2)
        card = self.card(f)
        card.grid(row=0, column=0, sticky="new", padx=(0, 10))
        self.label(card, "Custody hand-over", 16, True).grid(row=0, column=0, columnspan=2, sticky="w",
                                                             padx=26, pady=(22, 2))
        self.tr_serial, self.tr_sender = tk.StringVar(), tk.StringVar()
        self.tr_receiver, self.tr_evidence = tk.StringVar(), tk.StringVar()
        people = self.ctl.participant_ids()
        self.label(card, "Serial number", 12, muted=True).grid(row=1, column=0, sticky="w", padx=(26, 8), pady=(12, 2))
        self.entry(card, self.tr_serial, 270).grid(row=2, column=0, sticky="w", padx=(26, 8))
        self.button(card, "Look up current holder", self.do_lookup_holder, "secondary", 200).grid(
            row=2, column=1, sticky="w", padx=8)
        self.label(card, "Sender", 12, muted=True).grid(row=3, column=0, sticky="w", padx=(26, 8), pady=(12, 2))
        self.combo(card, self.tr_sender, people, 270).grid(row=4, column=0, sticky="w", padx=(26, 8))
        self.label(card, "Receiver", 12, muted=True).grid(row=3, column=1, sticky="w", padx=8, pady=(12, 2))
        self.combo(card, self.tr_receiver, people, 270).grid(row=4, column=1, sticky="w", padx=8)
        self.label(card, "Authorization evidence", 12, muted=True).grid(row=5, column=0, sticky="w", padx=(26, 8),
                                                                       pady=(12, 2))
        self.entry(card, self.tr_evidence, 270).grid(row=6, column=0, sticky="w", padx=(26, 8))
        self.button(card, "Generate evidence", self.do_generate_evidence, "secondary", 200).grid(
            row=6, column=1, sticky="w", padx=8)
        self.button(card, "Transfer product", self.do_transfer, "primary", 190).grid(
            row=7, column=0, sticky="w", padx=26, pady=(22, 10))
        self.tr_info = self.label(card, "", 12, muted=True, justify="left", wraplength=560)
        self.tr_info.grid(row=8, column=0, columnspan=2, sticky="w", padx=26, pady=(0, 22))
        self.tr_result = self.banner(f, "")
        self.tr_result.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.tr_result.grid_remove()
        side = self.card(f)
        side.grid(row=0, column=1, sticky="new")
        self.label(side, "The valid path", 16, True).pack(anchor="w", padx=24, pady=(22, 10))
        for i, role in enumerate(STAGES):
            ctk.CTkLabel(side, text=f"  {i + 1}   {role}", anchor="w", height=36, corner_radius=10,
                         fg_color=ACCENT_SOFT, text_color=INK, font=self.font(13, True)).pack(fill="x", padx=24, pady=3)
        ctk.CTkLabel(side, text="Each hand-over must go exactly one step forward, from the current holder, with "
                     "authorization evidence. Try a wrong sender, a skipped stage, 'Rogue_Actor' or empty "
                     "evidence to see the rejection codes.", font=self.font(12), text_color=MUTED, anchor="w",
                     justify="left", wraplength=330).pack(anchor="w", padx=24, pady=(12, 22))

    def do_lookup_holder(self):
        p = self.ctl.product(self.tr_serial.get())
        if not p:
            self.tr_info.configure(text="Serial number not found in the product register")
            return
        self.tr_sender.set(p["current_owner"])
        self.tr_info.configure(text=f"{p['product_name']} is held by {p['current_owner']} [{p['current_status']}]")

    def do_generate_evidence(self):
        self.tr_evidence.set(f"AUTH-{self.tr_sender.get()}-{self.tr_serial.get().strip()}")

    def do_transfer(self):
        res = self.ctl.transfer(self.tr_serial.get().strip(), self.tr_sender.get(), self.tr_receiver.get(),
                                self.tr_evidence.get())
        self._grid_msg(self.tr_result, res.ok, f"{'Transferred' if res.ok else 'REJECTED'}  [{res.code}]  {res.message}")
        self.refresh_status()
        self.refresh_products()
        return res

    # =============================================================== 4 history
    def _build_history(self, f):
        top = self.card(f)
        top.pack(fill="x", pady=(0, 12))
        self.label(top, "Serial number", 13, True).pack(side="left", padx=(20, 10), pady=14)
        self.hist_serial = tk.StringVar()
        e = self.entry(top, self.hist_serial, 240)
        e.pack(side="left", pady=12)
        e.bind("<Return>", lambda _e: self.do_history())
        self.button(top, "Show history", self.do_history, "primary", 150).pack(side="left", padx=12)
        self.hist_summary = self.label(f, "", 13, muted=True)
        self.hist_summary.pack(anchor="w", pady=(0, 8), padx=4)
        self.timeline = self.card(f)
        self.timeline.pack(fill="x", pady=(0, 12))
        self._draw_timeline([])
        self.label(f, "Custody events (database)", 13, True).pack(anchor="w", pady=(2, 4))
        frame, self.hist_events = self.table(f, [
            ("id", "Event #", 70), ("time", "Timestamp", 160), ("type", "Type", 90), ("sender", "Sender", 140),
            ("receiver", "Receiver", 140), ("evidence", "Authorization evidence", 280)], height=4)
        frame.pack(fill="both", expand=True, pady=(0, 8))
        self.label(f, "Ledger blocks (SHA-256 hash chain)", 13, True).pack(anchor="w", pady=(2, 4))
        frame, self.hist_blocks = self.table(f, [
            ("block", "Block", 70), ("time", "Timestamp", 160), ("hash", "Block hash (first 24)", 250),
            ("prev", "Previous hash (first 24)", 250)], height=4)
        frame.pack(fill="both", expand=True)

    def _draw_timeline(self, events):
        for w in self.timeline.winfo_children():
            w.destroy()
        reached = {}
        for e in events:
            role = e["receiver"].split("_")[0]
            if role in STAGES and role not in reached:
                reached[role] = e
        row = ctk.CTkFrame(self.timeline, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=16)
        for i in range(4):
            row.grid_columnconfigure(i * 2, weight=1, uniform="node")
        for i, role in enumerate(STAGES):
            e = reached.get(role)
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.grid(row=0, column=i * 2, sticky="n")
            ctk.CTkLabel(col, text="✓" if e else str(i + 1), width=42, height=42, corner_radius=21,
                         fg_color=ACCENT if e else ("#cbd5e1", "#2a3753"), text_color="#ffffff" if e else MUTED,
                         font=self.font(15, True)).pack()
            ctk.CTkLabel(col, text=role, font=self.font(13, True), text_color=INK if e else MUTED,
                         width=180).pack(pady=(6, 0))
            ctk.CTkLabel(col, text=e["receiver"] if e else "not reached", font=self.font(12), text_color=MUTED,
                         width=180).pack()
            ctk.CTkLabel(col, text=e["timestamp"] if e else " ", font=self.font(11), text_color=MUTED,
                         width=180).pack()
            if i < 3:
                done = e is not None and reached.get(STAGES[i + 1]) is not None
                ctk.CTkFrame(row, fg_color=ACCENT if done else ("#cbd5e1", "#2a3753"), height=4, corner_radius=2
                             ).grid(row=0, column=i * 2 + 1, sticky="ew", pady=(19, 0))
                row.grid_columnconfigure(i * 2 + 1, weight=1, uniform="link")

    def do_history(self):
        serial = self.hist_serial.get().strip()
        events, blocks = self.ctl.history(serial)
        fill_tree(self.hist_events, [(e["event_id"], e["timestamp"], e["event_type"], e["sender"],
                                      e["receiver"], e["authorization_evidence"]) for e in events])
        fill_tree(self.hist_blocks, [(b["block"], b["timestamp"], b["hash"][:24], b["previous_hash"][:24])
                                     for b in blocks])
        self._draw_timeline(events)
        p = self.ctl.product(serial)
        if not serial:
            self.hist_summary.configure(text="Enter a serial number.", text_color=MUTED)
        elif p:
            self.hist_summary.configure(
                text=f"{p['product_name']}  |  held by {p['current_owner']} [{p['current_status']}]  |  "
                     f"{len(events)} event(s), {len(blocks)} ledger block(s)", text_color=INK)
        else:
            self.hist_summary.configure(text=f"{serial}: not in the product register "
                                             f"({len(events)} event(s) refer to it)", text_color=TONES["bad"][0])

    # =============================================================== 5 verify
    def _build_verify(self, f):
        f.grid_columnconfigure(0, weight=1, uniform="v")
        f.grid_columnconfigure(1, weight=1, uniform="v")
        top = self.card(f)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.label(top, "Serial number", 13, True).pack(side="left", padx=(20, 10), pady=14)
        self.ver_serial = tk.StringVar()
        e = self.entry(top, self.ver_serial, 260)
        e.pack(side="left", pady=12)
        e.bind("<Return>", lambda _e: self.do_verify())
        self.button(top, "Verify", self.do_verify, "primary", 140).pack(side="left", padx=12)
        self.ver_status = ctk.CTkLabel(f, text="", height=56, corner_radius=14, anchor="w", font=self.font(20, True),
                                       text_color=MUTED, fg_color="transparent")
        self.ver_status.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.ver_note = self.label(f, "", 13, muted=True, justify="left", wraplength=1000)
        self.ver_note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 10))
        facts = self.card(f)
        facts.grid(row=3, column=0, sticky="nsew", padx=(0, 6), pady=(0, 10))
        self.label(facts, "Product", 14, True).pack(anchor="w", padx=20, pady=(16, 6))
        self.ver_facts = ctk.CTkLabel(facts, text="Enter a serial number and press Verify.", font=self.font(13),
                                      text_color=MUTED, anchor="nw", justify="left", wraplength=520)
        self.ver_facts.pack(anchor="w", padx=20, pady=(0, 16), fill="x")
        fi = self.card(f)
        fi.grid(row=3, column=1, sticky="nsew", padx=(6, 0), pady=(0, 10))
        self.label(fi, "Findings", 14, True).pack(anchor="w", padx=20, pady=(16, 6))
        self.ver_findings = ctk.CTkScrollableFrame(fi, fg_color="transparent", height=140)
        self.ver_findings.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.ver_text = self.textbox(f, height=170)
        self.ver_text.grid(row=4, column=0, columnspan=2, sticky="nsew")
        f.grid_rowconfigure(4, weight=1)

    def do_verify(self):
        serial = self.ver_serial.get().strip()
        if not serial:
            self.tone(self.ver_status, "Enter a serial number", "muted")
            return None
        rep = self.ctl.verify(serial)
        kind = status_kind(rep["status"])
        self.ver_status.configure(text=rep["status"], text_color=TONES[kind][0], fg_color=TONES[kind][1])
        self.ver_note.configure(text=rep["message"])
        p = rep.get("product")
        if p:
            self.ver_facts.configure(text_color=INK, text=(
                f"{p['product_name']}  ({p['drug_id']})\nBatch    {p['batch_number']}\nMaker    {p['manufacturer']}\n"
                f"Made     {p['manufacturing_date']}\nExpires  {p['expiry_date']}\n"
                f"Holder   {p['current_owner']}  [{p['current_status']}]\nEvents   {len(rep.get('history') or [])}"))
        else:
            self.ver_facts.configure(text_color=MUTED, text="This serial number is not in the product register.")
        for w in self.ver_findings.winfo_children():
            w.destroy()
        if not rep["findings"]:
            ctk.CTkLabel(self.ver_findings, text="No rule failed.", font=self.font(13), text_color=TONES["ok"][0],
                         anchor="w").pack(anchor="w", padx=8, pady=6)
        for fd in rep["findings"]:
            k = "warn" if fd["severity"] == "warning" else "bad"
            row = ctk.CTkFrame(self.ver_findings, fg_color=TONES[k][1], corner_radius=10)
            row.pack(fill="x", pady=3, padx=2)
            ctk.CTkLabel(row, text=fd["rule"], font=self.font(11, True), text_color=TONES[k][0], anchor="w").pack(
                anchor="w", padx=12, pady=(8, 0))
            ctk.CTkLabel(row, text=fd["message"], font=self.font(12), text_color=INK, anchor="w", justify="left",
                         wraplength=430).pack(anchor="w", padx=12, pady=(0, 8))
        set_text(self.ver_text, vf.VerificationEngine.format_report(rep))
        return rep

    # =============================================================== 6 ledger
    def _build_ledger(self, f):
        top = self.card(f)
        top.pack(fill="x", pady=(0, 12))
        self.button(top, "Verify ledger now", self.do_check_ledger, "primary", 160).pack(side="left", padx=(18, 8), pady=14)
        self.button(top, "Save anchor", self.do_save_anchor, "secondary", 120).pack(side="left", padx=4)
        self.button(top, "Verify against saved anchor", lambda: self.do_check_ledger(use_anchor=True), "secondary",
                    210).pack(side="left", padx=4)
        self.label(top, "|", 16, muted=True).pack(side="left", padx=10)
        self.label(top, "Demo: alter block #", 12, muted=True).pack(side="left", padx=(0, 6))
        self.tamper_block = tk.StringVar(value="1")
        self.entry(top, self.tamper_block, 70).pack(side="left")
        self.button(top, "Simulate tampering", self.do_tamper_demo, "danger", 170).pack(side="left", padx=10)
        self.led_status = ctk.CTkLabel(f, text="Press 'Verify ledger now'", height=56, corner_radius=14, anchor="w",
                                       font=self.font(20, True), text_color=MUTED, fg_color="transparent")
        self.led_status.pack(fill="x")
        self.led_info = self.label(f, "", 13, muted=True, justify="left", wraplength=1000)
        self.led_info.pack(anchor="w", pady=(6, 10))
        frame, self.led_issues = self.table(f, [
            ("block", "Block", 80), ("code", "Failure type", 190), ("message", "Details", 700)], height=9)
        frame.pack(fill="both", expand=True)
        self.label(f, "A single-node hash chain shows tamper evidence only. Truncating the newest blocks or rewriting "
                      "the whole chain is caught only by an anchor (head hash + length) stored outside the database.",
                   12, muted=True, justify="left", wraplength=1000).pack(anchor="w", pady=(10, 0))

    def _show_issues(self, issues):
        fill_tree(self.led_issues, [(i["block_index"], i["code"], i["message"]) for i in issues],
                  tags=["bad"] * len(issues))

    def do_check_ledger(self, use_anchor=False):
        res = self.ctl.ledger_status(use_anchor)
        ok = res["ok"]
        self.tone(self.led_status, "LEDGER INTEGRITY VERIFIED" if ok else "LEDGER INTEGRITY FAILED",
                  "ok" if ok else "bad")
        extra = "checked against saved anchor" if res["anchor_used"] else "chain-only check (no anchor)"
        self.led_info.configure(text=f"{res['blocks']} blocks  |  head hash {res['head_hash'][:32]}...  |  "
                                     f"{extra}. {res['message']}")
        self._show_issues(res["issues"])
        return res

    def do_save_anchor(self):
        data = self.ctl.save_anchor()
        self.led_info.configure(text=f"Anchor saved outside the database: {data['length']} blocks, "
                                     f"head {data['head_hash'][:32]}... ({data['saved_at']})")
        return data

    def do_tamper_demo(self):
        try:
            res = self.ctl.tamper_demo(int(self.tamper_block.get()))
        except (ValueError, TypeError) as e:
            self.tone(self.led_status, "Cannot run demo", "muted")
            self.led_info.configure(text=str(e))
            return None
        detected = not res["after_ok"]
        self.tone(self.led_status, "TAMPERING DETECTED (demo on a copy)" if detected else "TAMPERING NOT DETECTED",
                  "bad" if detected else "ok")
        self.led_info.configure(text=f"{res['message']}. The real ledger was not changed.")
        self._show_issues(res["issues"])
        return res

    # =============================================================== 7 alerts
    def _build_alerts(self, f):
        top = self.card(f)
        top.pack(fill="x", pady=(0, 10))
        self.label(top, "Scan", 13, True).pack(side="left", padx=(20, 10), pady=14)
        self.alert_source = tk.StringVar(value="Application database")
        self.combo(top, self.alert_source, list(SOURCES), 280).pack(side="left", pady=12)
        self.button(top, "Scan for suspicious records", self.do_scan, "primary", 230).pack(side="left", padx=12)
        self.alert_summary = self.label(top, "", 13, muted=True)
        self.alert_summary.pack(side="left", padx=10)
        tv = ctk.CTkTabview(f, corner_radius=14, fg_color=CARD, border_width=1, border_color=BORDER,
                            segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_H)
        tv.pack(fill="both", expand=True)
        self.alert_inner = tv
        page1, page2 = tv.add("Flagged records"), tv.add("Rejected attempts")
        frame, self.alert_tree = self.table(page1, [
            ("serial", "Serial", 100), ("status", "Status", 230), ("severity", "Severity", 80),
            ("finding", "First finding", 640)], height=7)
        frame.pack(fill="both", expand=True)
        self.alert_tree.bind("<<TreeviewSelect>>", self._on_alert_select)
        self.alert_text = self.textbox(page1, height=140)
        self.alert_text.pack(fill="both", expand=True, pady=(8, 0))
        frame, self.rej_tree = self.table(page2, [
            ("time", "Timestamp", 160), ("kind", "Kind", 90), ("serial", "Serial", 100), ("actor", "Actor", 130),
            ("code", "Rejection code", 190), ("message", "Message", 480)], height=12)
        frame.pack(fill="both", expand=True)

    def do_scan(self):
        source = SOURCES[self.alert_source.get()]
        try:
            reps = self.ctl.alerts(source)
        except FileNotFoundError as e:
            self.alert_summary.configure(text=str(e), text_color=TONES["bad"][0])
            return None
        self._alert_reports = {r["serial_number"]: r for r in reps}
        fill_tree(self.alert_tree,
                  [(r["serial_number"], r["status"], r["findings"][0]["severity"] if r["findings"] else "",
                    r["findings"][0]["message"][:140]) for r in reps],
                  tags=[("warning" if r["status"] in WARNING_RULES else "alert") for r in reps])
        set_text(self.alert_text, "Select a row to see the full verification report.")
        counts = {}
        for r in reps:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        self.alert_summary.configure(
            text=(f"{len(reps)} flagged record(s): " + ", ".join(f"{k} x{v}" for k, v in counts.items()))
            if reps else "No flagged records found.",
            text_color=TONES["bad"][0] if reps else TONES["ok"][0])
        rej = self.ctl.rejected_attempts()
        fill_tree(self.rej_tree, [(r["timestamp"], r["kind"], r["serial_number"], r["actor"], r["code"],
                                   r["message"]) for r in rej])
        return reps

    def _on_alert_select(self, _event=None):
        sel = self.alert_tree.selection()
        if sel:
            serial = self.alert_tree.item(sel[0], "values")[0]
            set_text(self.alert_text, vf.VerificationEngine.format_report(self._alert_reports[serial]))

    # =============================================================== 8 results
    def _build_results(self, f):
        top = self.card(f)
        top.pack(fill="x", pady=(0, 10))
        self.label(top, "Results file", 13, True).pack(side="left", padx=(20, 10), pady=14)
        self.res_file = tk.StringVar()
        self.res_box = self.combo(top, self.res_file, [], 380, command=lambda _v: self.do_show_results())
        self.res_box.pack(side="left", pady=12)
        self.button(top, "Show", self.do_show_results, "primary", 110).pack(side="left", padx=12)
        self.res_info = self.label(f, "", 12, muted=True, justify="left", wraplength=1000)
        self.res_info.pack(anchor="w", pady=(0, 8))
        holder = self.card(f)
        holder.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(holder, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=6, pady=6)
        self.res_tree = ttk.Treeview(inner, show="headings", height=16)
        ys = ctk.CTkScrollbar(inner, command=self.res_tree.yview)
        self.res_tree.configure(yscrollcommand=ys.set)
        self.res_tree.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        self._trees.append(self.res_tree)
        self._tag_tree(self.res_tree)
        self.refresh_results_files()

    def refresh_results_files(self):
        files = self.ctl.results_files()
        self._res_paths = {os.path.basename(p): p for p in files}
        self.res_box.configure(values=list(self._res_paths) or [""])
        if files and not self.res_file.get():
            self.res_file.set(os.path.basename(files[0]))
        if not files:
            self.res_info.configure(text="No results files found in the logs folder. Run the experiment "
                                         "scripts (test_phase3.py, test_phase4.py, ...) first.")

    def do_show_results(self):
        path = self._res_paths.get(self.res_file.get())
        if not path:
            return None
        header, rows = self.ctl.read_table(path)
        self.res_tree.delete(*self.res_tree.get_children())
        self.res_tree.configure(columns=header)
        for k, h in enumerate(header):                      # size each column to its longest cell
            longest = max([len(h)] + [len(r[k]) for r in rows[:500] if k < len(r)])
            self.res_tree.heading(h, text=h, anchor="w")
            self.res_tree.column(h, width=max(100, min(480, longest * 8 + 28)), anchor="w", stretch=True)
        for i, r in enumerate(rows):
            self.res_tree.insert("", "end", values=r, tags=("odd",) if i % 2 else ())
        self.res_info.configure(text=f"{os.path.basename(path)}  |  {len(rows)} row(s). Values come from actual "
                                     f"experiment runs saved in the logs folder.")
        return header, rows

    # =============================================================== sidebar actions
    def _show_progress(self):
        """Full-window overlay with a progress bar, percentage, elapsed time and estimated time left."""
        ov = ctk.CTkFrame(self, fg_color=PAGE, corner_radius=0)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        ov.lift()
        box = self.card(ov)
        box.place(relx=0.5, rely=0.46, anchor="center")
        self.label(box, "Loading the synthetic dataset", 20, True).pack(anchor="w", padx=40, pady=(32, 2))
        self.label(box, "Replaying every custody event through the supply-chain rules and the hash-linked ledger.",
                   13, muted=True).pack(anchor="w", padx=40)
        bar = ctk.CTkProgressBar(box, width=620, height=18, corner_radius=9, progress_color=ACCENT,
                                 fg_color=("#e2e8f0", "#26334d"))
        bar.set(0)
        bar.pack(padx=40, pady=(26, 8))
        pct = self.label(box, "0 %", 30, True)
        pct.pack(anchor="w", padx=40)
        detail = self.label(box, "Starting...", 13, muted=True)
        detail.pack(anchor="w", padx=40, pady=(0, 32))
        return ov, bar, pct, detail

    def do_load_dataset(self):
        if not self.confirm("Load synthetic dataset",
                            "This erases the current application data and replays the 1,000-package "
                            "synthetic dataset through the normal register/transfer rules. Continue?"):
            return None
        import time
        ov, bar, pct, detail = self._show_progress()
        t0 = time.perf_counter()
        self.update()

        def progress(i, n):
            frac = i / n if n else 1
            elapsed = time.perf_counter() - t0
            eta = (elapsed / frac - elapsed) if frac > 0.02 else None
            bar.set(frac)
            pct.configure(text=f"{frac * 100:.0f} %")
            detail.configure(text=f"{i:,} of {n:,} events   |   elapsed {elapsed:.0f} s"
                                  + (f"   |   about {eta:.0f} s left" if eta is not None else ""))
            self.status_var.set(f"Loading synthetic dataset...  {i}/{n}")
            self.update()

        try:
            res = self.ctl.load_synthetic_dataset(progress=progress)
        except FileNotFoundError as e:
            ov.destroy()
            messagebox.showerror("Dataset not found", f"{e}\nRun generate_dataset.py first.")
            self.refresh_status()
            return None
        pct.configure(text="100 %")
        detail.configure(text=f"Done in {time.perf_counter() - t0:.0f} s: {res['accepted']:,} events accepted, "
                              f"{res['rejected']:,} rejected by the rules, {res['blocks']:,} ledger blocks.")
        self.update()
        self.after(900, ov.destroy)
        self.refresh_all()
        return res

    def do_reset(self):
        if not self.confirm("Reset application data", "Erase all products, events, ledger blocks and "
                                                      "rejected attempts?"):
            return False
        self.ctl.reset()
        self.refresh_all()
        return True


if __name__ == "__main__":
    App().mainloop()
