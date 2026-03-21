"""Sold history view — list of sold cards with profit/loss, click for detail."""

import customtkinter as ctk
from tkinter import ttk
from PIL import Image
import database as db
from inventory_view import CardDetailDialog, _apply_treeview_style

def _fmt_usd(val) -> str:
    if val is None:
        return "—"
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


class SoldView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()
        self.refresh()

    def _build(self):
        _apply_treeview_style()

        # header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Sold History",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

        # search
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 0))
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(bar, textvariable=self._search_var,
                     placeholder_text="Search by name or serial…",
                     width=300, height=32).pack(side="left")

        # treeview
        tv_frame = ctk.CTkFrame(self, fg_color="transparent")
        tv_frame.grid(row=2, column=0, sticky="nsew", padx=24, pady=12)
        tv_frame.grid_rowconfigure(0, weight=1)
        tv_frame.grid_columnconfigure(0, weight=1)

        cols = ("company", "grade", "serial", "card_name", "set_name",
                "cost", "sale_price", "profit", "margin", "sale_date")
        self.tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                 selectmode="browse")

        headings = {
            "company":    ("Co.",       70),
            "grade":      ("Grade",     65),
            "serial":     ("Serial",    100),
            "card_name":  ("Card Name", 210),
            "set_name":   ("Set",       130),
            "cost":       ("Cost",      90),
            "sale_price": ("Sold For",  90),
            "profit":     ("Profit",    100),
            "margin":     ("ROI %",     80),
            "sale_date":  ("Sale Date", 100),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text,
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, minwidth=40)

        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", self._on_row_double_click)
        self.tree.bind("<Return>",   self._on_row_double_click)

        self._sort_col = "sale_date"
        self._sort_rev = True

    def refresh(self):
        self._cards = db.get_graded_cards(sold=True)
        self._apply_filter()

    def _apply_filter(self):
        search = self._search_var.get().lower() if hasattr(self, "_search_var") else ""
        cards  = self._cards
        if search:
            cards = [
                c for c in cards
                if search in c["card_name"].lower()
                or search in c["serial_number"].lower()
            ]
        self._populate_tree(cards)

    def _populate_tree(self, cards):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for card in cards:
            profit  = None
            margin  = None
            if card["sale_price"] is not None:
                profit = card["sale_price"] - card["acquisition_price"]
                if card["acquisition_price"] > 0:
                    margin = (profit / card["acquisition_price"]) * 100

            self.tree.insert("", "end", iid=str(card["id"]), values=(
                card["grading_company"],
                card["grade"],
                card["serial_number"],
                card["card_name"],
                card["set_name"],
                _fmt_usd(card["acquisition_price"]),
                _fmt_usd(card["sale_price"]),
                _fmt_usd(profit),
                f"{margin:+.1f}%" if margin is not None else "—",
                card["sale_date"] or "—",
            ))

    def _sort_by(self, col: str):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False

        col_map = {
            "company":    "grading_company",
            "grade":      "grade",
            "serial":     "serial_number",
            "card_name":  "card_name",
            "set_name":   "set_name",
            "cost":       "acquisition_price",
            "sale_price": "sale_price",
            "sale_date":  "sale_date",
        }

        db_col = col_map.get(col)
        if db_col:
            def sort_key(c, dc=db_col):
                v = c[dc]
                if dc == "grade":
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        return 0
                if isinstance(v, str):
                    return v or ""
                return v or 0
            self._cards.sort(key=sort_key, reverse=self._sort_rev)
        self._apply_filter()

    def _on_row_double_click(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        card_id = int(sel[0])
        CardDetailDialog(self, card_id, on_close=self.refresh)
