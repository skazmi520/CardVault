"""Ungraded cards view — list, add dialog, detail/edit, grading return dialog."""

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
from PIL import Image
from datetime import date
import database as db

STATUS_COLORS = {
    "Not Slated": "gray",
    "Slated":     "#007AFF",
    "At Grading": "#FF9500",
}

def _date_str() -> str:
    return date.today().isoformat()

def _fmt_usd(val) -> str:
    if val is None:
        return "—"
    return f"${val:,.2f}"

def _apply_treeview_style():
    style = ttk.Style()
    style.theme_use("clam")
    mode = ctk.get_appearance_mode()
    bg  = "#2b2b2b" if mode == "Dark" else "#f0f0f0"
    fg  = "#e0e0e0" if mode == "Dark" else "#1a1a1a"
    sel = "#3a7ebf"
    hdr = "#1e1e1e" if mode == "Dark" else "#d0d0d0"
    style.configure("Treeview",
        background=bg, foreground=fg, fieldbackground=bg, rowheight=34,
        borderwidth=0, font=("SF Pro Text", 12))
    style.configure("Treeview.Heading",
        background=hdr, foreground=fg, font=("SF Pro Text", 12, "bold"), relief="flat")
    style.map("Treeview", background=[("selected", sel)], foreground=[("selected", "white")])


