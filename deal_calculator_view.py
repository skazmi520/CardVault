"""Deal Calculator — multi-card buy cost matrix and sell projections."""

import customtkinter as ctk

BUY_PCTS  = [70, 75, 80, 85, 90]
SELL_PCTS = [90, 100, 110, 125, 150]


def _fmt_usd(val: float) -> str:
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


def _parse_float(s: str):
    try:
        return float(s.replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


class DealCalculatorView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self._card_rows: list[dict] = []   # {name, mv, pct} StringVars
        self._global_pct = ctk.StringVar(value="80")
        self._global_pct.trace_add("write", lambda *_: self._recalculate())
        self._pct_btns: dict[int, ctk.CTkButton] = {}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def refresh(self):
        pass  # card state preserved in StringVars between navigations

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Deal Calculator",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        self._build_card_section()      # rows 0–1
        self._build_global_controls()   # row  2
        self._build_matrix_section()    # rows 3–4
        self._build_proj_section()      # rows 5–6

        self._add_card()                # seed one blank row

    # ── card list section ─────────────────────────────────────────────────────

    def _build_card_section(self):
        hdr = ctk.CTkFrame(self._scroll, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Cards",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="+ Add Card", width=100, height=28,
                      corner_radius=8, command=self._add_card
                      ).grid(row=0, column=1, sticky="e")

        self._cards_outer = ctk.CTkFrame(self._scroll, corner_radius=12)
        self._cards_outer.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 0))
        self._cards_outer.grid_columnconfigure(1, weight=1)

    def _rebuild_card_rows(self):
        for w in self._cards_outer.winfo_children():
            w.destroy()

        # column headers
        for col, text in enumerate(["", "Card Name", "Market Value", "Buy % Override", ""]):
            ctk.CTkLabel(self._cards_outer, text=text,
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).grid(row=0, column=col,
                                padx=(16 if col == 0 else 6, 6),
                                pady=(10, 4), sticky="w")
        self._cards_outer.grid_columnconfigure(1, weight=1)

        for i, row in enumerate(self._card_rows):
            r = i + 1

            ctk.CTkLabel(self._cards_outer, text=f"{i+1}.",
                         width=20, font=ctk.CTkFont(size=12), text_color="gray"
                         ).grid(row=r, column=0, padx=(16, 4), pady=4)

            ctk.CTkEntry(self._cards_outer, textvariable=row["name"],
                         placeholder_text="Card name", height=32
                         ).grid(row=r, column=1, sticky="ew", padx=6, pady=4)

            mv_f = ctk.CTkFrame(self._cards_outer, fg_color="transparent")
            mv_f.grid(row=r, column=2, padx=6, pady=4, sticky="w")
            ctk.CTkLabel(mv_f, text="$",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(side="left")
            ctk.CTkEntry(mv_f, textvariable=row["mv"],
                         placeholder_text="0.00", width=110, height=32).pack(side="left")

            pct_f = ctk.CTkFrame(self._cards_outer, fg_color="transparent")
            pct_f.grid(row=r, column=3, padx=6, pady=4, sticky="w")
            ctk.CTkEntry(pct_f, textvariable=row["pct"],
                         placeholder_text="Global", width=72, height=32).pack(side="left")
            ctk.CTkLabel(pct_f, text="%",
                         font=ctk.CTkFont(size=12), text_color="gray"
                         ).pack(side="left", padx=(4, 0))

            ctk.CTkButton(self._cards_outer, text="×", width=28, height=28,
                          fg_color="transparent",
                          text_color=("gray40", "gray60"),
                          hover_color=("gray80", "gray30"),
                          corner_radius=6,
                          command=lambda idx=i: self._remove_card(idx)
                          ).grid(row=r, column=4, padx=(4, 12), pady=4)

        ctk.CTkFrame(self._cards_outer, height=8, fg_color="transparent"
                     ).grid(row=len(self._card_rows) + 1, column=0)

    def _add_card(self):
        name_var = ctk.StringVar()
        mv_var   = ctk.StringVar()
        pct_var  = ctk.StringVar()
        for v in (name_var, mv_var, pct_var):
            v.trace_add("write", lambda *_: self._recalculate())
        self._card_rows.append({"name": name_var, "mv": mv_var, "pct": pct_var})
        self._rebuild_card_rows()
        self._recalculate()

    def _remove_card(self, idx: int):
        if len(self._card_rows) <= 1:
            return
        self._card_rows.pop(idx)
        self._rebuild_card_rows()
        self._recalculate()

    # ── global % controls ─────────────────────────────────────────────────────

    def _build_global_controls(self):
        ctrl = ctk.CTkFrame(self._scroll, corner_radius=12)
        ctrl.grid(row=2, column=0, sticky="ew", padx=20, pady=(12, 0))

        top = ctk.CTkFrame(ctrl, fg_color="transparent")
        top.grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(top, text="Global Buy %",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(top, text="   Applied to cards without an individual override",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(side="left")

        btn_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="w", padx=16, pady=(8, 12))

        for pct in BUY_PCTS:
            btn = ctk.CTkButton(btn_row, text=f"{pct}%", width=58, height=32,
                                corner_radius=8,
                                command=lambda p=pct: self._global_pct.set(str(p)))
            btn.pack(side="left", padx=3)
            self._pct_btns[pct] = btn

        ctk.CTkLabel(btn_row, text="  or  ",
                     font=ctk.CTkFont(size=11), text_color="gray").pack(side="left")
        ctk.CTkEntry(btn_row, textvariable=self._global_pct,
                     width=60, height=32, placeholder_text="80").pack(side="left")
        ctk.CTkLabel(btn_row, text="%",
                     font=ctk.CTkFont(size=11), text_color="gray"
                     ).pack(side="left", padx=(4, 0))

    def _update_pct_btn_styles(self):
        try:
            active = round(float(self._global_pct.get()))
        except (ValueError, AttributeError):
            active = -1
        for pct, btn in self._pct_btns.items():
            if pct == active:
                btn.configure(fg_color="#007AFF", text_color="white",
                              hover_color="#0066DD")
            else:
                btn.configure(fg_color=("gray80", "gray25"),
                              text_color=("gray10", "gray90"),
                              hover_color=("gray70", "gray35"))

    # ── result section frames ─────────────────────────────────────────────────

    def _build_matrix_section(self):
        ctk.CTkLabel(self._scroll, text="Buy Cost Matrix",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=3, column=0, sticky="w", padx=20, pady=(22, 6))
        self._matrix_frame = ctk.CTkFrame(self._scroll, corner_radius=12)
        self._matrix_frame.grid(row=4, column=0, sticky="ew", padx=20)
        self._matrix_frame.grid_columnconfigure(1, weight=1)

    def _build_proj_section(self):
        ctk.CTkLabel(self._scroll, text="Sell Projections",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=5, column=0, sticky="w", padx=20, pady=(22, 6))
        self._proj_frame = ctk.CTkFrame(self._scroll, corner_radius=12)
        self._proj_frame.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 24))
        self._proj_frame.grid_columnconfigure(0, weight=1)

    # ── recalculate ───────────────────────────────────────────────────────────

    def _recalculate(self):
        self._update_pct_btn_styles()

        try:
            global_pct = float(self._global_pct.get())
        except ValueError:
            global_pct = 80.0

        cards = []
        for i, row in enumerate(self._card_rows):
            mv = _parse_float(row["mv"].get())
            if mv is None or mv <= 0:
                continue
            pct_str = row["pct"].get().strip()
            override = _parse_float(pct_str) if pct_str else None
            eff_pct  = override if override is not None else global_pct
            cards.append({
                "name":    row["name"].get().strip() or f"Card {i + 1}",
                "mv":      mv,
                "pct":     override,        # None = global
                "eff_pct": eff_pct,
                "offer":   mv * eff_pct / 100,
            })

        self._render_matrix(cards, global_pct)
        self._render_sell_proj(cards)

    # ── buy cost matrix ───────────────────────────────────────────────────────

    def _render_matrix(self, cards: list[dict], global_pct: float):
        for w in self._matrix_frame.winfo_children():
            w.destroy()

        if not cards:
            ctk.CTkLabel(self._matrix_frame,
                         text="Add at least one card with a market value.",
                         text_color="gray"
                         ).grid(padx=16, pady=20)
            return

        BLUE      = "#007AFF"
        cell_font = ctk.CTkFont(size=12)
        bold_font = ctk.CTkFont(size=12, weight="bold")

        # header row
        col_defs = [("Card", 0, "w"), ("Market Value", 120, "center")] + \
                   [(f"@{p}%", 86, "center") for p in BUY_PCTS]
        for c, (text, width, anchor) in enumerate(col_defs):
            kw = {"width": width} if width else {}
            ctk.CTkLabel(self._matrix_frame, text=text,
                         font=ctk.CTkFont(size=11), text_color="gray",
                         anchor=anchor, **kw
                         ).grid(row=0, column=c,
                                padx=(16 if c == 0 else 4, 4),
                                pady=(12, 6), sticky="w")
        self._matrix_frame.grid_columnconfigure(0, weight=1)

        # data rows
        col_totals = {p: 0.0 for p in BUY_PCTS}
        total_mv   = 0.0

        for r, card in enumerate(cards):
            grid_row = r + 1
            total_mv += card["mv"]

            ctk.CTkLabel(self._matrix_frame, text=card["name"],
                         font=cell_font, anchor="w"
                         ).grid(row=grid_row, column=0,
                                padx=(16, 4), pady=2, sticky="w")

            ctk.CTkLabel(self._matrix_frame, text=_fmt_usd(card["mv"]),
                         font=cell_font, width=120, anchor="center"
                         ).grid(row=grid_row, column=1, padx=4, pady=2)

            for ci, pct in enumerate(BUY_PCTS):
                cost = card["mv"] * pct / 100
                col_totals[pct] += cost
                is_active = abs(pct - card["eff_pct"]) < 0.01
                ctk.CTkLabel(self._matrix_frame, text=_fmt_usd(cost),
                             font=bold_font if is_active else cell_font,
                             text_color="white" if is_active else ("gray10", "gray90"),
                             fg_color=BLUE if is_active else "transparent",
                             corner_radius=6, width=86, anchor="center"
                             ).grid(row=grid_row, column=ci + 2, padx=4, pady=2)

        # divider
        n_rows = len(cards)
        ctk.CTkFrame(self._matrix_frame, height=1,
                     fg_color=("gray70", "gray40")
                     ).grid(row=n_rows + 1, column=0,
                            columnspan=2 + len(BUY_PCTS),
                            sticky="ew", padx=16, pady=(6, 2))

        # totals row — highlight the active global % column
        trow = n_rows + 2
        ctk.CTkLabel(self._matrix_frame, text="TOTAL",
                     font=bold_font, anchor="w"
                     ).grid(row=trow, column=0, padx=(16, 4), pady=(4, 14), sticky="w")
        ctk.CTkLabel(self._matrix_frame, text=_fmt_usd(total_mv),
                     font=bold_font, width=120, anchor="center"
                     ).grid(row=trow, column=1, padx=4, pady=(4, 14))

        for ci, pct in enumerate(BUY_PCTS):
            is_active = abs(pct - global_pct) < 0.01
            ctk.CTkLabel(self._matrix_frame, text=_fmt_usd(col_totals[pct]),
                         font=bold_font,
                         text_color="white" if is_active else ("gray10", "gray90"),
                         fg_color=BLUE if is_active else "transparent",
                         corner_radius=6, width=86, anchor="center"
                         ).grid(row=trow, column=ci + 2, padx=4, pady=(4, 14))

    # ── sell projections ──────────────────────────────────────────────────────

    def _render_sell_proj(self, cards: list[dict]):
        for w in self._proj_frame.winfo_children():
            w.destroy()

        if not cards:
            ctk.CTkLabel(self._proj_frame,
                         text="Projections will appear once cards are entered.",
                         text_color="gray"
                         ).grid(padx=16, pady=20)
            return

        total_offer = sum(c["offer"] for c in cards)
        total_mv    = sum(c["mv"]    for c in cards)
        avg_disc    = 100 - (total_offer / total_mv * 100) if total_mv > 0 else 0

        # summary tiles
        tiles = ctk.CTkFrame(self._proj_frame, fg_color="transparent")
        tiles.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        tiles.grid_columnconfigure((0, 1, 2), weight=1)

        for col, (label, value, color) in enumerate([
            ("Total Market Value",       _fmt_usd(total_mv),    "#007AFF"),
            ("Your Offer",               _fmt_usd(total_offer), "#007AFF"),
            ("Avg Discount off MV",      f"{avg_disc:.1f}%",    "#34C759"),
        ]):
            tile = ctk.CTkFrame(tiles, corner_radius=10)
            tile.grid(row=0, column=col, padx=5, sticky="ew")
            ctk.CTkLabel(tile, text=value,
                         font=ctk.CTkFont(size=16, weight="bold"), text_color=color
                         ).grid(padx=14, pady=(10, 2), sticky="w")
            ctk.CTkLabel(tile, text=label,
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).grid(padx=14, pady=(0, 10), sticky="w")

        # projection table
        tbl = ctk.CTkFrame(self._proj_frame, fg_color="transparent")
        tbl.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        tbl.grid_columnconfigure((0, 1, 2, 3), weight=1)

        for col, text in enumerate(["Sell At", "Sale Price", "Profit", "ROI"]):
            ctk.CTkLabel(tbl, text=text,
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).grid(row=0, column=col, sticky="w", padx=8, pady=(0, 4))

        for row_idx, sell_pct in enumerate(SELL_PCTS):
            sale_price = total_mv * sell_pct / 100
            profit     = sale_price - total_offer
            roi        = (profit / total_offer * 100) if total_offer > 0 else 0
            pc         = "#34C759" if profit >= 0 else "#FF3B30"

            ctk.CTkLabel(tbl, text=f"{sell_pct}% of MV",
                         font=ctk.CTkFont(size=12)
                         ).grid(row=row_idx + 1, column=0, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(tbl, text=_fmt_usd(sale_price),
                         font=ctk.CTkFont(size=12)
                         ).grid(row=row_idx + 1, column=1, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(tbl, text=_fmt_usd(profit),
                         font=ctk.CTkFont(size=12, weight="bold"), text_color=pc
                         ).grid(row=row_idx + 1, column=2, sticky="w", padx=8, pady=3)
            ctk.CTkLabel(tbl, text=f"{roi:+.1f}%",
                         font=ctk.CTkFont(size=12), text_color=pc
                         ).grid(row=row_idx + 1, column=3, sticky="w", padx=8, pady=3)
