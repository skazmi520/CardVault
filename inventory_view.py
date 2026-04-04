"""Inventory view — graded card list, add dialog, detail/edit dialog, mark-sold dialog."""

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
from PIL import Image
from datetime import date
import database as db

try:
    from tkcalendar import DateEntry
    _HAS_CAL = True
except ImportError:
    _HAS_CAL = False

COMPANY_COLORS = db.COMPANY_COLORS

def _fmt_usd(val) -> str:
    if val is None:
        return "—"
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"

def _date_str() -> str:
    return date.today().isoformat()

def _make_date_entry(parent, initial=None):
    """Return a tkcalendar DateEntry or fallback CTkEntry if not installed."""
    from datetime import date as _date
    d = initial if isinstance(initial, _date) else _date.today()
    if not _HAS_CAL:
        e = ctk.CTkEntry(parent, height=32, width=160, placeholder_text="YYYY-MM-DD")
        e.insert(0, d.isoformat())
        return e
    is_dark = ctk.get_appearance_mode() == "Dark"
    bg = "#2b2b2b" if is_dark else "#f5f5f5"
    fg = "#e0e0e0" if is_dark else "#1a1a1a"
    return DateEntry(
        parent, width=16, date_pattern="yyyy-mm-dd",
        year=d.year, month=d.month, day=d.day,
        font=("SF Pro Text", 12),
        background="#007AFF", foreground="white",
        selectbackground="#007AFF", selectforeground="white",
        headersbackground="#005BD9", headersforeground="white",
        normalbackground=bg, normalforeground=fg,
        weekendbackground=bg, weekendforeground=fg,
        othermonthbackground="#222222" if is_dark else "#e5e5e5",
        othermonthforeground="#888888",
        borderwidth=0,
    )

# ── helpers ───────────────────────────────────────────────────────────────────

def _apply_treeview_style():
    style = ttk.Style()
    style.theme_use("clam")
    mode = ctk.get_appearance_mode()
    bg   = "#2b2b2b" if mode == "Dark" else "#f0f0f0"
    fg   = "#e0e0e0" if mode == "Dark" else "#1a1a1a"
    sel  = "#3a7ebf"
    hdr  = "#1e1e1e" if mode == "Dark" else "#d0d0d0"

    style.configure(
        "Treeview",
        background=bg, foreground=fg,
        fieldbackground=bg, rowheight=34,
        borderwidth=0, font=("SF Pro Text", 12),
    )
    style.configure(
        "Treeview.Heading",
        background=hdr, foreground=fg,
        font=("SF Pro Text", 12, "bold"), relief="flat",
    )
    style.map("Treeview", background=[("selected", sel)], foreground=[("selected", "white")])


# ── main view ─────────────────────────────────────────────────────────────────