class UngradedView(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app
        self._filter_status = None   # None = All
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build()
        self.refresh()

    def _build(self):
        _apply_treeview_style()

        # header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        ctk.CTkLabel(hdr, text="Ungraded Cards",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="+ Add Card", width=110, height=34,
                      corner_radius=8,
                      command=self._open_add_dialog).pack(side="right")

        # status filter chips
        chip_frame = ctk.CTkFrame(self, fg_color="transparent")
        chip_frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(10, 0))
        self._chip_btns: dict = {}

        for label, key in [("All", None)] + [(s, s) for s in db.GRADING_STATUSES]:
            btn = ctk.CTkButton(
                chip_frame, text=label, width=90, height=28, corner_radius=14,
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self._set_status_filter(k),
            )
            btn.pack(side="left", padx=3)
            self._chip_btns[key] = btn
        self._update_chips()

        # treeview
        tv_frame = ctk.CTkFrame(self, fg_color="transparent")
        tv_frame.grid(row=2, column=0, sticky="nsew", padx=24, pady=12)
        tv_frame.grid_rowconfigure(0, weight=1)
        tv_frame.grid_columnconfigure(0, weight=1)

        cols = ("status", "card_name", "number", "set_name", "year", "cost", "date", "target")
        self.tree = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                 selectmode="browse")

        headings = {
            "status":    ("Status",     110),
            "card_name": ("Card Name",  220),
            "number":    ("Card #",     80),
            "set_name":  ("Set",        150),
            "year":      ("Year",       65),
            "cost":      ("Cost",       90),
            "date":      ("Date Added", 110),
            "target":    ("Target Co.", 90),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=50)

        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Double-1>", self._on_row_double_click)
        self.tree.bind("<Return>",   self._on_row_double_click)

    def refresh(self):
        self._cards = db.get_ungraded_cards(converted=False)
        self._apply_filter()

    def _apply_filter(self):
        cards = self._cards
        if self._filter_status:
            cards = [c for c in cards if c["grading_status"] == self._filter_status]
        self._populate_tree(cards)

    def _populate_tree(self, cards):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for card in cards:
            self.tree.insert("", "end", iid=str(card["id"]), values=(
                card["grading_status"],
                ("★ " if card["is_favorited"] else "") + card["card_name"],
                card["card_number"],
                card["set_name"],
                card["year"],
                _fmt_usd(card["purchase_price"]),
                card["purchase_date"],
                card["target_grading_company"] or "—",
            ))

    def _set_status_filter(self, status):
        self._filter_status = status
        self._update_chips()
        self._apply_filter()

    def _update_chips(self):
        if not hasattr(self, "_chip_btns"):
            return
        for key, btn in self._chip_btns.items():
            if key == self._filter_status:
                color = STATUS_COLORS.get(key, "#3a7ebf") if key else "#3a7ebf"
                btn.configure(fg_color=color, text_color="white")
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray90"))

    def _on_row_double_click(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        card_id = int(sel[0])
        UngradedDetailDialog(self, card_id, on_close=self.refresh)

    def _open_add_dialog(self):
        AddUngradedCardDialog(self, on_save=self.refresh)


# ── add ungraded dialog ───────────────────────────────────────────────────────

class AddUngradedCardDialog(ctk.CTkToplevel):
    def __init__(self, parent, on_save=None):
        super().__init__(parent)
        self.title("Add Ungraded Card")
        self.geometry("500x560")
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

        def lbl(text, row, col=0, span=2):
            ctk.CTkLabel(scroll, text=text, font=ctk.CTkFont(size=12),
                         anchor="w").grid(row=row, column=col, columnspan=span,
                                          sticky="w", pady=(8, 0))

        def entry(row, col=0, span=2, **kw):
            e = ctk.CTkEntry(scroll, height=32, **kw)
            e.grid(row=row, column=col, columnspan=span, sticky="ew",
                   padx=(0, 6) if col == 0 and span == 1 else (6, 0) if col == 1 else 0,
                   pady=(2, 0))
            return e

        lbl("Card Name *", r); r += 1
        self._name = entry(r); r += 1

        lbl("Card Number", r, 0, 1); lbl("Set Name", r, 1, 1); r += 1
        self._number = entry(r, 0, 1)
        self._set    = entry(r, 1, 1); r += 1

        lbl("Year", r, 0, 1); lbl("Purchase Date *", r, 1, 1); r += 1
        self._year = entry(r, 0, 1)
        self._pdate = entry(r, 1, 1, placeholder_text="YYYY-MM-DD")
        self._pdate.insert(0, _date_str()); r += 1

        lbl("Purchase Price (USD) *", r); r += 1
        self._price = entry(r, placeholder_text="0.00"); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        lbl("Grading Status", r, 0, 1); lbl("Target Company", r, 1, 1); r += 1
        self._status_var = ctk.StringVar(value="Not Slated")
        ctk.CTkOptionMenu(scroll, values=db.GRADING_STATUSES,
                          variable=self._status_var, height=32
                          ).grid(row=r, column=0, sticky="ew", padx=(0, 6))
        self._target_var = ctk.StringVar(value="")
        ctk.CTkOptionMenu(scroll, values=[""] + db.GRADING_COMPANIES,
                          variable=self._target_var, height=32
                          ).grid(row=r, column=1, sticky="ew", padx=(6, 0)); r += 1

        lbl("Notes", r); r += 1
        self._notes = ctk.CTkTextbox(scroll, height=60)
        self._notes.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(2, 0)); r += 1

        ctk.CTkFrame(scroll, height=1, fg_color="gray40").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=10); r += 1

        lbl("Photo (optional)", r); r += 1
        self._photo_lbl = ctk.CTkLabel(scroll, text="No photo selected", text_color="gray")
        self._photo_lbl.grid(row=r, column=0, sticky="w")
        ctk.CTkButton(scroll, text="Choose…", width=90, height=28,
                      command=self._pick_photo).grid(row=r, column=1, sticky="e"); r += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(btn_frame, text="Cancel", width=100,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Save Card", width=110,
                      command=self._save).pack(side="right")

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
        if not name:
            messagebox.showerror("Required", "Card name is required.", parent=self); return
        if not price:
            messagebox.showerror("Required", "Purchase price is required.", parent=self); return
        try:
            price_f = float(price)
        except ValueError:
            messagebox.showerror("Invalid", "Price must be a number.", parent=self); return

        photo_fn = None
        if self._photo_path:
            try:
                photo_fn = db.save_photo(self._photo_path)
            except Exception as e:
                messagebox.showwarning("Photo", f"Could not save photo: {e}", parent=self)

        db.add_ungraded_card(
            card_name=name,
            card_number=self._number.get().strip(),
            set_name=self._set.get().strip(),
            year=self._year.get().strip(),
            photo_filename=photo_fn,
            purchase_price=price_f,
            purchase_date=self._pdate.get().strip(),
            notes=self._notes.get("1.0", "end").strip(),
            grading_status=self._status_var.get(),
            target_grading_company=self._target_var.get(),
        )
        if self._on_save:
            self._on_save()
        self.destroy()


