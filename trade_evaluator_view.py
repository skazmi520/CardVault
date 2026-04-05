"""Trade Evaluator — compare multi-card trades to find fair deals.

Supports:
  - Manual entries (name + value)
  - Cards pulled from your inventory (by market value or cost basis)
  - Percentage or direct-dollar override per inventory card
"""

import customtkinter as ctk
from tkinter import messagebox
import database as db

def _fmt_usd(val: float) -> str:
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


class TradeItem:
    """One card on either side of the trade."""
    def __init__(self):
        self.name: str = ""
        self.manual_value: float = 0.0
        # if from inventory:
        self.inventory_id: int | None = None
        self.market_value: float | None = None
        self.cost_basis: float = 0.0
        self.use_cost: bool = False
        # override
        self.override_mode: str = "default"   # "default" | "percent" | "custom"
        self.override_pct: float = 100.0
        self.override_val: float = 0.0

    @property
    def value(self) -> float:
        if self.inventory_id is not None:
            base = self.cost_basis if self.use_cost else (self.market_value or 0.0)
            if self.override_mode == "percent":
                return base * self.override_pct / 100
            if self.override_mode == "custom":
                return self.override_val
            return base
        return self.manual_value


class TradeEvaluatorView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self._giving:  list[TradeItem] = [TradeItem()]
        self._getting: list[TradeItem] = [TradeItem()]
        self._show_cost = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def refresh(self):
        pass  # stateless

    def _build(self):
        # header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Trade Evaluator",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="Reset", width=80, height=30,
                      fg_color="transparent", border_width=1,
                      command=self._reset).pack(side="right", padx=(8, 0))
        self._cost_toggle_btn = ctk.CTkButton(
            hdr, text="Show Cost Basis", width=130, height=30,
            fg_color="transparent", border_width=1,
            command=self._toggle_cost,
        )
        self._cost_toggle_btn.pack(side="right")

        # main body (left: giving, right: getting)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=12)
        body.grid_columnconfigure((0, 1), weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._give_frame = self._build_side(body, "You Give", "#FF3B30", "give", col=0)
        self._get_frame  = self._build_side(body, "You Get",  "#34C759", "get",  col=1)

        # summary bar
        self._summary = ctk.CTkFrame(self, corner_radius=12)
        self._summary.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))
        self._summary.grid_columnconfigure((0, 1, 2), weight=1)
        self._build_summary_bar()
        self._refresh_summary()

    def _build_side(self, parent, title: str, color: str, side: str, col: int) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(parent, corner_radius=12)
        frame.grid(row=0, column=col, sticky="nsew",
                   padx=(0, 6) if col == 0 else (6, 0))
        frame.grid_columnconfigure(0, weight=1)

        # title
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=title,
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=color).grid(row=0, column=0, sticky="w")
        total_lbl = ctk.CTkLabel(header, text="$0.00",
                                 font=ctk.CTkFont(size=13, weight="bold"),
                                 text_color=color)
        total_lbl.grid(row=0, column=1, sticky="e")

        # rows scroll
        rows_frame = ctk.CTkScrollableFrame(frame, fg_color="transparent", height=300)
        rows_frame.grid(row=1, column=0, sticky="nsew", padx=8)
        rows_frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # add buttons
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=8)

        ctk.CTkButton(
            btn_frame, text="+ Manual Entry", height=28, width=120,
            fg_color="transparent", border_width=1, text_color=color,
            command=lambda s=side: self._add_manual(s),
        ).pack(side="left", padx=(0, 6))
        # "From Inventory" only makes sense on the giving side
        if side == "give":
            ctk.CTkButton(
                btn_frame, text="+ From Inventory", height=28, width=140,
                fg_color="transparent", border_width=1, text_color=color,
                command=lambda s=side: self._pick_from_inventory(s),
            ).pack(side="left")

        # store refs
        setattr(self, f"_{side}_rows_frame", rows_frame)
        setattr(self, f"_{side}_total_lbl",  total_lbl)

        self._redraw_side(side)
        return frame

    def _refresh_totals(self, side: str):
        """Update only the total label and summary bar — no widget recreation."""
        items: list[TradeItem] = self._giving if side == "give" else self._getting
        total_lbl: ctk.CTkLabel = getattr(self, f"_{side}_total_lbl")
        total = sum(it.value for it in items)
        total_lbl.configure(text=_fmt_usd(total))
        self._refresh_summary()

    def _redraw_side(self, side: str):
        items: list[TradeItem] = self._giving if side == "give" else self._getting
        rows_frame: ctk.CTkScrollableFrame = getattr(self, f"_{side}_rows_frame")
        total_lbl:  ctk.CTkLabel           = getattr(self, f"_{side}_total_lbl")

        for w in rows_frame.winfo_children():
            w.destroy()

        for i, item in enumerate(items):
            self._draw_item_row(rows_frame, item, side, i)

        total = sum(it.value for it in items)
        total_lbl.configure(text=_fmt_usd(total))
        self._refresh_summary()

    def _draw_item_row(self, parent, item: TradeItem, side: str, idx: int):
        row_frame = ctk.CTkFrame(parent, corner_radius=8, fg_color=("gray90", "gray20"))
        row_frame.grid(row=idx, column=0, sticky="ew", pady=3)
        row_frame.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(row_frame, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        top.grid_columnconfigure(0, weight=1)

        if item.inventory_id is not None:
            # inventory card row
            ctk.CTkLabel(top, text=item.name,
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="w"
                         ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(top, text=_fmt_usd(item.value),
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="e"
                         ).grid(row=0, column=1, sticky="e")

            info_frame = ctk.CTkFrame(row_frame, fg_color="transparent")
            info_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

            mode_text = {
                "default": "Market Value" if not self._show_cost else "Cost Basis",
                "percent": f"{item.override_pct:.0f}% override",
                "custom":  "Custom value",
            }.get(item.override_mode, "")
            ctk.CTkLabel(info_frame, text=f"📁 Inventory  ·  {mode_text}",
                         font=ctk.CTkFont(size=10), text_color="gray"
                         ).pack(side="left")

            ctk.CTkButton(info_frame, text="Adjust", width=60, height=22,
                          font=ctk.CTkFont(size=10),
                          fg_color="transparent", border_width=1,
                          command=lambda it=item, s=side: self._open_override(it, s)
                          ).pack(side="right")
        else:
            # manual row
            name_var = ctk.StringVar(value=item.name)
            val_var  = ctk.StringVar(value=str(item.manual_value) if item.manual_value else "")

            def on_name_change(v, it=item):
                it.name = v  # name doesn't affect totals — no redraw needed

            def on_val_change(v, it=item, s=side):
                try:
                    it.manual_value = float(v)
                except ValueError:
                    it.manual_value = 0.0
                self._refresh_totals(s)  # update totals only, don't recreate rows

            name_var.trace_add("write", lambda *_, v=name_var, it=item:
                               on_name_change(v.get(), it))
            val_var.trace_add("write", lambda *_, v=val_var, it=item, s=side:
                              on_val_change(v.get(), it, s))

            ctk.CTkEntry(top, textvariable=name_var,
                         placeholder_text="Card name",
                         height=28
                         ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            ctk.CTkEntry(top, textvariable=val_var,
                         placeholder_text="$0.00", width=90, height=28
                         ).grid(row=0, column=1, sticky="e")

        # remove button
        del_btn = ctk.CTkButton(
            row_frame, text="✕", width=26, height=26, corner_radius=13,
            fg_color="transparent", text_color="gray",
            font=ctk.CTkFont(size=12),
            command=lambda s=side, i=idx: self._remove_item(s, i),
        )
        del_btn.grid(row=0, column=1, padx=(0, 4))

    def _add_manual(self, side: str):
        items: list[TradeItem] = self._giving if side == "give" else self._getting
        items.append(TradeItem())
        self._redraw_side(side)

    def _remove_item(self, side: str, idx: int):
        items: list[TradeItem] = self._giving if side == "give" else self._getting
        if len(items) > 1:
            items.pop(idx)
        else:
            items[0] = TradeItem()
        self._redraw_side(side)

    def _pick_from_inventory(self, side: str):
        InventoryPickerDialog(self, side, on_select=self._add_inventory_item)

    def _add_inventory_item(self, side: str, card):
        items: list[TradeItem] = self._giving if side == "give" else self._getting
        item = TradeItem()
        item.inventory_id = card["id"]
        item.name         = card["card_name"]
        item.market_value = card["market_value"]
        item.cost_basis   = card["acquisition_price"]
        item.use_cost     = self._show_cost
        # replace last empty manual item
        if items and items[-1].inventory_id is None and not items[-1].name:
            items[-1] = item
        else:
            items.append(item)
        self._redraw_side(side)

    def _open_override(self, item: TradeItem, side: str):
        OverrideDialog(self, item, self._show_cost,
                       on_apply=lambda: self._redraw_side(side))

    def _toggle_cost(self):
        self._show_cost = not self._show_cost
        label = "Show Market Value" if self._show_cost else "Show Cost Basis"
        self._cost_toggle_btn.configure(text=label)
        for item in self._giving + self._getting:
            if item.inventory_id is not None:
                item.use_cost = self._show_cost
        self._redraw_side("give")
        self._redraw_side("get")

    def _reset(self):
        self._giving  = [TradeItem()]
        self._getting = [TradeItem()]
        self._redraw_side("give")
        self._redraw_side("get")

    # ── summary bar ───────────────────────────────────────────────────────────

    def _build_summary_bar(self):
        self._give_total_lbl2 = ctk.CTkLabel(
            self._summary, text="Giving: $0.00",
            font=ctk.CTkFont(size=14), text_color="#FF3B30"
        )
        self._give_total_lbl2.grid(row=0, column=0, pady=12, padx=16, sticky="w")

        self._verdict_lbl = ctk.CTkLabel(
            self._summary, text="—",
            font=ctk.CTkFont(size=22, weight="bold"),
        )
        self._verdict_lbl.grid(row=0, column=1, pady=12)

        self._get_total_lbl2 = ctk.CTkLabel(
            self._summary, text="Getting: $0.00",
            font=ctk.CTkFont(size=14), text_color="#34C759"
        )
        self._get_total_lbl2.grid(row=0, column=2, pady=12, padx=16, sticky="e")

        self._desc_lbl = ctk.CTkLabel(
            self._summary, text="",
            font=ctk.CTkFont(size=11), text_color="gray"
        )
        self._desc_lbl.grid(row=1, column=0, columnspan=3, pady=(0, 10))

    def _refresh_summary(self):
        if not hasattr(self, "_give_total_lbl2"):
            return

        give_total = sum(it.value for it in self._giving)
        get_total  = sum(it.value for it in self._getting)
        net        = get_total - give_total

        self._give_total_lbl2.configure(text=f"Giving: {_fmt_usd(give_total)}")
        self._get_total_lbl2.configure(text=f"Getting: {_fmt_usd(get_total)}")

        if give_total <= 0 and get_total <= 0:
            self._verdict_lbl.configure(text="—", text_color=("gray10", "gray90"))
            self._desc_lbl.configure(text="")
            return

        if give_total > 0:
            pct = get_total / give_total * 100
        else:
            pct = 0

        if abs(pct - 100) <= 5:
            verdict = "FAIR"
            color   = "#34C759"
            desc    = "Deal is within ±5% — fair trade"
        elif pct < 90:
            verdict = f"{pct:.0f}%"
            color   = "#FF9500"
            desc    = f"You give away {_fmt_usd(-net)} more — unfavorable"
        else:
            verdict = f"{pct:.0f}%"
            color   = "#007AFF"
            desc    = f"You receive {_fmt_usd(net)} more ({pct:.0f}% return)"

        self._verdict_lbl.configure(text=verdict, text_color=color)
        self._desc_lbl.configure(text=desc, text_color=color)


# ── inventory picker dialog ───────────────────────────────────────────────────

class InventoryPickerDialog(ctk.CTkToplevel):
    def __init__(self, parent, side: str, on_select=None):
        super().__init__(parent)
        self.title("Select Card from Inventory")
        self.geometry("520x460")
        self.grab_set()
        self._side      = side
        self._on_select = on_select
        self._cards     = db.get_graded_cards(sold=False)
        self._build()

    def _build(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=16, pady=16)
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=1)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter())
        ctk.CTkEntry(f, textvariable=self._search_var,
                     placeholder_text="Search cards…", height=32
                     ).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._listbox_frame = ctk.CTkScrollableFrame(f, fg_color="transparent")
        self._listbox_frame.grid(row=1, column=0, sticky="nsew")
        self._listbox_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(f, text="Cancel", fg_color="transparent", border_width=1,
                      command=self.destroy
                      ).grid(row=2, column=0, sticky="e", pady=(8, 0))

        self._filter()

    def _filter(self):
        search = self._search_var.get().lower()
        cards  = self._cards
        if search:
            cards = [
                c for c in cards
                if search in c["card_name"].lower()
                or search in (c["serial_number"] or "").lower()
                or search in (c["set_name"] or "").lower()
            ]
        for w in self._listbox_frame.winfo_children():
            w.destroy()
        for i, card in enumerate(cards):
            self._card_row(self._listbox_frame, card, i)

    def _card_row(self, parent, card, row: int):
        frame = ctk.CTkFrame(parent, corner_radius=8, fg_color=("gray90", "gray20"),
                             cursor="hand2")
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.grid_columnconfigure(0, weight=1)
        frame.bind("<Button-1>", lambda e, c=card: self._select(c))

        ctk.CTkLabel(frame, text=card["card_name"],
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
                     ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 2))

        mkt_str = f"  Market: ${card['market_value']:.2f}" if card["market_value"] else "  No market value"
        detail  = (f"{card['grading_company']} · Grade {card['grade']}"
                   f"  ·  Cost: ${card['acquisition_price']:.2f}{mkt_str}")
        ctk.CTkLabel(frame, text=detail,
                     font=ctk.CTkFont(size=10), text_color="gray", anchor="w"
                     ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))

        ctk.CTkButton(frame, text="Select →", width=80, height=26,
                      font=ctk.CTkFont(size=11),
                      command=lambda c=card: self._select(c)
                      ).grid(row=0, column=1, rowspan=2, padx=10)

    def _select(self, card):
        if self._on_select:
            self._on_select(self._side, card)
        self.destroy()


