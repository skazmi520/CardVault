"""
CardVault Mac — Entry point and main application window.
Run:  python3 main.py
"""

import customtkinter as ctk
from database import init_db
from pathlib import Path
from PIL import Image, ImageTk

# ── appearance ────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("System")   # follows macOS dark/light mode
ctk.set_default_color_theme("blue")

NAV_ITEMS = [
    ("Dashboard",        "dashboard"),
    ("Inventory",        "inventory"),
    ("Ungraded",         "ungraded"),
    ("Sold",             "sold"),
    ("Deal Calculator",  "deal_calc"),
    ("Trade Evaluator",  "trade_eval"),
]

class CardVaultApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        init_db()

        self.title("CardVault")
        self.geometry("1100x720")
        self.minsize(900, 600)

        # Window icon (shows in Dock while running)
        icon_path = Path(__file__).parent / "icon.png"
        if icon_path.exists():
            try:
                pil_img = Image.open(icon_path).resize((64, 64), Image.LANCZOS)
                self._icon_img = ImageTk.PhotoImage(pil_img)
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass

        # ── layout: sidebar | content ─────────────────────────────────────────
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_content_area()

        # load views lazily
        self._views: dict[str, ctk.CTkFrame] = {}
        self._current_key: str | None = None

        self.show_view("dashboard")

    # ── sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=190, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(len(NAV_ITEMS) + 2, weight=1)

        logo = ctk.CTkLabel(
            self.sidebar,
            text="CardVault",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        logo.grid(row=0, column=0, padx=20, pady=(20, 6))

        subtitle = ctk.CTkLabel(
            self.sidebar,
            text="Pokemon Card Tracker",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        subtitle.grid(row=1, column=0, padx=20, pady=(0, 16))

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        for i, (label, key) in enumerate(NAV_ITEMS):
            btn = ctk.CTkButton(
                self.sidebar,
                text=label,
                anchor="w",
                height=36,
                corner_radius=8,
                fg_color="transparent",
                text_color=("gray10", "gray90"),
                hover_color=("gray80", "gray30"),
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self.show_view(k),
            )
            btn.grid(row=i + 2, column=0, padx=12, pady=3, sticky="ew")
            self._nav_buttons[key] = btn

    def _set_active_nav(self, key: str):
        for k, btn in self._nav_buttons.items():
            if k == key:
                btn.configure(
                    fg_color=("gray75", "gray25"),
                    font=ctk.CTkFont(size=13, weight="bold"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    font=ctk.CTkFont(size=13),
                )

    # ── content area ──────────────────────────────────────────────────────────

    def _build_content_area(self):
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

    def show_view(self, key: str):
        if self._current_key == key:
            # refresh in-place
            if key in self._views:
                self._views[key].refresh()
            return

        # hide current
        if self._current_key and self._current_key in self._views:
            self._views[self._current_key].grid_remove()

        # lazy-load
        if key not in self._views:
            view = self._load_view(key)
            view.grid(row=0, column=0, sticky="nsew")
            self._views[key] = view
        else:
            self._views[key].grid()
            self._views[key].refresh()

        self._current_key = key
        self._set_active_nav(key)

    def _load_view(self, key: str) -> ctk.CTkFrame:
        if key == "dashboard":
            from dashboard_view import DashboardView
            return DashboardView(self.content, app=self)
        elif key == "inventory":
            from inventory_view import InventoryView
            return InventoryView(self.content, app=self)
        elif key == "ungraded":
            from ungraded_view import UngradedView
            return UngradedView(self.content, app=self)
        elif key == "sold":
            from sold_view import SoldView
            return SoldView(self.content, app=self)
        elif key == "deal_calc":
            from deal_calculator_view import DealCalculatorView
            return DealCalculatorView(self.content, app=self)
        elif key == "trade_eval":
            from trade_evaluator_view import TradeEvaluatorView
            return TradeEvaluatorView(self.content, app=self)
        else:
            raise ValueError(f"Unknown view key: {key}")


if __name__ == "__main__":
    app = CardVaultApp()
    app.mainloop()