class InventoryView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self._filter_company = None   # None = All
        self._sort_col = "acquisition_date"
        self._sort_rev = True
        self._search_text = ""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()
        self.refresh()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        _apply_treeview_style()

        # header bar
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Inventory",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ Add Card", width=110, height=34,
                      corner_radius=8,
                      command=self._open_add_dialog).pack(side="right")

        # search + sort bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 0))

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(bar, textvariable=self._search_var,
                     placeholder_text="Search by name, serial, set…",
                     width=260, height=32).pack(side="left")

        # company filter chips
        chip_frame = ctk.CTkFrame(bar, fg_color="transparent")
        chip_frame.pack(side="left", padx=(14, 0))
        self._chip_btns: dict[str | None, ctk.CTkButton] = {}

        for label, key in [("All", None)] + [(c, c) for c in db.GRADING_COMPANIES]:
            btn = ctk.CTkButton(
                chip_frame, text=label, width=54, height=28, corner_radius=14,
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._set_company_filter(k),
            )
            btn.pack(side="left", padx=3)
            self._chip_btns[key] = btn
        self._update_chips()

        # sort dropdown
        self._sort_var = ctk.StringVar(value="Date Obtained")
        sort_opts = ["Date Obtained", "Card Name", "Cost", "Market Value", "Grade"]
        ctk.CTkOptionMenu(bar, values=sort_opts, variable=self._sort_var,
                          width=140, height=28,
                          command=self._on_sort_change).pack(side="right")
        ctk.CTkLabel(bar, text="Sort:", font=ctk.CTkFont(size=12)).pack(side="right", padx=(0, 4))

        # treeview
        tv_frame = ctk.CTkFrame(self, fg_color="transparent")
        tv_frame.grid(row=2, column=0, sticky="nsew", padx=24, pady=12)
        tv_frame.grid_rowconfigure(0, weight=1)
        tv_frame.grid_columnconfigure(0, weight=1)

        cols = ("company", "grade", "serial", "card_name", "set_name",
                "cost", "market", "gain")
        self.tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                 selectmode="browse")

        headings = {
            "company":   ("Co.",    80),
            "grade":     ("Grade",  70),
            "serial":    ("Serial", 100),
            "card_name": ("Card Name", 220),
            "set_name":  ("Set",    150),
            "cost":      ("Cost",   90),
            "market":    ("Market Value", 110),
            "gain":      ("Unrealized",  110),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, minwidth=50)

        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", self._on_row_double_click)
        self.tree.bind("<Return>",   self._on_row_double_click)

    # ── data loading ──────────────────────────────────────────────────────────

    def refresh(self):
        self._cards = db.get_graded_cards(sold=False)
        self._apply_filter()

    def _apply_filter(self):
        search = self._search_var.get().lower() if hasattr(self, "_search_var") else ""
        filtered = self._cards

        if self._filter_company:
            filtered = [c for c in filtered if c["grading_company"] == self._filter_company]

        if search:
            filtered = [
                c for c in filtered
                if search in c["card_name"].lower()
                or search in c["serial_number"].lower()
                or search in c["set_name"].lower()
            ]

        self._populate_tree(filtered)

    def _populate_tree(self, cards):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for card in cards:
            market = card["market_value"]
            gain   = (market - card["acquisition_price"]) if market is not None else None

            self.tree.insert("", "end", iid=str(card["id"]), values=(
                card["grading_company"],
                card["grade"],
                card["serial_number"],
                ("★ " if card["is_favorited"] else "") + card["card_name"],
                card["set_name"],
                _fmt_usd(card["acquisition_price"]),
                _fmt_usd(market),
                _fmt_usd(gain),
            ))

    # ── filtering / sorting ───────────────────────────────────────────────────

    def _set_company_filter(self, company):
        self._filter_company = company
        self._update_chips()
        self._apply_filter()

    def _update_chips(self):
        if not hasattr(self, "_chip_btns"):
            return
        for key, btn in self._chip_btns.items():
            if key == self._filter_company:
                color = COMPANY_COLORS.get(key, "#3a7ebf") if key else "#3a7ebf"
                btn.configure(fg_color=color, text_color="white")
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray90"))

    def _on_sort_change(self, choice: str):
        mapping = {
            "Date Obtained": "acquisition_date",
            "Card Name":     "card_name",
            "Cost":          "acquisition_price",
            "Market Value":  "market_value",
            "Grade":         "grade",
        }
        col = mapping.get(choice, "acquisition_date")
        self._sort_col = col
        self._sort_rev = True
        self._apply_filter()

    def _sort_by(self, col: str):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        def key(c):
            v = c[col] if col in c.keys() else ""
            if v is None:
                return (0, 0)
            if isinstance(v, (int, float)):
                return (1, v)
            # Grade column: parse numerically so "10" > "9" > "8" etc.
            if col == "grade":
                try:
                    return (1, float(v))
                except (ValueError, TypeError):
                    return (1, 0)
            return (1, str(v).lower())

        self._cards.sort(key=key, reverse=self._sort_rev)
        self._apply_filter()

    # ── interactions ──────────────────────────────────────────────────────────

    def _on_row_double_click(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        card_id = int(sel[0])
        CardDetailDialog(self, card_id, on_close=self.refresh)

    def _open_add_dialog(self):
        AddGradedCardDialog(self, on_save=self.refresh)


# ── add card dialog ───────────────────────────────────────────────────────────

class AddGradedCardDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_save=None):
        super().__init__(parent)
        self.title("Add Graded Card")
        self.geometry("560x720")
        self.resizable(False, True)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)
        self._on_save = on_save
        self._photo_path: str | None = None
        self._trade_rows: list[dict] = []
        self._price_var = ctk.StringVar()
        self._fee_var   = ctk.StringVar()
        self._price_var.trace_add("write", self._update_total)
        self._fee_var.trace_add("write",   self._update_total)
        self._trade_outer = None
        self._build()

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=16)
        scroll.grid_columnconfigure((0, 1), weight=1)
        self._scroll = scroll
        r = 0

        def lbl(text, row, col=0, span=2):
            ctk.CTkLabel(scroll, text=text, font=ctk.CTkFont(size=12), anchor="w"
                         ).grid(row=row, column=col, columnspan=span,
                                sticky="w", pady=(8, 0))

        def entry(row, col=0, span=2, **kw):
            e = ctk.CTkEntry(scroll, height=32, **kw)
            e.grid(row=row, column=col, columnspan=span, sticky="ew",
                   padx=(0, 6) if col == 0 and span == 1 else (6, 0) if col == 1 else 0,
                   pady=(2, 0))
            return e

        # ── Card Identity ─────────────────────────────────────────────────────
        lbl("Card Name *", r); r += 1
        self._name = entry(r); r += 1

        lbl("Card Number", r, 0, 1); lbl("Set Name", r, 1, 1); r += 1
        self._number = entry(r, 0, 1)
        self._set    = entry(r, 1, 1); r += 1

        lbl("Year", r); r += 1
        self._year = entry(r); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        # ── Grading Info ──────────────────────────────────────────────────────
        lbl("Grading Company *", r, 0, 1); lbl("Grade *", r, 1, 1); r += 1
        self._company_var = ctk.StringVar(value="PSA")
        ctk.CTkOptionMenu(scroll, values=db.GRADING_COMPANIES,
                          variable=self._company_var, height=32
                          ).grid(row=r, column=0, sticky="ew", padx=(0, 6))
        self._grade = entry(r, 1, 1); r += 1

        lbl("Serial Number", r); r += 1
        self._serial = entry(r); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        # ── Acquisition ───────────────────────────────────────────────────────
        lbl("Purchase Date *", r); r += 1
        self._acq_date = _make_date_entry(scroll)
        self._acq_date.grid(row=r, column=0, columnspan=2, sticky="w", pady=(2, 0)); r += 1

        lbl("Purchase Type", r); r += 1
        seg_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        seg_frame.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0)); r += 1
        self._type_seg = ctk.CTkSegmentedButton(
            seg_frame, values=["Cash", "Trade", "Cash & Trade"],
            command=self._on_type_change, width=320, height=32
        )
        self._type_seg.set("Cash")
        self._type_seg.pack(side="left")

        # Dynamic acquisition detail (rebuilt on type change)
        self._acq_detail = ctk.CTkFrame(scroll, fg_color="transparent")
        self._acq_detail.grid(row=r, column=0, columnspan=2, sticky="ew"); r += 1
        self._acq_detail.grid_columnconfigure((0, 1), weight=1)

        # Grading fee — always visible
        lbl("Grading Fee (USD)", r); r += 1
        fee_e = ctk.CTkEntry(scroll, height=32, textvariable=self._fee_var,
                             placeholder_text="0.00")
        fee_e.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0)); r += 1

        # Total cost
        self._total_cost_lbl = ctk.CTkLabel(scroll, text="Total Cost: —",
                                            font=ctk.CTkFont(size=12), text_color="gray")
        self._total_cost_lbl.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0)); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        lbl("Market Value (optional)", r); r += 1
        self._mkt = entry(r, placeholder_text="leave blank if unknown"); r += 1

        lbl("Notes", r); r += 1
        self._notes = ctk.CTkTextbox(scroll, height=60)
        self._notes.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0)); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        lbl("Photo (optional)", r); r += 1
        self._photo_lbl = ctk.CTkLabel(scroll, text="No photo selected", text_color="gray")
        self._photo_lbl.grid(row=r, column=0, sticky="w")
        ctk.CTkButton(scroll, text="Choose…", width=90, height=28,
                      command=self._pick_photo
                      ).grid(row=r, column=1, sticky="e", pady=(2, 0)); r += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Cancel", width=110,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Save Card", width=110,
                      command=self._save).pack(side="right")

        # Seed the initial type view
        self._on_type_change("Cash")

    # ── type switching ─────────────────────────────────────────────────────────

    def _on_type_change(self, acq_type: str | None = None):
        if acq_type is None:
            acq_type = self._type_seg.get()
        for w in self._acq_detail.winfo_children():
            w.destroy()
        self._acq_detail.grid_columnconfigure((0, 1), weight=1)
        self._trade_outer = None
        r = 0

        if acq_type in ("Cash", "Cash & Trade"):
            label_text = "Purchase Price (USD) *" if acq_type == "Cash" else "Cash Paid (USD) *"
            ctk.CTkLabel(self._acq_detail, text=label_text,
                         font=ctk.CTkFont(size=12), anchor="w"
                         ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(8, 0))
            r += 1
            ctk.CTkEntry(self._acq_detail, height=32,
                         textvariable=self._price_var, placeholder_text="0.00"
                         ).grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0))
            r += 1

        if acq_type in ("Trade", "Cash & Trade"):
            hdr = ctk.CTkFrame(self._acq_detail, fg_color="transparent")
            hdr.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(12, 4))
            hdr.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(hdr, text="Traded Cards",
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
                         ).grid(row=0, column=0, sticky="w")
            ctk.CTkButton(hdr, text="+ Add Card", width=90, height=26,
                          corner_radius=8, command=self._add_trade_row
                          ).grid(row=0, column=1, sticky="e")
            r += 1

            self._trade_outer = ctk.CTkFrame(self._acq_detail, corner_radius=8)
            self._trade_outer.grid(row=r, column=0, columnspan=2, sticky="ew")
            self._trade_outer.grid_columnconfigure(1, weight=1)

            if not self._trade_rows:
                self._add_trade_row_data()
            self._rebuild_trade_rows()

        self._update_total()

    # ── trade row helpers ──────────────────────────────────────────────────────

    def _add_trade_row_data(self):
        name_var = ctk.StringVar()
        mv_var   = ctk.StringVar()
        mv_var.trace_add("write", self._update_total)
        self._trade_rows.append({"name": name_var, "mv": mv_var})

    def _add_trade_row(self):
        self._add_trade_row_data()
        self._on_type_change()

    def _remove_trade_row(self, idx: int):
        if len(self._trade_rows) <= 1:
            return
        self._trade_rows.pop(idx)
        self._on_type_change()

    def _rebuild_trade_rows(self):
        if self._trade_outer is None:
            return
        for w in self._trade_outer.winfo_children():
            w.destroy()
        self._trade_outer.grid_columnconfigure(1, weight=1)

        for col, text in enumerate(["", "Card Name", "Market Value", ""]):
            ctk.CTkLabel(self._trade_outer, text=text,
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).grid(row=0, column=col,
                                padx=(14 if col == 0 else 6, 6),
                                pady=(8, 2), sticky="w")

        for i, row in enumerate(self._trade_rows):
            ri = i + 1
            ctk.CTkLabel(self._trade_outer, text=f"{i+1}.",
                         width=20, font=ctk.CTkFont(size=12), text_color="gray"
                         ).grid(row=ri, column=0, padx=(14, 4), pady=4)

            name_f = ctk.CTkFrame(self._trade_outer, fg_color="transparent")
            name_f.grid(row=ri, column=1, sticky="ew", padx=4, pady=4)
            name_f.grid_columnconfigure(0, weight=1)
            ctk.CTkEntry(name_f, textvariable=row["name"],
                         placeholder_text="Card name", height=30
                         ).grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(name_f, text="Inventory ↓", width=94, height=26,
                          font=ctk.CTkFont(size=11),
                          fg_color="transparent", border_width=1,
                          command=lambda nv=row["name"], mv=row["mv"]:
                              self._open_inventory_picker(nv, mv)
                          ).grid(row=0, column=1, padx=(4, 0))

            mv_f = ctk.CTkFrame(self._trade_outer, fg_color="transparent")
            mv_f.grid(row=ri, column=2, padx=4, pady=4, sticky="w")
            ctk.CTkLabel(mv_f, text="$",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(side="left")
            ctk.CTkEntry(mv_f, textvariable=row["mv"],
                         placeholder_text="0.00", width=100, height=30).pack(side="left")

            ctk.CTkButton(self._trade_outer, text="×", width=26, height=26,
                          fg_color="transparent",
                          text_color=("gray40", "gray60"),
                          hover_color=("gray80", "gray30"),
                          corner_radius=6,
                          command=lambda idx=i: self._remove_trade_row(idx)
                          ).grid(row=ri, column=3, padx=(4, 10), pady=4)

        ctk.CTkFrame(self._trade_outer, height=6, fg_color="transparent"
                     ).grid(row=len(self._trade_rows) + 1, column=0)

    def _open_inventory_picker(self, name_var: ctk.StringVar, mv_var: ctk.StringVar):
        def on_select(card_name, market_value):
            name_var.set(card_name)
            mv_var.set(f"{market_value:.2f}")
        InventoryPickerDialog(self, on_select=on_select)

    # ── total cost ─────────────────────────────────────────────────────────────

    def _update_total(self, *_):
        acq = self._type_seg.get()
        total = 0.0

        if acq in ("Cash", "Cash & Trade"):
            try:
                total += float(self._price_var.get() or 0)
            except ValueError:
                pass

        if acq in ("Trade", "Cash & Trade"):
            for row in self._trade_rows:
                try:
                    total += float(row["mv"].get() or 0)
                except ValueError:
                    pass

        try:
            total += float(self._fee_var.get() or 0)
        except ValueError:
            pass

        self._total_cost_lbl.configure(
            text=f"Total Cost: ${total:,.2f}" if total > 0 else "Total Cost: —"
        )

    # ── photo ──────────────────────────────────────────────────────────────────

    def _pick_photo(self):
        path = filedialog.askopenfilename(
            title="Select Card Photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.heic *.webp"), ("All", "*.*")]
        )
        if path:
            self._photo_path = path
            from pathlib import Path
            self._photo_lbl.configure(text=Path(path).name,
                                      text_color=("gray10", "gray90"))

    # ── save ───────────────────────────────────────────────────────────────────

    def _save(self):
        name = self._name.get().strip()
        if not name:
            messagebox.showerror("Required", "Card name is required.", parent=self)
            return

        acq = self._type_seg.get()
        price_f = 0.0
        fee_f   = 0.0
        trade_total   = 0.0
        trade_details = ""

        if acq in ("Cash", "Cash & Trade"):
            price_str = self._price_var.get().strip()
            if not price_str:
                messagebox.showerror("Required", "Purchase price is required.", parent=self)
                return
            try:
                price_f = float(price_str)
            except ValueError:
                messagebox.showerror("Invalid", "Purchase price must be a number.", parent=self)
                return

        if acq in ("Trade", "Cash & Trade"):
            if not self._trade_rows:
                messagebox.showerror("Required", "Add at least one traded card.", parent=self)
                return
            parts = []
            for row in self._trade_rows:
                mv_s = row["mv"].get().strip()
                try:
                    mv = float(mv_s) if mv_s else 0.0
                except ValueError:
                    mv = 0.0
                trade_total += mv
                n = row["name"].get().strip() or "Unknown Card"
                parts.append(f"{n}: {_fmt_usd(mv)}")
            trade_details = " | ".join(parts)

        fee_str = self._fee_var.get().strip()
        try:
            fee_f = float(fee_str) if fee_str else 0.0
        except ValueError:
            fee_f = 0.0

        acquisition_price = price_f + trade_total + fee_f
        acq_d = self._acq_date.get().strip()

        photo_filename = None
        if self._photo_path:
            try:
                photo_filename = db.save_photo(self._photo_path)
            except Exception as e:
                messagebox.showwarning("Photo", f"Could not save photo: {e}", parent=self)

        mkt_str = self._mkt.get().strip()
        mkt_f   = float(mkt_str) if mkt_str else None

        card_id = db.add_graded_card(
            serial_number=self._serial.get().strip(),
            grading_company=self._company_var.get(),
            grade=self._grade.get().strip(),
            card_name=name,
            card_number=self._number.get().strip(),
            set_name=self._set.get().strip(),
            photo_filename=photo_filename,
            acquisition_type=acq,
            acquisition_price=acquisition_price,
            grading_fee=fee_f,
            trade_value=trade_total,
            trade_details=trade_details,
            acquisition_date=acq_d,
            notes=self._notes.get("1.0", "end").strip(),
        )

        if mkt_f is not None:
            from database import get_connection
            conn = get_connection()
            conn.execute(
                "UPDATE graded_cards SET market_value=?, market_value_updated=? WHERE id=?",
                (mkt_f, date.today().isoformat(), card_id)
            )
            conn.commit()
            conn.close()

        if self._on_save:
            self._on_save()
        self.destroy()


