"""Record Trade dialog — book a multi-card trade as a single action.

Flow:
  You Give  → select cards from existing inventory + optional cash paid
  You Receive → add new cards (name/grade/company/value) + optional cash received

On confirm:
  • Each "giving" card is marked sold at its specified value
  • Each "receiving" card is added to inventory with acquisition_type Trade /
    Cash & Trade, trade_details listing everything that was given up
"""

import customtkinter as ctk
from tkinter import messagebox
from datetime import date
import database as db

COMPANY_COLORS = db.COMPANY_COLORS
COMPANIES      = db.GRADING_COMPANIES


def _parse_float(s: str) -> float:
    try:
        return max(0.0, float(s.strip().lstrip("$").replace(",", "")))
    except ValueError:
        return 0.0


def _fmt(val: float) -> str:
    return f"${val:,.2f}"


# ── Main dialog ───────────────────────────────────────────────────────────────

class RecordTradeDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_complete=None):
        super().__init__(parent)
        self.title("Record Trade")
        self.geometry("940x680")
        self.minsize(760, 520)
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)

        self._on_complete    = on_complete
        self._giving:  list[dict] = []   # {"id", "card_name", "grading_company", "grade", "val_var"}
        self._receiving: list[dict] = [] # {"card_name", "grading_company", "grade", "serial_number",
                                         #  "card_number", "set_name", "market_value"}
        self._cash_paid_var     = ctk.StringVar(value="")
        self._cash_received_var = ctk.StringVar(value="")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # ── header ─────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="Record Trade",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(hdr, text=date.today().isoformat(),
                     font=ctk.CTkFont(size=12), text_color="gray").grid(
            row=0, column=1, sticky="w", padx=(12, 0))
        ctk.CTkButton(hdr, text="Confirm Trade", width=130, height=34,
                      corner_radius=8, command=self._confirm).grid(
            row=0, column=2, sticky="e")

        # ── two-column body ─────────────────────────────────────────────────
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=12)
        body.grid_columnconfigure((0, 1), weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._build_give_col(body)
        self._build_get_col(body)

        # ── summary bar ─────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, corner_radius=12)
        bar.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))
        bar.grid_columnconfigure((0, 1, 2), weight=1)

        self._give_sum_lbl = ctk.CTkLabel(bar, text="Giving: $0.00",
                                          font=ctk.CTkFont(size=14),
                                          text_color="#FF3B30")
        self._give_sum_lbl.grid(row=0, column=0, pady=12, padx=16, sticky="w")

        self._net_lbl = ctk.CTkLabel(bar, text="—",
                                     font=ctk.CTkFont(size=18, weight="bold"))
        self._net_lbl.grid(row=0, column=1, pady=12)

        self._get_sum_lbl = ctk.CTkLabel(bar, text="Receiving: $0.00",
                                         font=ctk.CTkFont(size=14),
                                         text_color="#34C759")
        self._get_sum_lbl.grid(row=0, column=2, pady=12, padx=16, sticky="e")

    def _build_give_col(self, body):
        col = ctk.CTkFrame(body, corner_radius=12)
        col.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(1, weight=1)

        # column header
        ch = ctk.CTkFrame(col, fg_color="transparent")
        ch.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        ch.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ch, text="You Give",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#FF3B30").grid(row=0, column=0, sticky="w")
        self._give_total_lbl = ctk.CTkLabel(ch, text="$0.00",
                                             font=ctk.CTkFont(size=13, weight="bold"),
                                             text_color="#FF3B30")
        self._give_total_lbl.grid(row=0, column=1, sticky="e")

        # card list
        self._give_scroll = ctk.CTkScrollableFrame(col, fg_color="transparent")
        self._give_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self._give_scroll.grid_columnconfigure(0, weight=1)

        # footer: cash paid + pick button
        ft = ctk.CTkFrame(col, fg_color="transparent")
        ft.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(ft, text="Cash Paid:", font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkEntry(ft, textvariable=self._cash_paid_var,
                     placeholder_text="0.00", width=80, height=28).pack(
            side="left", padx=(6, 0))
        self._cash_paid_var.trace_add("write", lambda *_: self._refresh_totals())

        ctk.CTkButton(ft, text="+ From Inventory", height=28, width=140,
                      fg_color="transparent", border_width=1,
                      text_color="#FF3B30",
                      command=self._pick_giving).pack(side="right")

    def _build_get_col(self, body):
        col = ctk.CTkFrame(body, corner_radius=12)
        col.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        col.grid_columnconfigure(0, weight=1)
        col.grid_rowconfigure(1, weight=1)

        # column header
        ch = ctk.CTkFrame(col, fg_color="transparent")
        ch.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        ch.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ch, text="You Receive",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color="#34C759").grid(row=0, column=0, sticky="w")
        self._get_total_lbl = ctk.CTkLabel(ch, text="$0.00",
                                            font=ctk.CTkFont(size=13, weight="bold"),
                                            text_color="#34C759")
        self._get_total_lbl.grid(row=0, column=1, sticky="e")

        # card list
        self._get_scroll = ctk.CTkScrollableFrame(col, fg_color="transparent")
        self._get_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 4))
        self._get_scroll.grid_columnconfigure(0, weight=1)

        # footer: cash received + add button
        ft = ctk.CTkFrame(col, fg_color="transparent")
        ft.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        ctk.CTkLabel(ft, text="Cash Received:", font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkEntry(ft, textvariable=self._cash_received_var,
                     placeholder_text="0.00", width=80, height=28).pack(
            side="left", padx=(6, 0))
        self._cash_received_var.trace_add("write", lambda *_: self._refresh_totals())

        ctk.CTkButton(ft, text="+ Add Card", height=28, width=110,
                      fg_color="transparent", border_width=1,
                      text_color="#34C759",
                      command=self._open_add_receiving).pack(side="right")

    # ── rendering ─────────────────────────────────────────────────────────────

    def _redraw_giving(self):
        for w in self._give_scroll.winfo_children():
            w.destroy()
        is_dark = ctk.get_appearance_mode() == "Dark"
        for i, card in enumerate(self._giving):
            self._draw_give_row(i, card, is_dark)
        self._refresh_totals()

    def _draw_give_row(self, idx: int, card: dict, is_dark: bool):
        row_bg = ("gray88", "gray20") if idx % 2 == 0 else ("gray82", "gray17")
        row = ctk.CTkFrame(self._give_scroll, corner_radius=6,
                           fg_color=row_bg, height=44)
        row.grid(row=idx, column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(1, weight=1)
        row.grid_propagate(False)

        # company badge
        company    = card["grading_company"] or ""
        grade      = card["grade"] or ""
        badge_col  = COMPANY_COLORS.get(company, "#555")
        ctk.CTkLabel(row,
                     text=f"{company} {grade}".strip(),
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="white", fg_color=badge_col,
                     corner_radius=4, width=64, height=20,
                     ).grid(row=0, column=0, padx=(8, 6), pady=12)

        # card name
        ctk.CTkLabel(row, text=card["card_name"],
                     font=ctk.CTkFont(size=12), anchor="w"
                     ).grid(row=0, column=1, sticky="ew")

        # editable value — trace updates totals only, no redraw
        ctk.CTkEntry(row, textvariable=card["val_var"],
                     width=80, height=26,
                     placeholder_text="$0.00"
                     ).grid(row=0, column=2, padx=(4, 4))

        # remove
        ctk.CTkButton(row, text="✕", width=26, height=26, corner_radius=13,
                      fg_color="transparent", text_color="gray",
                      font=ctk.CTkFont(size=11),
                      command=lambda i=idx: self._remove_giving(i)
                      ).grid(row=0, column=3, padx=(0, 4))

    def _redraw_receiving(self):
        for w in self._get_scroll.winfo_children():
            w.destroy()
        is_dark = ctk.get_appearance_mode() == "Dark"
        for i, card in enumerate(self._receiving):
            self._draw_get_row(i, card, is_dark)
        self._refresh_totals()

    def _draw_get_row(self, idx: int, card: dict, is_dark: bool):
        row_bg = ("gray88", "gray20") if idx % 2 == 0 else ("gray82", "gray17")
        row = ctk.CTkFrame(self._get_scroll, corner_radius=6,
                           fg_color=row_bg, height=44)
        row.grid(row=idx, column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(1, weight=1)
        row.grid_propagate(False)

        company   = card.get("grading_company", "")
        grade     = card.get("grade", "")
        badge_col = COMPANY_COLORS.get(company, "#555")
        ctk.CTkLabel(row,
                     text=f"{company} {grade}".strip(),
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="white", fg_color=badge_col,
                     corner_radius=4, width=64, height=20,
                     ).grid(row=0, column=0, padx=(8, 6), pady=12)

        ctk.CTkLabel(row, text=card.get("card_name", ""),
                     font=ctk.CTkFont(size=12), anchor="w"
                     ).grid(row=0, column=1, sticky="ew")

        ctk.CTkLabel(row, text=_fmt(card.get("market_value", 0)),
                     font=ctk.CTkFont(size=12), width=80, anchor="e"
                     ).grid(row=0, column=2, padx=(4, 4))

        ctk.CTkButton(row, text="✕", width=26, height=26, corner_radius=13,
                      fg_color="transparent", text_color="gray",
                      font=ctk.CTkFont(size=11),
                      command=lambda i=idx: self._remove_receiving(i)
                      ).grid(row=0, column=3, padx=(0, 4))

    # ── totals ────────────────────────────────────────────────────────────────

    def _give_total(self) -> float:
        cards = sum(_parse_float(c["val_var"].get()) for c in self._giving)
        cash  = _parse_float(self._cash_paid_var.get())
        return cards + cash

    def _get_total(self) -> float:
        cards = sum(c.get("market_value", 0) for c in self._receiving)
        cash  = _parse_float(self._cash_received_var.get())
        return cards + cash

    def _refresh_totals(self):
        give = self._give_total()
        get  = self._get_total()
        net  = get - give

        self._give_total_lbl.configure(text=_fmt(give))
        self._get_total_lbl.configure(text=_fmt(get))
        self._give_sum_lbl.configure(text=f"Giving: {_fmt(give)}")
        self._get_sum_lbl.configure(text=f"Receiving: {_fmt(get)}")

        if give <= 0 and get <= 0:
            self._net_lbl.configure(text="—", text_color=("gray20", "gray80"))
        elif abs(net) < 0.01:
            self._net_lbl.configure(text="Even", text_color="#34C759")
        elif net > 0:
            self._net_lbl.configure(text=f"+{_fmt(net)}", text_color="#34C759")
        else:
            self._net_lbl.configure(text=f"-{_fmt(abs(net))}", text_color="#FF3B30")

    # ── actions ───────────────────────────────────────────────────────────────

    def _pick_giving(self):
        # filter out cards already added
        already = {c["id"] for c in self._giving}
        MultiInventoryPickerDialog(self, already, on_select=self._add_giving_cards)

    def _add_giving_cards(self, cards: list[dict]):
        existing_ids = {c["id"] for c in self._giving}
        for card in cards:
            if card["id"] in existing_ids:
                continue
            val_var = ctk.StringVar(
                value=f"{card['market_value']:.2f}" if card["market_value"] else "0.00"
            )
            val_var.trace_add("write", lambda *_: self._refresh_totals())
            self._giving.append({
                "id":               card["id"],
                "card_name":        card["card_name"],
                "grading_company":  card["grading_company"],
                "grade":            card["grade"],
                "val_var":          val_var,
            })
        self._redraw_giving()

    def _remove_giving(self, idx: int):
        self._giving.pop(idx)
        self._redraw_giving()

    def _open_add_receiving(self):
        AddReceivingCardDialog(self, on_add=self._add_receiving_card)

    def _add_receiving_card(self, card: dict):
        self._receiving.append(card)
        self._redraw_receiving()

    def _remove_receiving(self, idx: int):
        self._receiving.pop(idx)
        self._redraw_receiving()

    # ── confirm ───────────────────────────────────────────────────────────────

    def _confirm(self):
        if not self._giving and not self._receiving:
            messagebox.showwarning("Empty Trade", "Add at least one card on each side.",
                                   parent=self)
            return
        if not self._giving:
            messagebox.showwarning("Missing Cards", "Add the cards you are giving up.",
                                   parent=self)
            return
        if not self._receiving:
            messagebox.showwarning("Missing Cards", "Add the cards you are receiving.",
                                   parent=self)
            return

        today         = date.today().isoformat()
        cash_paid     = _parse_float(self._cash_paid_var.get())
        cash_received = _parse_float(self._cash_received_var.get())

        # Build trade detail string (what was given up)
        give_parts = [
            f"{c['card_name']} ({c['grading_company']} {c['grade']}): "
            f"{_fmt(_parse_float(c['val_var'].get()))}"
            for c in self._giving
        ]
        if cash_paid > 0:
            give_parts.append(f"Cash: {_fmt(cash_paid)}")
        trade_details = " | ".join(give_parts)

        total_give_cards = sum(_parse_float(c["val_var"].get()) for c in self._giving)

        # 1. Mark giving cards as sold
        for card in self._giving:
            sale_price = _parse_float(card["val_var"].get())
            db.mark_graded_sold(card["id"], sale_price, today)

        # 2. Add receiving cards to inventory
        for card in self._receiving:
            acq_type  = "Cash & Trade" if cash_paid > 0 else "Trade"
            acq_price = card.get("market_value", 0.0)

            new_id = db.add_graded_card(
                serial_number      = card.get("serial_number", ""),
                grading_company    = card.get("grading_company", ""),
                grade              = card.get("grade", ""),
                card_name          = card.get("card_name", ""),
                card_number        = card.get("card_number", ""),
                set_name           = card.get("set_name", ""),
                photo_filename     = None,
                acquisition_type   = acq_type,
                acquisition_price  = acq_price,
                grading_fee        = 0.0,
                trade_value        = total_give_cards,
                trade_details      = trade_details,
                acquisition_date   = today,
                notes              = f"Received in trade on {today}",
            )
            # set market value immediately
            if acq_price > 0:
                db.update_graded_card(new_id,
                                      market_value=acq_price,
                                      market_value_updated=today)

        if self._on_complete:
            self._on_complete()

        self.destroy()


# ── Multi-select inventory picker ─────────────────────────────────────────────

class MultiInventoryPickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, excluded_ids: set, on_select=None):
        super().__init__(parent)
        self.title("Select Cards to Give Up")
        self.geometry("540x500")
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)

        self._excluded  = excluded_ids
        self._on_select = on_select
        self._check_vars: dict[int, ctk.BooleanVar] = {}
        self._all_cards = [c for c in db.get_graded_cards(sold=False)
                           if c["id"] not in excluded_ids]

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        # search
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        top.grid_columnconfigure(0, weight=1)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter())
        ctk.CTkEntry(top, textvariable=self._search_var,
                     placeholder_text="Search cards…", height=32
                     ).grid(row=0, column=0, sticky="ew")

        # list
        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self._list.grid_columnconfigure(0, weight=1)

        # footer
        ft = ctk.CTkFrame(self, fg_color="transparent")
        ft.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 14))
        ctk.CTkButton(ft, text="Cancel", width=90,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(ft, text="Add Selected", width=120,
                      command=self._confirm).pack(side="right")

        self._filter()

    def _filter(self):
        search = self._search_var.get().lower()
        filtered = [
            c for c in self._all_cards
            if not search
               or search in (c["card_name"] or "").lower()
               or search in (c["serial_number"] or "").lower()
               or search in (c["set_name"] or "").lower()
        ]
        for w in self._list.winfo_children():
            w.destroy()
        for i, card in enumerate(filtered):
            self._card_row(card, i)

    def _card_row(self, card, row: int):
        if card["id"] not in self._check_vars:
            self._check_vars[card["id"]] = ctk.BooleanVar(value=False)
        var = self._check_vars[card["id"]]

        company   = card["grading_company"] or ""
        badge_col = COMPANY_COLORS.get(company, "#555")
        mkt       = f"  MV: {_fmt(card['market_value'])}" if card["market_value"] else ""

        frame = ctk.CTkFrame(self._list, corner_radius=6,
                             fg_color=("gray88", "gray20") if row % 2 == 0
                             else ("gray82", "gray17"))
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.grid_columnconfigure(2, weight=1)

        ctk.CTkCheckBox(frame, text="", variable=var,
                        width=28, checkbox_width=18, checkbox_height=18
                        ).grid(row=0, column=0, padx=(8, 4), pady=10)

        ctk.CTkLabel(frame,
                     text=f"{company} {card['grade']}".strip(),
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="white", fg_color=badge_col,
                     corner_radius=4, width=64, height=20,
                     ).grid(row=0, column=1, padx=(0, 8))

        ctk.CTkLabel(frame,
                     text=card["card_name"],
                     font=ctk.CTkFont(size=12), anchor="w"
                     ).grid(row=0, column=2, sticky="ew")

        ctk.CTkLabel(frame,
                     text=mkt,
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="e"
                     ).grid(row=0, column=3, padx=(0, 10))

    def _confirm(self):
        selected = [
            c for c in self._all_cards
            if self._check_vars.get(c["id"], ctk.BooleanVar()).get()
        ]
        if not selected:
            messagebox.showwarning("Nothing Selected",
                                   "Check at least one card to add.", parent=self)
            return
        if self._on_select:
            self._on_select(selected)
        self.destroy()


# ── Add Receiving Card dialog ─────────────────────────────────────────────────

class AddReceivingCardDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_add=None):
        super().__init__(parent)
        self.title("Add Received Card")
        self.geometry("420x380")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)

        self._on_add = on_add
        self._build()

    def _build(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=20, pady=16)
        f.grid_columnconfigure(1, weight=1)

        def row(label, widget_fn, r):
            ctk.CTkLabel(f, text=label, anchor="e", width=110,
                         font=ctk.CTkFont(size=12)
                         ).grid(row=r, column=0, sticky="e", padx=(0, 10), pady=5)
            w = widget_fn(f)
            w.grid(row=r, column=1, sticky="ew", pady=5)
            return w

        # Card Name
        self._name_var = ctk.StringVar()
        row("Card Name *", lambda p: ctk.CTkEntry(p, textvariable=self._name_var,
                                                   height=30), 0)

        # Company
        ctk.CTkLabel(f, text="Company *", anchor="e", width=110,
                     font=ctk.CTkFont(size=12)
                     ).grid(row=1, column=0, sticky="e", padx=(0, 10), pady=5)
        self._company_var = ctk.StringVar(value=COMPANIES[0])
        ctk.CTkSegmentedButton(f, values=COMPANIES, variable=self._company_var
                               ).grid(row=1, column=1, sticky="ew", pady=5)

        # Grade
        self._grade_var = ctk.StringVar()
        row("Grade *", lambda p: ctk.CTkEntry(p, textvariable=self._grade_var,
                                               placeholder_text="e.g. 10", height=30), 2)

        # Market Value
        self._value_var = ctk.StringVar()
        row("Market Value *", lambda p: ctk.CTkEntry(p, textvariable=self._value_var,
                                                      placeholder_text="0.00", height=30), 3)

        # Serial Number (optional)
        self._serial_var = ctk.StringVar()
        row("Serial # (opt.)", lambda p: ctk.CTkEntry(p, textvariable=self._serial_var,
                                                       height=30), 4)

        # Card Number (optional)
        self._card_num_var = ctk.StringVar()
        row("Card # (opt.)", lambda p: ctk.CTkEntry(p, textvariable=self._card_num_var,
                                                     height=30), 5)

        # Set Name (optional)
        self._set_var = ctk.StringVar()
        row("Set (opt.)", lambda p: ctk.CTkEntry(p, textvariable=self._set_var,
                                                  height=30), 6)

        # buttons
        btn_f = ctk.CTkFrame(self, fg_color="transparent")
        btn_f.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_f, text="Cancel", width=90,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_f, text="Add Card", width=110,
                      command=self._add).pack(side="right")

    def _add(self):
        name  = self._name_var.get().strip()
        grade = self._grade_var.get().strip()
        value = _parse_float(self._value_var.get())

        if not name:
            messagebox.showwarning("Missing Field", "Card Name is required.", parent=self)
            return
        if not grade:
            messagebox.showwarning("Missing Field", "Grade is required.", parent=self)
            return

        if self._on_add:
            self._on_add({
                "card_name":        name,
                "grading_company":  self._company_var.get(),
                "grade":            grade,
                "market_value":     value,
                "serial_number":    self._serial_var.get().strip(),
                "card_number":      self._card_num_var.get().strip(),
                "set_name":         self._set_var.get().strip(),
            })
        self.destroy()