# ── override dialog ───────────────────────────────────────────────────────────

class OverrideDialog(ctk.CTkToplevel):
    def __init__(self, parent, item: TradeItem, show_cost: bool, on_apply=None):
        super().__init__(parent)
        self.title("Adjust Card Value")
        self.geometry("360x320")
        self.resizable(False, False)
        self.grab_set()
        self._item      = item
        self._show_cost = show_cost
        self._on_apply  = on_apply
        self._build()

    def _build(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=20, pady=16)
        f.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(f, text="Value Override",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        base_label = "Cost Basis" if self._show_cost else "Market Value"
        base_val   = self._item.cost_basis if self._show_cost else (self._item.market_value or 0)
        ctk.CTkLabel(f, text=f"{base_label}: {_fmt_usd(base_val)}",
                     font=ctk.CTkFont(size=12), text_color="gray"
                     ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        # mode selection
        self._mode_var = ctk.StringVar(value=self._item.override_mode)
        modes = [("Use Default", "default"), ("% of Value", "percent"), ("Custom $", "custom")]
        for i, (label, mode) in enumerate(modes):
            ctk.CTkRadioButton(f, text=label, variable=self._mode_var, value=mode,
                               command=self._on_mode_change
                               ).grid(row=2 + i, column=0, sticky="w", pady=3)

        # pct entry
        self._pct_frame = ctk.CTkFrame(f, fg_color="transparent")
        self._pct_frame.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(self._pct_frame, text="Percentage:").pack(side="left")
        self._pct_entry = ctk.CTkEntry(self._pct_frame, width=80, height=28)
        self._pct_entry.insert(0, str(self._item.override_pct))
        self._pct_entry.pack(side="left", padx=6)
        ctk.CTkLabel(self._pct_frame, text="%").pack(side="left")

        # quick pct buttons
        qf = ctk.CTkFrame(f, fg_color="transparent")
        qf.grid(row=6, column=0, sticky="w", pady=4)
        for pct in [70, 80, 90, 100, 110]:
            ctk.CTkButton(qf, text=f"{pct}%", width=44, height=26,
                          corner_radius=6,
                          command=lambda p=pct: self._pct_entry.delete(0, "end") or
                                                self._pct_entry.insert(0, str(p))
                          ).pack(side="left", padx=2)

        # custom $ entry
        self._custom_frame = ctk.CTkFrame(f, fg_color="transparent")
        self._custom_frame.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(self._custom_frame, text="Amount: $").pack(side="left")
        self._custom_entry = ctk.CTkEntry(self._custom_frame, width=100, height=28)
        self._custom_entry.insert(0, str(self._item.override_val) if self._item.override_val else "")
        self._custom_entry.pack(side="left", padx=6)

        self._on_mode_change()

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Cancel", width=90,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Apply", width=90,
                      command=self._apply).pack(side="right")

    def _on_mode_change(self):
        mode = self._mode_var.get()
        # show/hide relevant inputs
        if mode == "percent":
            self._pct_frame.grid()
            self._custom_frame.grid_remove()
        elif mode == "custom":
            self._pct_frame.grid_remove()
            self._custom_frame.grid()
        else:
            self._pct_frame.grid_remove()
            self._custom_frame.grid_remove()

    def _apply(self):
        mode = self._mode_var.get()
        self._item.override_mode = mode
        if mode == "percent":
            try:
                self._item.override_pct = float(self._pct_entry.get())
            except ValueError:
                self._item.override_pct = 100.0
        elif mode == "custom":
            try:
                self._item.override_val = float(self._custom_entry.get())
            except ValueError:
                self._item.override_val = 0.0
        if self._on_apply:
            self._on_apply()
        self.destroy()