# ── card detail / edit dialog ─────────────────────────────────────────────────

class CardDetailDialog(ctk.CTkToplevel):
    def __init__(self, parent, card_id: int, on_close=None):
        super().__init__(parent)
        self._card_id  = card_id
        self._on_close = on_close
        self._img_ref  = None
        self._photo_path: str | None = None

        card = db.get_graded_card(card_id)
        self.title(card["card_name"] if card else "Card Detail")
        self.geometry("560x680")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)

        if not card:
            ctk.CTkLabel(self, text="Card not found.").pack(pady=40)
            return

        self._card = card
        self._build(card)

    def _build(self, card):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=16)
        scroll.grid_columnconfigure((0, 1), weight=1)

        r = 0

        # photo
        img_path = db.photo_path(card["photo_filename"])
        if img_path:
            try:
                img = Image.open(img_path).resize((160, 160))
                self._img_ref = ctk.CTkImage(img, size=(160, 160))
                ctk.CTkLabel(scroll, image=self._img_ref, text="").grid(
                    row=r, column=0, columnspan=2, pady=(0, 10)
                )
                r += 1
            except Exception:
                pass

        # Company badge
        color = COMPANY_COLORS.get(card["grading_company"], "#3a7ebf")
        badge_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        badge_frame.grid(row=r, column=0, columnspan=2, sticky="ew"); r += 1

        ctk.CTkLabel(
            badge_frame,
            text=f"  {card['grading_company']} · Grade {card['grade']}  ",
            fg_color=color, corner_radius=8,
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", pady=4)

        if card["is_favorited"]:
            ctk.CTkLabel(badge_frame, text="  ★ Favorite",
                         text_color="#FFD700",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=8)

        def lbl_row(label, value, row):
            ctk.CTkLabel(scroll, text=label, font=ctk.CTkFont(size=11),
                         text_color="gray", anchor="w"
                         ).grid(row=row, column=0, sticky="w", pady=(8, 0))
            ctk.CTkLabel(scroll, text=str(value) if value else "—",
                         font=ctk.CTkFont(size=13), anchor="w"
                         ).grid(row=row, column=1, sticky="w", pady=(8, 0))

        lbl_row("Card Name",      card["card_name"],        r); r += 1
        lbl_row("Serial Number",  card["serial_number"],    r); r += 1
        lbl_row("Card Number",    card["card_number"],      r); r += 1
        lbl_row("Set",            card["set_name"],         r); r += 1
        lbl_row("Acquisition",    f"{card['acquisition_type']}  ·  {card['acquisition_date']}", r); r += 1

        # cost breakdown
        fee      = card["grading_fee"] or 0
        trade_v  = card["trade_value"] if "trade_value" in card.keys() else 0
        trade_d  = card["trade_details"] if "trade_details" in card.keys() else ""
        acq_type = card["acquisition_type"]

        if acq_type == "Trade":
            lbl_row("Trade Value",  _fmt_usd(trade_v), r); r += 1
            if trade_d:
                lbl_row("Traded Cards", trade_d, r); r += 1
            if fee > 0:
                lbl_row("Grading Fee", _fmt_usd(fee), r); r += 1
            lbl_row("Total Cost", _fmt_usd(card["acquisition_price"]), r); r += 1
        elif acq_type == "Cash & Trade":
            cash_paid = card["acquisition_price"] - trade_v - fee
            lbl_row("Cash Paid",   _fmt_usd(cash_paid), r); r += 1
            lbl_row("Trade Value", _fmt_usd(trade_v),   r); r += 1
            if trade_d:
                lbl_row("Traded Cards", trade_d, r); r += 1
            if fee > 0:
                lbl_row("Grading Fee", _fmt_usd(fee), r); r += 1
            lbl_row("Total Cost", _fmt_usd(card["acquisition_price"]), r); r += 1
        else:  # Cash
            if fee > 0:
                purchase_only = card["acquisition_price"] - fee
                lbl_row("Purchase Price", _fmt_usd(purchase_only), r); r += 1
                lbl_row("Grading Fee",    _fmt_usd(fee),           r); r += 1
                lbl_row("Total Cost",     _fmt_usd(card["acquisition_price"]), r); r += 1
            else:
                lbl_row("Total Cost", _fmt_usd(card["acquisition_price"]), r); r += 1

        # market value (editable)
        ctk.CTkLabel(scroll, text="Market Value", font=ctk.CTkFont(size=11),
                     text_color="gray", anchor="w"
                     ).grid(row=r, column=0, sticky="w", pady=(8, 0))
        mkt_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        mkt_frame.grid(row=r, column=1, sticky="w"); r += 1
        self._mkt_entry = ctk.CTkEntry(mkt_frame, width=110, height=28,
                                       placeholder_text="0.00")
        if card["market_value"] is not None:
            self._mkt_entry.insert(0, f"{card['market_value']:.2f}")
        self._mkt_entry.pack(side="left")
        ctk.CTkButton(mkt_frame, text="Update", width=68, height=28,
                      command=self._update_market_value).pack(side="left", padx=(6, 0))

        # unrealized gain
        if card["market_value"] is not None:
            gain = card["market_value"] - card["acquisition_price"]
            color2 = "green" if gain >= 0 else "red"
            lbl_row("Unrealized P&L", _fmt_usd(gain), r); r += 1
            # color the value label
            for w in scroll.grid_slaves(row=r - 1, column=1):
                w.configure(text_color=color2)

        if card["notes"]:
            lbl_row("Notes", card["notes"], r); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=12); r += 1

        # action buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkButton(btn_frame, text="Close", width=90,
                      fg_color="transparent", border_width=1,
                      command=self._close).pack(side="left")

        fav_text = "★ Unfavorite" if card["is_favorited"] else "☆ Favorite"
        ctk.CTkButton(btn_frame, text=fav_text, width=110,
                      fg_color="#FFD700" if card["is_favorited"] else "transparent",
                      text_color="black" if card["is_favorited"] else ("gray10", "gray90"),
                      border_width=1,
                      command=self._toggle_favorite).pack(side="left", padx=8)

        if not card["is_sold"]:
            ctk.CTkButton(btn_frame, text="Mark Sold", width=100,
                          fg_color="#34C759",
                          command=self._open_mark_sold).pack(side="right", padx=(8, 0))

        ctk.CTkButton(btn_frame, text="Delete", width=80,
                      fg_color="#FF3B30",
                      command=self._delete).pack(side="right")

    # ── actions ───────────────────────────────────────────────────────────────

    def _update_market_value(self):
        val_str = self._mkt_entry.get().strip()
        try:
            val = float(val_str) if val_str else None
        except ValueError:
            messagebox.showerror("Invalid", "Enter a valid number.", parent=self)
            return
        db.update_graded_card(self._card_id, market_value=val,
                              market_value_updated=date.today().isoformat())
        messagebox.showinfo("Saved", "Market value updated.", parent=self)
        self._close()

    def _toggle_favorite(self):
        new_val = 0 if self._card["is_favorited"] else 1
        db.update_graded_card(self._card_id, is_favorited=new_val)
        self._close()

    def _open_mark_sold(self):
        MarkSoldDialog(self, self._card_id, on_save=self._close)

    def _delete(self):
        if messagebox.askyesno("Delete Card",
                               f"Delete '{self._card['card_name']}'? This cannot be undone.",
                               parent=self):
            db.delete_graded_card(self._card_id)
            self._close()

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()


