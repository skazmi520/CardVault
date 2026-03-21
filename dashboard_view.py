"""Dashboard — home screen with stats, analytics, favorites, and deal tools."""

import customtkinter as ctk
from PIL import Image
import database as db

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _fmt_usd(val: float) -> str:
    if val is None:
        return "—"
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


class DashboardView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Dashboard",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)
        self._load_content()

    def refresh(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        self._load_content()

    def _load_content(self):
        stats   = db.get_dashboard_stats()
        summary = db.get_analytics_summary()

        self._build_stat_tiles(stats, summary)   # row 0
        self._build_company_breakdown()          # rows 2-3
        self._build_portfolio_chart()            # rows 4-5
        self._build_monthly_chart()              # rows 6-7
        self._build_performers(summary)          # rows 8-9
        self._build_top_holdings()               # rows 10-11
        self._build_inventory_aging()            # rows 12-13
        self._build_recent_sales()               # rows 14-15
        self._build_favorites()                  # rows 16-17
        self._build_deal_tools()                 # rows 18-19

    # ── section label helper ──────────────────────────────────────────────────

    def _section(self, text: str, scroll_row: int) -> ctk.CTkFrame:
        ctk.CTkLabel(self.scroll, text=text,
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=scroll_row, column=0, sticky="w",
                            padx=20, pady=(22, 6))
        frame = ctk.CTkFrame(self.scroll, corner_radius=12)
        frame.grid(row=scroll_row + 1, column=0, sticky="ew", padx=20)
        frame.grid_columnconfigure(0, weight=1)
        return frame

    # ── stat tiles (row 0) ────────────────────────────────────────────────────

    def _build_stat_tiles(self, stats: dict, summary: dict):
        frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        tiles = [
            ("Cards Owned",     str(stats["cards_owned"]),            None),
            ("Portfolio Value",  _fmt_usd(stats["total_market"]),     None),
            ("Unrealized P&L",  _fmt_usd(stats["unrealized_profit"]),
             "green" if stats["unrealized_profit"] >= 0 else "red"),
            ("Realized Profit", _fmt_usd(stats["realized_profit"]),
             "green" if stats["realized_profit"] >= 0 else "red"),
            ("Cards Sold",      str(stats["sold_count"]),             None),
            ("Win Rate",        f"{summary['win_rate']:.0f}%",
             "green" if summary["win_rate"] >= 50 else "red"),
            ("Avg. Profit",     _fmt_usd(summary["avg_profit"]),
             "green" if summary["avg_profit"] >= 0 else "red"),
            ("Total Revenue",   _fmt_usd(summary["total_revenue"]),   None),
        ]

        for i, (label, value, color) in enumerate(tiles):
            row, col = divmod(i, 4)
            self._stat_tile(frame, label, value, color, row, col)

    def _stat_tile(self, parent, label, value, color, row, col):
        tile = ctk.CTkFrame(parent, corner_radius=12)
        tile.grid(row=row, column=col, padx=5, pady=5, sticky="ew")
        tile.grid_columnconfigure(0, weight=1)
        vc = color if color else ("gray10", "gray90")
        ctk.CTkLabel(tile, text=value,
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=vc
                     ).grid(row=0, column=0, padx=12, pady=(12, 2), sticky="w")
        ctk.CTkLabel(tile, text=label,
                     font=ctk.CTkFont(size=11), text_color="gray"
                     ).grid(row=1, column=0, padx=12, pady=(0, 12), sticky="w")

    # ── company breakdown (row 1–2) ───────────────────────────────────────────

    def _build_company_breakdown(self):
        cards = db.get_graded_cards(sold=False)
        if not cards:
            return

        data: dict[str, dict] = {}
        for c in cards:
            co = c["grading_company"]
            if co not in data:
                data[co] = {"count": 0, "cost": 0.0, "market": 0.0}
            data[co]["count"]  += 1
            data[co]["cost"]   += c["acquisition_price"]
            data[co]["market"] += c["market_value"] if c["market_value"] else c["acquisition_price"]

        frame = self._section("Inventory by Company", scroll_row=2)
        frame.grid_columnconfigure(tuple(range(len(data))), weight=1)

        colors = db.COMPANY_COLORS
        for i, (co, d) in enumerate(sorted(data.items())):
            col_frame = ctk.CTkFrame(frame, fg_color="transparent")
            col_frame.grid(row=0, column=i, padx=12, pady=14, sticky="ew")

            color = colors.get(co, "#3a7ebf")
            ctk.CTkLabel(col_frame, text=co,
                         fg_color=color, corner_radius=6,
                         text_color="white",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         width=60, height=24
                         ).pack()
            ctk.CTkLabel(col_frame,
                         text=f"{d['count']} card{'s' if d['count'] != 1 else ''}",
                         font=ctk.CTkFont(size=13, weight="bold")
                         ).pack(pady=(8, 2))
            ctk.CTkLabel(col_frame, text=f"Value: {_fmt_usd(d['market'])}",
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).pack()
            ctk.CTkLabel(col_frame, text=f"Cost:  {_fmt_usd(d['cost'])}",
                         font=ctk.CTkFont(size=11), text_color="gray"
                         ).pack()

    # ── portfolio value over time (rows 4–5) ─────────────────────────────────

    def _build_portfolio_chart(self):
        snapshots = db.get_portfolio_snapshots()

        # Section header with snapshot button side by side
        hdr = ctk.CTkFrame(self.scroll, fg_color="transparent")
        hdr.grid(row=4, column=0, sticky="ew", padx=20, pady=(22, 6))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Portfolio Value Over Time",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text="Save Snapshot",
                      width=130, height=28, corner_radius=8,
                      command=self._save_snapshot
                      ).grid(row=0, column=1, sticky="e")

        frame = ctk.CTkFrame(self.scroll, corner_radius=12)
        frame.grid(row=5, column=0, sticky="ew", padx=20)
        frame.grid_columnconfigure(0, weight=1)

        if not snapshots:
            ctk.CTkLabel(frame,
                         text="No snapshots yet. After repricing your cards, click "
                              "\"Save Snapshot\" to record today's portfolio value.",
                         text_color="gray", wraplength=600
                         ).grid(padx=16, pady=20)
            return

        if not HAS_MPL:
            ctk.CTkLabel(frame,
                         text="pip install matplotlib  to enable charts",
                         text_color="gray").grid(padx=16, pady=14)
            return

        dates  = [s["date"]        for s in snapshots]
        values = [s["total_value"] for s in snapshots]

        is_dark = ctk.get_appearance_mode() == "Dark"
        bg      = "#2b2b2b" if is_dark else "#f5f5f5"
        fg      = "#e0e0e0" if is_dark else "#1a1a1a"
        grid_c  = "#3a3a3a" if is_dark else "#cccccc"
        line_c  = "#007AFF"
        fill_c  = "#007AFF22"

        fig = Figure(figsize=(8, 2.6), dpi=96, facecolor=bg)
        ax  = fig.add_subplot(111, facecolor=bg)

        x = range(len(dates))
        ax.plot(list(x), values, color=line_c, linewidth=2, marker="o",
                markersize=4, zorder=3)
        ax.fill_between(list(x), values, alpha=0.15, color=line_c, zorder=2)

        # x-axis: show dates, thin out if many points
        step = max(1, len(dates) // 12)
        ax.set_xticks([i for i in x if i % step == 0])
        ax.set_xticklabels([dates[i] for i in x if i % step == 0],
                           rotation=45, ha="right", fontsize=8, color=fg)
        ax.yaxis.set_tick_params(labelsize=8, colors=fg)
        ax.set_ylabel("Value (USD)", fontsize=8, color=fg)
        ax.grid(axis="y", color=grid_c, linestyle="--", linewidth=0.5, zorder=1)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.tight_layout(pad=1.2)

        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.draw()
        canvas.get_tk_widget().grid(padx=16, pady=(6, 14), sticky="ew")

    def _save_snapshot(self):
        snap = db.record_portfolio_snapshot()
        # Refresh just the portfolio chart area
        self.refresh()

    # ── monthly profit chart (rows 6–7) ───────────────────────────────────────

    def _build_monthly_chart(self):
        data = db.get_monthly_profits()
        frame = self._section("Monthly Profit / Loss", scroll_row=6)

        if not HAS_MPL:
            ctk.CTkLabel(frame,
                         text="pip install matplotlib  to enable charts",
                         text_color="gray"
                         ).grid(padx=16, pady=14)
            return

        if not data:
            ctk.CTkLabel(frame,
                         text="No sold cards yet — chart will appear here.",
                         text_color="gray"
                         ).grid(padx=16, pady=14)
            return

        months  = [d["month"] for d in data]
        profits = [d["profit"] for d in data]
        bar_colors = ["#34C759" if p >= 0 else "#FF3B30" for p in profits]

        is_dark = ctk.get_appearance_mode() == "Dark"
        bg      = "#2b2b2b" if is_dark else "#f5f5f5"
        fg      = "#e0e0e0" if is_dark else "#1a1a1a"
        grid_c  = "#3a3a3a" if is_dark else "#cccccc"

        fig = Figure(figsize=(8, 2.8), dpi=96, facecolor=bg)
        ax  = fig.add_subplot(111, facecolor=bg)
        ax.bar(range(len(months)), profits, color=bar_colors, zorder=3)
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels(months, rotation=45, ha="right", fontsize=8, color=fg)
        ax.yaxis.set_tick_params(labelsize=8, colors=fg)
        ax.axhline(0, color=fg, linewidth=0.6, zorder=2)
        ax.grid(axis="y", color=grid_c, linestyle="--", linewidth=0.5, zorder=1)
        for spine in ax.spines.values():
            spine.set_visible(False)
        fig.tight_layout(pad=1.2)

        canvas = FigureCanvasTkAgg(fig, master=frame)
        canvas.draw()
        canvas.get_tk_widget().grid(padx=16, pady=(6, 14), sticky="ew")

    # ── best / worst performers (rows 8–9) ────────────────────────────────────

    def _build_performers(self, summary: dict):
        best  = summary.get("best")
        worst = summary.get("worst")
        if not best:
            return
        if best and worst and best["id"] == worst["id"]:
            worst = None

        frame = self._section("Performers", scroll_row=8)
        frame.grid_columnconfigure(0, weight=1)

        row = 0
        if best:
            self._performer_row(frame, best, "Best", "#34C759", row); row += 1
        if worst:
            self._performer_row(frame, worst, "Worst", "#FF3B30", row); row += 1
        ctk.CTkFrame(frame, height=6, fg_color="transparent").grid(row=row, column=0)

    def _performer_row(self, parent, card, label, color, row):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=16, pady=5)
        f.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(f, text=f"  {label}  ",
                     fg_color=color, corner_radius=8,
                     text_color="white", font=ctk.CTkFont(size=11, weight="bold")
                     ).grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ctk.CTkLabel(f, text=card["card_name"],
                     font=ctk.CTkFont(size=13), anchor="w"
                     ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(f, text=f"{card['grading_company']} · Grade {card['grade']}",
                     font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                     ).grid(row=1, column=1, sticky="w")

        profit = (card["sale_price"] - card["acquisition_price"]) if card["sale_price"] else 0
        pc = "#34C759" if profit >= 0 else "#FF3B30"
        ctk.CTkLabel(f, text=_fmt_usd(profit),
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=pc
                     ).grid(row=0, column=2, rowspan=2, padx=(12, 0))

    # ── top 5 holdings (rows 10–11) ───────────────────────────────────────────

    def _build_top_holdings(self):
        cards = db.get_graded_cards(sold=False)
        if not cards:
            return

        top = sorted(
            cards,
            key=lambda c: c["market_value"] if c["market_value"] else c["acquisition_price"],
            reverse=True
        )[:5]

        frame = self._section("Top Holdings by Value", scroll_row=10)
        frame.grid_columnconfigure(0, weight=1)

        colors = db.COMPANY_COLORS
        for i, card in enumerate(top):
            f = ctk.CTkFrame(frame, fg_color="transparent")
            f.grid(row=i, column=0, sticky="ew", padx=16,
                   pady=(10 if i == 0 else 4, 4 if i < len(top) - 1 else 10))
            f.grid_columnconfigure(1, weight=1)

            # rank
            ctk.CTkLabel(f, text=f"#{i+1}",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="gray", width=28
                         ).grid(row=0, column=0, rowspan=2)

            # name + company badge
            name_f = ctk.CTkFrame(f, fg_color="transparent")
            name_f.grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(name_f, text=card["card_name"],
                         font=ctk.CTkFont(size=13), anchor="w"
                         ).pack(side="left")
            co_color = colors.get(card["grading_company"], "#3a7ebf")
            ctk.CTkLabel(name_f,
                         text=f"  {card['grading_company']} {card['grade']}  ",
                         fg_color=co_color, corner_radius=6,
                         text_color="white", font=ctk.CTkFont(size=10)
                         ).pack(side="left", padx=8)

            # values
            mkt = card["market_value"]
            val_str = _fmt_usd(mkt) if mkt else f"Cost: {_fmt_usd(card['acquisition_price'])}"
            ctk.CTkLabel(f, text=val_str,
                         font=ctk.CTkFont(size=13, weight="bold")
                         ).grid(row=0, column=2, rowspan=2, padx=(12, 0))

            if mkt:
                gain = mkt - card["acquisition_price"]
                gc = "green" if gain >= 0 else "red"
                ctk.CTkLabel(f, text=_fmt_usd(gain),
                             font=ctk.CTkFont(size=11), text_color=gc
                             ).grid(row=1, column=1, sticky="w")

    # ── inventory aging (rows 12–13) ──────────────────────────────────────────

    def _build_inventory_aging(self):
        aging = db.get_aging_cards(limit=8)
        if not aging:
            return

        frame = self._section("Longest Held Cards", scroll_row=12)
        frame.grid_columnconfigure(0, weight=1)
        colors = db.COMPANY_COLORS

        for i, entry in enumerate(aging):
            card  = entry["card"]
            days  = entry["days_held"]
            years = days // 365
            months = (days % 365) // 30
            rem_days = days % 30

            # Build a readable duration string
            parts = []
            if years:  parts.append(f"{years}y")
            if months: parts.append(f"{months}mo")
            if rem_days or not parts: parts.append(f"{rem_days}d")
            duration = " ".join(parts)

            # Color the age: green <90d, yellow 90-180d, orange 180-365d, red >365d
            if days < 90:
                age_color = "#34C759"
            elif days < 180:
                age_color = "#FFD60A"
            elif days < 365:
                age_color = "#FF9500"
            else:
                age_color = "#FF3B30"

            f = ctk.CTkFrame(frame, fg_color="transparent")
            f.grid(row=i, column=0, sticky="ew", padx=16,
                   pady=(10 if i == 0 else 3, 3 if i < len(aging) - 1 else 10))
            f.grid_columnconfigure(1, weight=1)

            # Duration badge
            ctk.CTkLabel(f, text=f" {duration} ",
                         fg_color=age_color, corner_radius=6,
                         text_color="white" if days >= 90 else "black",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         width=60
                         ).grid(row=0, column=0, rowspan=2, padx=(0, 12))

            # Card name + company badge
            name_f = ctk.CTkFrame(f, fg_color="transparent")
            name_f.grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(name_f, text=card["card_name"],
                         font=ctk.CTkFont(size=13), anchor="w"
                         ).pack(side="left")
            co_color = colors.get(card["grading_company"], "#3a7ebf")
            ctk.CTkLabel(name_f,
                         text=f"  {card['grading_company']} {card['grade']}  ",
                         fg_color=co_color, corner_radius=6,
                         text_color="white", font=ctk.CTkFont(size=10)
                         ).pack(side="left", padx=8)

            ctk.CTkLabel(f, text=f"Acquired {card['acquisition_date']}",
                         font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                         ).grid(row=1, column=1, sticky="w")

            # Cost + market value
            mkt = card["market_value"]
            val_str = _fmt_usd(mkt) if mkt else _fmt_usd(card["acquisition_price"])
            ctk.CTkLabel(f, text=val_str,
                         font=ctk.CTkFont(size=13, weight="bold")
                         ).grid(row=0, column=2, rowspan=2, padx=(12, 0))

    # ── recent sales (rows 14–15) ─────────────────────────────────────────────

    def _build_recent_sales(self):
        sold = db.get_graded_cards(sold=True)
        if not sold:
            return

        recent = sold[:5]

        frame = self._section("Recent Sales", scroll_row=14)
        frame.grid_columnconfigure(0, weight=1)

        for i, card in enumerate(recent):
            f = ctk.CTkFrame(frame, fg_color="transparent")
            f.grid(row=i, column=0, sticky="ew", padx=16,
                   pady=(10 if i == 0 else 4, 4 if i < len(recent) - 1 else 10))
            f.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(f, text=card["card_name"],
                         font=ctk.CTkFont(size=13), anchor="w"
                         ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(f,
                         text=f"{card['grading_company']} · Grade {card['grade']}  ·  {card['sale_date'] or ''}",
                         font=ctk.CTkFont(size=11), text_color="gray", anchor="w"
                         ).grid(row=1, column=0, sticky="w")

            if card["sale_price"] is not None:
                profit = card["sale_price"] - card["acquisition_price"]
                pc = "green" if profit >= 0 else "red"
                ctk.CTkLabel(f, text=_fmt_usd(profit),
                             font=ctk.CTkFont(size=13, weight="bold"),
                             text_color=pc
                             ).grid(row=0, column=1, rowspan=2, padx=(12, 0))

    # ── favorites (row 11–12) ─────────────────────────────────────────────────

    def _build_favorites(self):
        favs = [c for c in db.get_graded_cards(sold=False) if c["is_favorited"]]
        if not favs:
            return

        ctk.CTkLabel(self.scroll, text="Favorites",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=16, column=0, sticky="w", padx=20, pady=(22, 6))

        scroll_row = ctk.CTkScrollableFrame(
            self.scroll, orientation="horizontal", height=145, fg_color="transparent"
        )
        scroll_row.grid(row=17, column=0, sticky="ew", padx=20)

        self._thumb_refs = []
        for i, card in enumerate(favs):
            self._fav_tile(scroll_row, card, i)

    def _fav_tile(self, parent, card, col):
        tile = ctk.CTkFrame(parent, width=100, corner_radius=10)
        tile.grid(row=0, column=col, padx=6, pady=4)
        tile.grid_propagate(False)

        img_path = db.photo_path(card["photo_filename"])
        if img_path:
            try:
                img = Image.open(img_path).resize((80, 80))
                ctk_img = ctk.CTkImage(img, size=(80, 80))
                self._thumb_refs.append(ctk_img)
                ctk.CTkLabel(tile, image=ctk_img, text="").pack(pady=(8, 2))
            except Exception:
                self._placeholder_img(tile)
        else:
            self._placeholder_img(tile)

        ctk.CTkLabel(tile, text=card["card_name"],
                     font=ctk.CTkFont(size=10, weight="bold"),
                     wraplength=90).pack(padx=4, pady=(0, 2))

        if card["market_value"] is not None:
            gain = card["market_value"] - card["acquisition_price"]
            color = "green" if gain >= 0 else "red"
            ctk.CTkLabel(tile, text=_fmt_usd(gain),
                         font=ctk.CTkFont(size=10),
                         text_color=color).pack(pady=(0, 6))

    def _placeholder_img(self, parent):
        ph = ctk.CTkFrame(parent, width=80, height=80, corner_radius=8, fg_color="gray30")
        ph.pack(pady=(8, 2))
        ph.pack_propagate(False)
        ctk.CTkLabel(ph, text="🃏", font=ctk.CTkFont(size=28)
                     ).place(relx=0.5, rely=0.5, anchor="center")

    # ── deal tools (row 14–15) ────────────────────────────────────────────────

    def _build_deal_tools(self):
        ctk.CTkLabel(self.scroll, text="Deal Tools",
                     font=ctk.CTkFont(size=16, weight="bold")
                     ).grid(row=18, column=0, sticky="w", padx=20, pady=(22, 6))

        outer = ctk.CTkFrame(self.scroll, fg_color="transparent")
        outer.grid(row=19, column=0, sticky="ew", padx=20, pady=(0, 24))
        outer.grid_columnconfigure(0, weight=1)

        tools = [
            ("Deal Calculator",
             "Calculate cost and profit projections at any market %",
             "#FF9500", "deal_calc"),
            ("Trade Evaluator",
             "Compare multi-card trades to find fair deals",
             "#AF52DE", "trade_eval"),
        ]
        for i, (title, subtitle, color, key) in enumerate(tools):
            self._tool_row(outer, title, subtitle, color, key, row=i)

    def _tool_row(self, parent, title, subtitle, color, key, row):
        frame = ctk.CTkFrame(parent, corner_radius=12)
        frame.grid(row=row, column=0, sticky="ew", pady=4)
        frame.grid_columnconfigure(1, weight=1)

        icon_box = ctk.CTkFrame(frame, width=44, height=44,
                                corner_radius=10, fg_color=color)
        icon_box.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=12)
        icon_box.grid_propagate(False)
        ctk.CTkLabel(icon_box, text={"deal_calc": "%", "trade_eval": "⇄"}.get(key, "?"),
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color="white"
                     ).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(frame, text=title,
                     font=ctk.CTkFont(size=14, weight="bold"), anchor="w"
                     ).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(12, 0))
        ctk.CTkLabel(frame, text=subtitle,
                     font=ctk.CTkFont(size=11), text_color="gray",
                     anchor="w", wraplength=400
                     ).grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 12))
        ctk.CTkButton(frame, text="Open →",
                      width=80, height=30, corner_radius=8,
                      command=lambda k=key: self.app.show_view(k)
                      ).grid(row=0, column=2, rowspan=2, padx=12)
