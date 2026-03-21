"""Inventory view — graded card list, add dialog, detail/edit dialog, mark-sold dialog."""

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
from PIL import Image
from datetime import date
import database as db

COMPANY_COLORS = db.COMPANY_COLORS

def _fmt_usd(val) -> str:
    if val is None:
        return "—"
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"

def _date_str() -> str:
    return date.today().isoformat()

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
        self.geometry("520x620")
        self.resizable(False, False)
        self.grab_set()
        self._on_save = on_save
        self._photo_path: str | None = None
        self._build()

    def _build(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=16)
        scroll.grid_columnconfigure((0, 1), weight=1)

        r = 0

        def lbl(text, row, col, span=1):
            ctk.CTkLabel(scroll, text=text,
                         font=ctk.CTkFont(size=12), anchor="w"
                         ).grid(row=row, column=col, columnspan=span,
                                sticky="w", pady=(8, 0))

        def entry(row, col, span=1, **kw):
            e = ctk.CTkEntry(scroll, height=32, **kw)
            e.grid(row=row, column=col, columnspan=span, sticky="ew",
                   padx=(0, 6) if col == 0 and span == 1 else (6, 0) if col == 1 else 0,
                   pady=(2, 0))
            return e

        # Card Identity
        lbl("Card Name *", r, 0, 2); r += 1
        self._name = entry(r, 0, 2); r += 1

        lbl("Card Number", r, 0); lbl("Set Name", r, 1); r += 1
        self._number = entry(r, 0)
        self._set    = entry(r, 1); r += 1

        lbl("Year", r, 0, 2); r += 1
        self._year = entry(r, 0, 2); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        # Grading info
        lbl("Grading Company *", r, 0); lbl("Grade *", r, 1); r += 1
        self._company_var = ctk.StringVar(value="PSA")
        ctk.CTkOptionMenu(scroll, values=db.GRADING_COMPANIES,
                          variable=self._company_var, height=32
                          ).grid(row=r, column=0, sticky="ew", padx=(0, 6))
        self._grade = entry(r, 1); r += 1

        lbl("Serial Number", r, 0, 2); r += 1
        self._serial = entry(r, 0, 2); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        # Acquisition
        lbl("Acquisition Type", r, 0); lbl("Purchase Date *", r, 1); r += 1
        self._acq_var = ctk.StringVar(value="Cash")
        ctk.CTkOptionMenu(scroll, values=db.ACQUISITION_TYPES,
                          variable=self._acq_var, height=32
                          ).grid(row=r, column=0, sticky="ew", padx=(0, 6))
        self._acq_date = entry(r, 1, placeholder_text="YYYY-MM-DD")
        self._acq_date.insert(0, _date_str()); r += 1

        lbl("Purchase Price (USD) *", r, 0); lbl("Grading Fee (USD)", r, 1); r += 1
        self._price = entry(r, 0, placeholder_text="0.00")
        self._fee   = entry(r, 1, placeholder_text="0.00"); r += 1

        # total cost display
        self._total_cost_lbl = ctk.CTkLabel(scroll, text="Total Cost: —",
                                            font=ctk.CTkFont(size=12), text_color="gray")
        self._total_cost_lbl.grid(row=r, column=0, columnspan=2, sticky="w", pady=(2, 0)); r += 1
        self._price.bind("<KeyRelease>", self._update_total_cost)
        self._fee.bind("<KeyRelease>",   self._update_total_cost)

        lbl("Market Value (optional)", r, 0, 2); r += 1
        self._mkt = entry(r, 0, 2, placeholder_text="leave blank if unknown"); r += 1

        lbl("Notes", r, 0, 2); r += 1
        self._notes = ctk.CTkTextbox(scroll, height=60)
        self._notes.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0)); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        # Photo
        lbl("Photo (optional)", r, 0, 2); r += 1
        self._photo_lbl = ctk.CTkLabel(scroll, text="No photo selected", text_color="gray")
        self._photo_lbl.grid(row=r, column=0, sticky="w")
        ctk.CTkButton(scroll, text="Choose…", width=90, height=28,
                      command=self._pick_photo
                      ).grid(row=r, column=1, sticky="e", pady=(2, 0)); r += 1

        # action buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Cancel", width=110,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Save Card", width=110,
                      command=self._save).pack(side="right")

    def _update_total_cost(self, event=None):
        try:
            price = float(self._price.get() or 0)
            fee   = float(self._fee.get()   or 0)
            self._total_cost_lbl.configure(text=f"Total Cost: ${price + fee:,.2f}")
        except ValueError:
            self._total_cost_lbl.configure(text="Total Cost: —")

    def _pick_photo(self):
        path = filedialog.askopenfilename(
            title="Select Card Photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.heic *.webp"), ("All", "*.*")]
        )
        if path:
            self._photo_path = path
            from pathlib import Path
            self._photo_lbl.configure(text=Path(path).name, text_color=("gray10", "gray90"))

    def _save(self):
        name  = self._name.get().strip()
        price = self._price.get().strip()
        acq_d = self._acq_date.get().strip()

        if not name:
            messagebox.showerror("Required", "Card name is required.", parent=self)
            return
        if not price:
            messagebox.showerror("Required", "Purchase price is required.", parent=self)
            return
        try:
            price_f = float(price)
        except ValueError:
            messagebox.showerror("Invalid", "Purchase price must be a number.", parent=self)
            return

        fee_str = self._fee.get().strip()
        try:
            fee_f = float(fee_str) if fee_str else 0.0
        except ValueError:
            fee_f = 0.0

        photo_filename = None
        if self._photo_path:
            try:
                photo_filename = db.save_photo(self._photo_path)
            except Exception as e:
                messagebox.showwarning("Photo", f"Could not save photo: {e}", parent=self)

        mkt_str = self._mkt.get().strip()
        mkt_f   = float(mkt_str) if mkt_str else None

        db.add_graded_card(
            serial_number=self._serial.get().strip(),
            grading_company=self._company_var.get(),
            grade=self._grade.get().strip(),
            card_name=name,
            card_number=self._number.get().strip(),
            set_name=self._set.get().strip(),
            photo_filename=photo_filename,
            acquisition_type=self._acq_var.get(),
            acquisition_price=price_f + fee_f,
            grading_fee=fee_f,
            acquisition_date=acq_d,
            notes=self._notes.get("1.0", "end").strip(),
        )
        if mkt_f is not None:
            # set market value on the freshly created card
            from database import get_connection
            conn = get_connection()
            conn.execute(
                "UPDATE graded_cards SET market_value=?, market_value_updated=? "
                "WHERE id=(SELECT MAX(id) FROM graded_cards)",
                (mkt_f, date.today().isoformat())
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
        self.grab_set()

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
        fee = card["grading_fee"] or 0
        if fee > 0:
            purchase_only = card["acquisition_price"] - fee
            lbl_row("Purchase Price",  _fmt_usd(purchase_only), r); r += 1
            lbl_row("Grading Fee",     _fmt_usd(fee),           r); r += 1
            lbl_row("Total Cost",      _fmt_usd(card["acquisition_price"]), r); r += 1
        else:
            lbl_row("Total Cost",      _fmt_usd(card["acquisition_price"]), r); r += 1

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
        self.geometry("380x280")
        self.resizable(False, False)
        self.grab_set()
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
        self._sale_date = ctk.CTkEntry(f, height=34, placeholder_text="YYYY-MM-DD")
        self._sale_date.insert(0, _date_str())
        self._sale_date.grid(row=4, column=0, sticky="ew", pady=(4, 0))

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