# ── mark as sold dialog ───────────────────────────────────────────────────────

class MarkSoldDialog(ctk.CTkToplevel):
    def __init__(self, parent, card_id: int, on_save=None):
        super().__init__(parent)
        self._card_id = card_id
        self._on_save = on_save
        card = db.get_graded_card(card_id)
        self.title(f"Mark as Sold — {card['card_name'] if card else ''}")
        self.geometry("400x360")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)
        self._card = card
        self._build()

    def _build(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=24, pady=20)
        f.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(f, text="Record Sale", font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, sticky="w", pady=(0, 16))

        ctk.CTkLabel(f, text="Sale Price (USD) *", font=ctk.CTkFont(size=12),
                     text_color="gray", anchor="w").grid(row=1, column=0, sticky="w")
        self._price = ctk.CTkEntry(f, height=34, placeholder_text="0.00")
        self._price.grid(row=2, column=0, sticky="ew", pady=(4, 14))

        ctk.CTkLabel(f, text="Sale Date *", font=ctk.CTkFont(size=12),
                     text_color="gray", anchor="w").grid(row=3, column=0, sticky="w")
        self._sale_date = _make_date_entry(f)
        self._sale_date.grid(row=4, column=0, sticky="w", pady=(4, 0))

        # profit preview
        self._profit_lbl = ctk.CTkLabel(f, text="", font=ctk.CTkFont(size=12))
        self._profit_lbl.grid(row=5, column=0, pady=(10, 0))
        self._price.bind("<KeyRelease>", self._update_profit_preview)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))
        ctk.CTkButton(btn_frame, text="Cancel", width=100,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Confirm Sale", width=120,
                      fg_color="#34C759",
                      command=self._save).pack(side="right")

    def _update_profit_preview(self, event=None):
        try:
            price = float(self._price.get())
            profit = price - self._card["acquisition_price"]
            color  = "green" if profit >= 0 else "red"
            self._profit_lbl.configure(
                text=f"Profit: {_fmt_usd(profit)}",
                text_color=color,
            )
        except (ValueError, TypeError):
            self._profit_lbl.configure(text="")

    def _save(self):
        price_str = self._price.get().strip()
        sale_date = self._sale_date.get().strip()
        if not price_str:
            messagebox.showerror("Required", "Sale price is required.", parent=self)
            return
        try:
            price_f = float(price_str)
        except ValueError:
            messagebox.showerror("Invalid", "Sale price must be a number.", parent=self)
            return
        db.mark_graded_sold(self._card_id, price_f, sale_date)
        if self._on_save:
            self._on_save()
        self.destroy()


