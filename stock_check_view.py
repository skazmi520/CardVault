"""Stock Check view — checklist of inventory cards for physical verification."""

import customtkinter as ctk
import database as db
import print_inventory

COMPANY_COLORS = db.COMPANY_COLORS


class StockCheckView(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Stock Check")
        self.geometry("720x620")
        self.resizable(True, True)
        self.minsize(520, 400)
        self.transient(parent.winfo_toplevel())
        self.lift()
        self.after(50, self.focus_force)

        self._filter_company = None
        self._search_text = ""
        self._check_vars: dict[int, ctk.BooleanVar] = {}  # card_id → BooleanVar

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build()
        self._load_cards()

    # ── layout ────────────────────────────────────────────────────────────────

    def _build(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(hdr, text="Stock Check",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w")

        self._progress_label = ctk.CTkLabel(
            hdr, text="0 / 0 checked",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        self._progress_label.grid(row=0, column=1, sticky="e", padx=(12, 8))

        ctk.CTkButton(hdr, text="Print List", width=90, height=30,
                      corner_radius=8,
                      command=print_inventory.open_print_view).grid(
            row=0, column=2, sticky="e", padx=(0, 8))

        ctk.CTkButton(hdr, text="Reset All", width=90, height=30,
                      corner_radius=8, fg_color="transparent",
                      border_width=1,
                      text_color=("gray20", "gray80"),
                      hover_color=("gray85", "gray25"),
                      command=self._reset_all).grid(row=0, column=3, sticky="e")

        # Filter bar
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(10, 0))

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(bar, textvariable=self._search_var,
                     placeholder_text="Search by name or card number…",
                     width=240, height=32).pack(side="left")

        chip_frame = ctk.CTkFrame(bar, fg_color="transparent")
        chip_frame.pack(side="left", padx=(12, 0))
        self._chip_btns: dict = {}

        for label, key in [("All", None)] + [(c, c) for c in db.GRADING_COMPANIES]:
            btn = ctk.CTkButton(
                chip_frame, text=label, width=54, height=28, corner_radius=14,
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._set_company_filter(k),
            )
            btn.pack(side="left", padx=3)
            self._chip_btns[key] = btn
        self._update_chips()

        # Scrollable list area
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=12)
        self._scroll.grid_columnconfigure(0, weight=1)

    # ── data ──────────────────────────────────────────────────────────────────

    def _load_cards(self):
        self._all_cards = db.get_graded_cards(sold=False)
        # Pre-create BooleanVars for any new card IDs
        for card in self._all_cards:
            if card["id"] not in self._check_vars:
                self._check_vars[card["id"]] = ctk.BooleanVar(value=False)
        self._apply_filter()

    def _apply_filter(self):
        search = self._search_var.get().strip().lower()
        self._search_text = search

        filtered = []
        for card in self._all_cards:
            if self._filter_company and card["grading_company"] != self._filter_company:
                continue
            if search:
                name = (card["card_name"] or "").lower()
                num  = (card["card_number"] or "").lower()
                if search not in name and search not in num:
                    continue
            filtered.append(card)

        # Sort: company then card_name
        filtered.sort(key=lambda c: (c["grading_company"] or "", (c["card_name"] or "").lower()))
        self._render_rows(filtered)

    def _render_rows(self, cards):
        # Destroy existing rows
        for widget in self._scroll.winfo_children():
            widget.destroy()

        is_dark = ctk.get_appearance_mode() == "Dark"

        for i, card in enumerate(cards):
            card_id = card["id"]
            var = self._check_vars[card_id]

            row_bg = ("gray88", "gray20") if i % 2 == 0 else ("gray82", "gray17")

            row = ctk.CTkFrame(self._scroll, corner_radius=6,
                               fg_color=row_bg, height=38)
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(2, weight=1)
            row.grid_propagate(False)

            cb = ctk.CTkCheckBox(
                row, text="", variable=var,
                width=28, height=28,
                checkbox_width=20, checkbox_height=20,
                command=self._update_progress,
            )
            cb.grid(row=0, column=0, padx=(10, 6), pady=5)

            # Company badge
            company = card["grading_company"] or ""
            grade   = card["grade"] or ""
            badge_color = COMPANY_COLORS.get(company, "#555555")
            badge_text = f"{company} {grade}".strip()
            ctk.CTkLabel(
                row, text=badge_text,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="white",
                fg_color=badge_color,
                corner_radius=4,
                width=72, height=22,
            ).grid(row=0, column=1, padx=(0, 10), pady=8)

            # Card name
            ctk.CTkLabel(
                row, text=card["card_name"] or "",
                font=ctk.CTkFont(size=13),
                anchor="w",
            ).grid(row=0, column=2, sticky="ew", padx=(0, 8))

            # Card number
            num_text = f"#{card['card_number']}" if card["card_number"] else ""
            ctk.CTkLabel(
                row, text=num_text,
                font=ctk.CTkFont(size=12),
                text_color="gray",
                anchor="e",
                width=80,
            ).grid(row=0, column=3, sticky="e", padx=(0, 14))

            # Clicking anywhere on the row toggles the checkbox
            for widget in (row,):
                widget.bind("<Button-1>", lambda e, v=var: self._toggle(v))

        self._update_progress()

    # ── interactions ──────────────────────────────────────────────────────────

    def _toggle(self, var: ctk.BooleanVar):
        var.set(not var.get())
        self._update_progress()

    def _update_progress(self):
        checked = sum(1 for v in self._check_vars.values() if v.get())
        total   = len([c for c in self._all_cards])
        self._progress_label.configure(text=f"{checked} / {total} checked")

    def _reset_all(self):
        for v in self._check_vars.values():
            v.set(False)
        self._update_progress()

    def _set_company_filter(self, key):
        self._filter_company = key
        self._update_chips()
        self._apply_filter()

    def _update_chips(self):
        for key, btn in self._chip_btns.items():
            active = (key == self._filter_company)
            btn.configure(
                fg_color=("#007AFF" if active else "transparent"),
                text_color=("white" if active else ("gray10", "gray90")),
                border_width=(0 if active else 1),
                border_color=("gray70", "gray40"),
            )
