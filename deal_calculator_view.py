"""Deal Calculator — compute cost and profit projections at any market %."""

import customtkinter as ctk
from tkinter import ttk

SELL_POINTS = [
    ("90% of MV",  90),
    ("100% of MV", 100),
    ("110% of MV", 110),
    ("125% of MV", 125),
    ("150% of MV", 150),
]

def _fmt_usd(val: float) -> str:
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"

def _apply_proj_style():
    style = ttk.Style()
    style.theme_use("clam")
    mode = ctk.get_appearance_mode()
    bg  = "#2b2b2b" if mode == "Dark" else "#f0f0f0"
    fg  = "#e0e0e0" if mode == "Dark" else "#1a1a1a"
    hdr = "#1e1e1e" if mode == "Dark" else "#d0d0d0"
    sel = "#3a7ebf"
    style.configure("Proj.Treeview",
        background=bg, foreground=fg, fieldbackground=bg, rowheight=30,
        borderwidth=0, font=("SF Pro Text", 12))
    style.configure("Proj.Treeview.Heading",
        background=hdr, foreground=fg, font=("SF Pro Text", 12, "bold"), relief="flat")
    style.map("Proj.Treeview",
        background=[("selected", sel)], foreground=[("selected", "white")])


class DealCalculatorView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def refresh(self):
        pass  # stateless — nothing to reload

    def _build(self):
        _apply_proj_style()

        # header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Deal Calculator",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

        # scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        scroll.grid_columnconfigure(0, weight=1)

        # ── input card ────────────────────────────────────────────────────────
        input_card = ctk.CTkFrame(scroll, corner_radius=12)
        input_card.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        input_card.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(input_card, text="Inputs",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, columnspan=3, sticky="w",
                            padx=16, pady=(14, 8))

        # card label (optional)
        ctk.CTkLabel(input_card, text="Card Reference (optional)",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=1, column=0, columnspan=3, sticky="w", padx=16)
        self._label_var = ctk.StringVar()
        ctk.CTkEntry(input_card, textvariable=self._label_var,
                     placeholder_text="Card name or notes",
                     height=32
                     ).grid(row=2, column=0, columnspan=3, sticky="ew",
                            padx=16, pady=(4, 12))

        # market value
        ctk.CTkLabel(input_card, text="Market Value ($)",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=3, column=0, sticky="w", padx=16)
        self._mkt_var = ctk.StringVar()
        self._mkt_var.trace_add("write", lambda *_: self._recalculate())
        ctk.CTkEntry(input_card, textvariable=self._mkt_var,
                     placeholder_text="0.00", height=36, width=160
                     ).grid(row=4, column=0, sticky="w", padx=16, pady=(4, 0))

        # buy pct
        ctk.CTkLabel(input_card, text="Your Buy Target (% of MV)",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=3, column=1, sticky="w", padx=8)
        self._pct_var = ctk.StringVar(value="70")
        self._pct_var.trace_add("write", lambda *_: self._recalculate())
        pct_frame = ctk.CTkFrame(input_card, fg_color="transparent")
        pct_frame.grid(row=4, column=1, sticky="w", padx=8, pady=(4, 0))
        ctk.CTkEntry(pct_frame, textvariable=self._pct_var,
                     width=70, height=36).pack(side="left")
        ctk.CTkLabel(pct_frame, text="% of MV", font=ctk.CTkFont(size=11),
                     text_color="gray").pack(side="left", padx=6)

        # quick pct buttons
        ctk.CTkLabel(input_card, text="Quick %",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=3, column=2, sticky="w", padx=8)
        qf = ctk.CTkFrame(input_card, fg_color="transparent")
        qf.grid(row=4, column=2, sticky="w", padx=8, pady=(4, 0))
        for pct in [60, 70, 80]:
            ctk.CTkButton(qf, text=f"{pct}%", width=48, height=30,
                          corner_radius=8,
                          command=lambda p=pct: self._pct_var.set(str(p))
                          ).pack(side="left", padx=2)

        # Your Cost display
        self._cost_lbl = ctk.CTkLabel(
            input_card, text="Your Cost: —",
            font=ctk.CTkFont(size=17, weight="bold"), text_color="#007AFF"
        )
        self._cost_lbl.grid(row=5, column=0, columnspan=3, sticky="w",
                            padx=16, pady=(14, 6))

        self._breakeven_lbl = ctk.CTkLabel(
            input_card, text="Break-even: —",
            font=ctk.CTkFont(size=12), text_color="gray"
        )
        self._breakeven_lbl.grid(row=6, column=0, columnspan=3, sticky="w",
                                 padx=16, pady=(0, 14))

        # ── profit projection table ───────────────────────────────────────────
        proj_card = ctk.CTkFrame(scroll, corner_radius=12)
        proj_card.grid(row=1, column=0, sticky="ew", padx=20, pady=(14, 0))
        proj_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(proj_card, text="Profit Projections",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))

        cols = ("scenario", "sale_price", "profit", "roi")
        self.proj_tree = ttk.Treeview(proj_card, columns=cols, show="headings",
                                      style="Proj.Treeview", height=len(SELL_POINTS))
        headings = {
            "scenario":   ("Scenario",   160),
            "sale_price": ("Sale Price", 130),
            "profit":     ("Profit",     130),
            "roi":        ("ROI %",      100),
        }
        for col, (text, width) in headings.items():
            self.proj_tree.heading(col, text=text)
            self.proj_tree.column(col, width=width, anchor="center")
        self.proj_tree.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))

        # ── breakeven section ─────────────────────────────────────────────────
        be_card = ctk.CTkFrame(scroll, corner_radius=12)
        be_card.grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 24))
        be_card.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(be_card, text="Break-even Analysis",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, columnspan=2, sticky="w",
                            padx=16, pady=(14, 8))

        self._be_price_lbl = ctk.CTkLabel(be_card, text="Break-even Price:  —",
                                          font=ctk.CTkFont(size=13))
        self._be_price_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))

        self._be_pct_lbl = ctk.CTkLabel(be_card, text="Break-even % of MV:  —",
                                        font=ctk.CTkFont(size=13))
        self._be_pct_lbl.grid(row=1, column=1, sticky="w", padx=16, pady=(0, 14))

    # ── calculation ───────────────────────────────────────────────────────────

    def _recalculate(self):
        try:
            mkt  = float(self._mkt_var.get())
            pct  = float(self._pct_var.get())
        except (ValueError, AttributeError):
            self._cost_lbl.configure(text="Your Cost: —")
            self._breakeven_lbl.configure(text="")
            self._clear_proj()
            return

        if mkt <= 0 or pct <= 0:
            return

        your_cost = mkt * pct / 100
        self._cost_lbl.configure(
            text=f"Your Cost:  {_fmt_usd(your_cost)}",
            text_color="#007AFF",
        )

        be_pct = (your_cost / mkt) * 100
        self._breakeven_lbl.configure(
            text=f"Break-even at {be_pct:.1f}% of market value"
        )
        self._be_price_lbl.configure(
            text=f"Break-even Price:  {_fmt_usd(your_cost)}"
        )
        self._be_pct_lbl.configure(
            text=f"Break-even % of MV:  {be_pct:.1f}%"
        )

        # projection table
        self._clear_proj()
        for label, sell_pct in SELL_POINTS:
            sale_price = mkt * sell_pct / 100
            profit     = sale_price - your_cost
            roi        = (profit / your_cost) * 100 if your_cost > 0 else 0
            sign_c     = "" if profit >= 0 else ""
            self.proj_tree.insert("", "end", values=(
                label,
                _fmt_usd(sale_price),
                _fmt_usd(profit),
                f"{roi:+.1f}%",
            ))

    def _clear_proj(self):
        for item in self.proj_tree.get_children():
            self.proj_tree.delete(item)