# ── inventory picker dialog ────────────────────────────────────────────────────

class InventoryPickerDialog(ctk.CTkToplevel):
    """Pick a card from inventory or sold history to fill a trade row."""

    def __init__(self, parent, on_select):
        super().__init__(parent)
        self.title("Select Card")
        self.geometry("620x420")
        self.resizable(False, True)
        self.grab_set()
        self._on_select   = on_select
        self._cards_data: dict = {}
        self._build()

    def _build(self):
        # Toggle bar
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        top.grid_columnconfigure(0, weight=1)

        self._toggle_var = ctk.StringVar(value="Inventory")
        ctk.CTkSegmentedButton(
            top, values=["Inventory", "Sold"],
            variable=self._toggle_var,
            command=self._refresh_list,
            width=200, height=30,
        ).pack(side="left")
        ctk.CTkLabel(top, text="Double-click to select",
                     font=ctk.CTkFont(size=11), text_color="gray"
                     ).pack(side="right")

        # Treeview
        tv_frame = ctk.CTkFrame(self, fg_color="transparent")
        tv_frame.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        tv_frame.grid_rowconfigure(0, weight=1)
        tv_frame.grid_columnconfigure(0, weight=1)

        _apply_treeview_style()
        cols = ("company", "grade", "card_name", "set_name", "value")
        self.tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                 selectmode="browse")
        hdrs = {
            "company":   ("Co.",        80),
            "grade":     ("Grade",      70),
            "card_name": ("Card Name", 210),
            "set_name":  ("Set",       140),
            "value":     ("Value",     100),
        }
        for col, (text, width) in hdrs.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=40)

        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", self._pick)
        self.tree.bind("<Return>",   self._pick)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(8, 14))
        ctk.CTkButton(btn_frame, text="Cancel", width=90,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Select", width=90,
                      command=self._pick).pack(side="right")

        self._refresh_list()

    def _refresh_list(self, *_):
        for item in self.tree.get_children():
            self.tree.delete(item)
        sold = self._toggle_var.get() == "Sold"
        cards = db.get_graded_cards(sold=sold)
        self._cards_data = {}
        for card in cards:
            mv = card["market_value"] if card["market_value"] is not None else card["acquisition_price"]
            self.tree.insert("", "end", iid=str(card["id"]), values=(
                card["grading_company"],
                card["grade"],
                card["card_name"],
                card["set_name"],
                _fmt_usd(mv),
            ))
            self._cards_data[str(card["id"])] = (card["card_name"], mv)

    def _pick(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        card_name, mv = self._cards_data[sel[0]]
        self._on_select(card_name, mv)
        self.destroy()