# ── ungraded detail dialog ────────────────────────────────────────────────────

class UngradedDetailDialog(ctk.CTkToplevel):
    def __init__(self, parent, card_id: int, on_close=None):
        super().__init__(parent)
        self._card_id  = card_id
        self._on_close = on_close
        self._img_ref  = None
        card = db.get_ungraded_card(card_id)
        self.title(card["card_name"] if card else "Card Detail")
        self.geometry("500x580")
        self.resizable(False, False)
        self.grab_set()
        if not card:
            ctk.CTkLabel(self, text="Not found.").pack(pady=40)
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
                img = Image.open(img_path).resize((140, 140))
                self._img_ref = ctk.CTkImage(img, size=(140, 140))
                ctk.CTkLabel(scroll, image=self._img_ref, text="").grid(
                    row=r, column=0, columnspan=2, pady=(0, 10)); r += 1
            except Exception:
                pass

        # status badge
        color = STATUS_COLORS.get(card["grading_status"], "gray")
        ctk.CTkLabel(
            scroll,
            text=f"  {card['grading_status']}  ",
            fg_color=color, corner_radius=8,
            text_color="white", font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        # update status inline
        ctk.CTkLabel(scroll, text="Update Status:", font=ctk.CTkFont(size=11),
                     text_color="gray", anchor="w"
                     ).grid(row=r, column=0, sticky="w")
        self._status_var = ctk.StringVar(value=card["grading_status"])
        status_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        status_frame.grid(row=r, column=1, sticky="w"); r += 1
        ctk.CTkOptionMenu(status_frame, values=db.GRADING_STATUSES,
                          variable=self._status_var, height=28, width=140
                          ).pack(side="left")
        ctk.CTkButton(status_frame, text="Save", width=60, height=28,
                      command=self._save_status).pack(side="left", padx=6)

        def lbl_row(label, value, row):
            ctk.CTkLabel(scroll, text=label, font=ctk.CTkFont(size=11),
                         text_color="gray", anchor="w"
                         ).grid(row=row, column=0, sticky="w", pady=(8, 0))
            ctk.CTkLabel(scroll, text=str(value) if value else "—",
                         font=ctk.CTkFont(size=13), anchor="w"
                         ).grid(row=row, column=1, sticky="w", pady=(8, 0))

        lbl_row("Card Name",    card["card_name"],       r); r += 1
        lbl_row("Card Number",  card["card_number"],     r); r += 1
        lbl_row("Set",          card["set_name"],        r); r += 1
        lbl_row("Year",         card["year"],            r); r += 1
        lbl_row("Cost",         _fmt_usd(card["purchase_price"]), r); r += 1
        lbl_row("Purchase Date",card["purchase_date"],   r); r += 1
        lbl_row("Target Co.",   card["target_grading_company"] or "—", r); r += 1
        if card["notes"]:
            lbl_row("Notes",    card["notes"],           r); r += 1

        # action buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkButton(btn_frame, text="Close", width=90,
                      fg_color="transparent", border_width=1,
                      command=self._close).pack(side="left")

        ctk.CTkButton(btn_frame, text="Record Grading Return", width=170,
                      fg_color="#007AFF",
                      command=self._open_grading_return).pack(side="right")

        ctk.CTkButton(btn_frame, text="Delete", width=80,
                      fg_color="#FF3B30",
                      command=self._delete).pack(side="right", padx=8)

    def _save_status(self):
        db.update_ungraded_card(self._card_id, grading_status=self._status_var.get())
        messagebox.showinfo("Saved", "Grading status updated.", parent=self)
        self._close()

    def _open_grading_return(self):
        GradingReturnDialog(self, self._card_id, on_save=self._close)

    def _delete(self):
        if messagebox.askyesno("Delete", f"Delete '{self._card['card_name']}'?", parent=self):
            db.delete_ungraded_card(self._card_id)
            self._close()

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()


# ── grading return dialog ─────────────────────────────────────────────────────

class GradingReturnDialog(ctk.CTkToplevel):
    def __init__(self, parent, ungraded_id: int, on_save=None):
        super().__init__(parent)
        self._ungraded_id = ungraded_id
        self._on_save     = on_save
        card = db.get_ungraded_card(ungraded_id)
        self.title(f"Grading Return — {card['card_name'] if card else ''}")
        self.geometry("420x380")
        self.resizable(False, False)
        self.grab_set()
        self._card = card
        self._build()

    def _build(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=24, pady=20)
        f.grid_columnconfigure((0, 1), weight=1)
        r = 0

        ctk.CTkLabel(f, text="Record Grading Return",
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 12)); r += 1

        ctk.CTkLabel(f, text="The card will be moved to your graded inventory.",
                     font=ctk.CTkFont(size=11), text_color="gray"
                     ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 12)); r += 1

        def lbl(text, row, col=0, span=2):
            ctk.CTkLabel(f, text=text, font=ctk.CTkFont(size=12),
                         text_color="gray", anchor="w"
                         ).grid(row=row, column=col, columnspan=span, sticky="w", pady=(8, 0))

        def entry(row, col=0, span=2, **kw):
            e = ctk.CTkEntry(f, height=32, **kw)
            e.grid(row=row, column=col, columnspan=span, sticky="ew",
                   padx=(0, 6) if col == 0 and span == 1 else (6, 0) if col == 1 else 0,
                   pady=(2, 0))
            return e

        lbl("Grading Company *", r, 0, 1); lbl("Grade Received *", r, 1, 1); r += 1
        self._company_var = ctk.StringVar(value=self._card.get("target_grading_company") or "PSA")
        ctk.CTkOptionMenu(f, values=db.GRADING_COMPANIES,
                          variable=self._company_var, height=32
                          ).grid(row=r, column=0, sticky="ew", padx=(0, 6))
        self._grade = entry(r, 1, 1, placeholder_text="e.g. 9"); r += 1

        lbl("Serial Number *", r); r += 1
        self._serial = entry(r); r += 1

        lbl("Return Date *", r); r += 1
        self._ret_date = entry(r, placeholder_text="YYYY-MM-DD")
        self._ret_date.insert(0, _date_str()); r += 1

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))
        ctk.CTkButton(btn_frame, text="Cancel", width=100,
                      fg_color="transparent", border_width=1,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(btn_frame, text="Convert to Graded", width=150,
                      fg_color="#34C759",
                      command=self._save).pack(side="right")

    def _save(self):
        grade  = self._grade.get().strip()
        serial = self._serial.get().strip()
        ret_d  = self._ret_date.get().strip()

        if not grade:
            messagebox.showerror("Required", "Grade is required.", parent=self); return
        if not serial:
            messagebox.showerror("Required", "Serial number is required.", parent=self); return

        db.convert_ungraded_to_graded(
            ungraded_id=self._ungraded_id,
            serial_number=serial,
            grading_company=self._company_var.get(),
            grade=grade,
            acquisition_date=ret_d,
        )
        messagebox.showinfo("Done",
                            "Card converted and added to your graded inventory!",
                            parent=self)
        if self._on_save:
            self._on_save()
        self.destroy()
