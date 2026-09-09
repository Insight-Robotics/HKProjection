"""Desktop GUI for WGS84 <-> HK1980 Grid conversion.

Three tabs:

    Single point   type coordinates in, read the answer out
    Batch file     convert a CSV or a shapefile
    Height (HKPD)  ellipsoidal height <-> Hong Kong Principal Datum level

Built on tkinter, so it needs nothing beyond the standard library; shapefile
support additionally needs ``pyshp``, and the Batch tab says so if it is missing.

    python hkgui.py
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import hk1980
import hkbatch as hb
from hk1980 import ELLIPSOIDAL, HKPD

APP_TITLE = "Hong Kong 1980 Grid Converter"

WGS84_TO_HK = "WGS84  ->  HK1980 Grid"
HK_TO_WGS84 = "HK1980 Grid  ->  WGS84"

HEIGHT_LABELS = {
    ELLIPSOIDAL: "Ellipsoidal (WGS84)",
    HKPD: "HKPD level",
}
LABEL_TO_HEIGHT = {v: k for k, v in HEIGHT_LABELS.items()}

MONO = ("Consolas", 10)

#: Shown in the height column chooser when a Z-aware shapefile supplies the
#: height from its geometry rather than from an attribute.
USE_Z = "<from Z ordinate>"


def _direction_of(label: str) -> str:
    return hb.TO_HK1980 if label == WGS84_TO_HK else hb.TO_WGS84


# ==========================================================================
# Single point
# ==========================================================================

class SinglePointTab(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master, padding=12)
        self.direction = tk.StringVar(value=WGS84_TO_HK)
        self.height_type = tk.StringVar(value=HEIGHT_LABELS[ELLIPSOIDAL])
        self.strict = tk.BooleanVar(value=True)
        self._build()
        self._on_direction()

    def _build(self) -> None:
        dirbox = ttk.LabelFrame(self, text="Direction", padding=8)
        dirbox.pack(fill="x")
        for text in (WGS84_TO_HK, HK_TO_WGS84):
            ttk.Radiobutton(dirbox, text=text, value=text, variable=self.direction,
                            command=self._on_direction).pack(side="left", padx=(0, 20))

        inbox = ttk.LabelFrame(self, text="Input", padding=8)
        inbox.pack(fill="x", pady=(10, 0))
        inbox.columnconfigure(1, weight=1)

        self.lbl_a = ttk.Label(inbox, text="Latitude")
        self.lbl_a.grid(row=0, column=0, sticky="w", pady=3)
        self.ent_a = ttk.Entry(inbox, font=MONO)
        self.ent_a.grid(row=0, column=1, sticky="ew", padx=6)
        self.hint_a = ttk.Label(inbox, text="", foreground="#666")
        self.hint_a.grid(row=0, column=2, sticky="w")

        self.lbl_b = ttk.Label(inbox, text="Longitude")
        self.lbl_b.grid(row=1, column=0, sticky="w", pady=3)
        self.ent_b = ttk.Entry(inbox, font=MONO)
        self.ent_b.grid(row=1, column=1, sticky="ew", padx=6)
        self.hint_b = ttk.Label(inbox, text="", foreground="#666")
        self.hint_b.grid(row=1, column=2, sticky="w")

        ttk.Label(inbox, text="Height (m)").grid(row=2, column=0, sticky="w", pady=3)
        self.ent_h = ttk.Entry(inbox, font=MONO)
        self.ent_h.grid(row=2, column=1, sticky="ew", padx=6)
        self.ent_h.insert(0, "0")
        self.cmb_ht = ttk.Combobox(
            inbox, textvariable=self.height_type, state="readonly", width=20,
            values=[HEIGHT_LABELS[ELLIPSOIDAL], HEIGHT_LABELS[HKPD]])
        self.cmb_ht.grid(row=2, column=2, sticky="w")

        ttk.Checkbutton(inbox, text="Restrict to Height Model coverage",
                        variable=self.strict).grid(row=3, column=1, sticky="w", padx=6,
                                                   pady=(6, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=10)
        ttk.Button(btns, text="Convert", command=self.convert).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left", padx=6)
        ttk.Button(btns, text="Copy result", command=self.copy).pack(side="left")

        outbox = ttk.LabelFrame(self, text="Result", padding=8)
        outbox.pack(fill="both", expand=True)
        self.out = tk.Text(outbox, height=15, font=MONO, wrap="none",
                           state="disabled", background="#f7f7f7")
        self.out.pack(fill="both", expand=True)

    # -- behaviour ---------------------------------------------------------

    def _on_direction(self) -> None:
        if _direction_of(self.direction.get()) == hb.TO_HK1980:
            self.lbl_a.config(text="Latitude")
            self.lbl_b.config(text="Longitude")
            self.hint_a.config(text="22.3193  or  22 19 09.5 N")
            self.hint_b.config(text="114.1694  or  114 10 09.8 E")
            self.height_type.set(HEIGHT_LABELS[ELLIPSOIDAL])
        else:
            self.lbl_a.config(text="Northing (m)")
            self.lbl_b.config(text="Easting (m)")
            self.hint_a.config(text="820032.9")
            self.hint_b.config(text="835497.9")
            self.height_type.set(HEIGHT_LABELS[HKPD])

    def clear(self) -> None:
        for e in (self.ent_a, self.ent_b, self.ent_h):
            e.delete(0, "end")
        self.ent_h.insert(0, "0")
        self._show("")

    def copy(self) -> None:
        text = self.out.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _show(self, text: str) -> None:
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.config(state="disabled")

    def convert(self) -> None:
        direction = _direction_of(self.direction.get())
        height_type = LABEL_TO_HEIGHT[self.height_type.get()]
        try:
            raw_h = self.ent_h.get().strip() or "0"
            height = hb.parse_number(raw_h)
            if direction == hb.TO_HK1980:
                a = hb.parse_angle(self.ent_a.get())
                b = hb.parse_angle(self.ent_b.get())
            else:
                a = hb.parse_number(self.ent_a.get())
                b = hb.parse_number(self.ent_b.get())
        except ValueError as exc:
            self._show("Input error:\n  %s" % exc)
            return

        try:
            if direction == hb.TO_HK1980:
                r = hk1980.wgs84_to_hk1980(a, b, height, strict=self.strict.get(),
                                           height_type=height_type)
                self._show(self._format_forward(a, b, r))
            else:
                r = hk1980.hk1980_to_wgs84(a, b, height, strict=self.strict.get(),
                                           height_type=height_type)
                self._show(self._format_inverse(a, b, r))
        except ValueError as exc:
            self._show("Conversion error:\n  %s" % exc)
        except Exception:
            self._show("Unexpected error:\n%s" % traceback.format_exc())

    # -- formatting --------------------------------------------------------

    @staticmethod
    def _dms(deg: float, hemis: str) -> str:
        d, m, s = hk1980.to_dms(deg)
        return "%d%s %02d' %08.5f\" %s" % (abs(d), chr(176), m, s,
                                           hemis[0] if deg >= 0 else hemis[1])

    def _format_forward(self, lat, lon, r) -> str:
        L = []
        L.append("INPUT   WGS84 / ITRF96")
        L.append("  Latitude        %16.9f    %s" % (lat, self._dms(lat, "NS")))
        L.append("  Longitude       %16.9f    %s" % (lon, self._dms(lon, "EW")))
        L.append("")
        L.append("OUTPUT  HK1980 Grid")
        L.append("  Northing  N     %16.3f m" % r.N)
        L.append("  Easting   E     %16.3f m" % r.E)
        L.append("")
        L.append("HEIGHTS")
        L.append("  HKPD level      %16.3f m" % r.h_hkpd)
        L.append("  Ellipsoidal     %16.3f m   (WGS84)" % r.ellipsoidal_height)
        L.append("  Separation      %16.3f m   (H - h)" % r.separation)
        L.append("")
        L.append("INTERMEDIATE  HK80 datum geographic")
        L.append("  Latitude        %16.9f    %s" % (r.hk80_lat, self._dms(r.hk80_lat, "NS")))
        L.append("  Longitude       %16.9f    %s" % (r.hk80_lon, self._dms(r.hk80_lon, "EW")))
        L.append("  Ellipsoidal ht  %16.3f m   (International 1910 - not a level)"
                 % r.hk80_height)
        return "\n".join(L)

    def _format_inverse(self, N, E, r) -> str:
        L = []
        L.append("INPUT   HK1980 Grid")
        L.append("  Northing  N     %16.3f m" % N)
        L.append("  Easting   E     %16.3f m" % E)
        L.append("")
        L.append("OUTPUT  WGS84 / ITRF96")
        L.append("  Latitude        %16.9f    %s" % (r.lat, self._dms(r.lat, "NS")))
        L.append("  Longitude       %16.9f    %s" % (r.lon, self._dms(r.lon, "EW")))
        L.append("")
        L.append("HEIGHTS")
        L.append("  Ellipsoidal     %16.3f m   (WGS84, approximate - see note *6)"
                 % r.ellipsoidal_height)
        L.append("  HKPD level      %16.3f m" % r.h_hkpd)
        L.append("  Separation      %16.3f m   (H - h)" % r.separation)
        L.append("")
        L.append("INTERMEDIATE  HK80 datum geographic")
        L.append("  Latitude        %16.9f    %s" % (r.hk80_lat, self._dms(r.hk80_lat, "NS")))
        L.append("  Longitude       %16.9f    %s" % (r.hk80_lon, self._dms(r.hk80_lon, "EW")))
        return "\n".join(L)


# ==========================================================================
# Height converter
# ==========================================================================

class HeightTab(ttk.Frame):
    """Ellipsoidal height <-> HKPD level at a given position."""

    def __init__(self, master) -> None:
        super().__init__(master, padding=12)
        self.pos_kind = tk.StringVar(value="WGS84 latitude / longitude")
        self.from_type = tk.StringVar(value=HEIGHT_LABELS[ELLIPSOIDAL])
        self.strict = tk.BooleanVar(value=True)
        self._build()

    def _build(self) -> None:
        ttk.Label(
            self, wraplength=620, foreground="#444",
            text=("Converts between height above the WGS84 ellipsoid and level above "
                  "Hong Kong Principal Datum, using the separation model built from "
                  "the 74 control points of the Height Model of Hong Kong v1.0. "
                  "The position is only used to look up the separation, so it need "
                  "only be good to a few hundred metres.")
        ).pack(fill="x", pady=(0, 10))

        box = ttk.LabelFrame(self, text="Position", padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Given as").grid(row=0, column=0, sticky="w", pady=3)
        cmb = ttk.Combobox(box, textvariable=self.pos_kind, state="readonly", width=32,
                           values=["WGS84 latitude / longitude",
                                   "HK1980 Grid northing / easting"])
        cmb.grid(row=0, column=1, sticky="w", padx=6)
        cmb.bind("<<ComboboxSelected>>", lambda _e: self._on_kind())

        self.lbl_a = ttk.Label(box, text="Latitude")
        self.lbl_a.grid(row=1, column=0, sticky="w", pady=3)
        self.ent_a = ttk.Entry(box, font=MONO)
        self.ent_a.grid(row=1, column=1, sticky="ew", padx=6)

        self.lbl_b = ttk.Label(box, text="Longitude")
        self.lbl_b.grid(row=2, column=0, sticky="w", pady=3)
        self.ent_b = ttk.Entry(box, font=MONO)
        self.ent_b.grid(row=2, column=1, sticky="ew", padx=6)

        hbox = ttk.LabelFrame(self, text="Height", padding=8)
        hbox.pack(fill="x", pady=(10, 0))
        hbox.columnconfigure(1, weight=1)
        ttk.Label(hbox, text="Value (m)").grid(row=0, column=0, sticky="w", pady=3)
        self.ent_h = ttk.Entry(hbox, font=MONO)
        self.ent_h.grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(hbox, text="is a").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(hbox, textvariable=self.from_type, state="readonly", width=22,
                     values=[HEIGHT_LABELS[ELLIPSOIDAL], HEIGHT_LABELS[HKPD]]
                     ).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Checkbutton(hbox, text="Restrict to Height Model coverage",
                        variable=self.strict).grid(row=2, column=1, sticky="w",
                                                   padx=6, pady=(6, 0))

        ttk.Button(self, text="Convert", command=self.convert).pack(anchor="w", pady=10)

        out = ttk.LabelFrame(self, text="Result", padding=8)
        out.pack(fill="both", expand=True)
        self.out = tk.Text(out, height=8, font=MONO, wrap="none", state="disabled",
                           background="#f7f7f7")
        self.out.pack(fill="both", expand=True)

    def _on_kind(self) -> None:
        if self.pos_kind.get().startswith("WGS84"):
            self.lbl_a.config(text="Latitude")
            self.lbl_b.config(text="Longitude")
        else:
            self.lbl_a.config(text="Northing (m)")
            self.lbl_b.config(text="Easting (m)")

    def _show(self, text: str) -> None:
        self.out.config(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text)
        self.out.config(state="disabled")

    def convert(self) -> None:
        try:
            height = hb.parse_number(self.ent_h.get())
            if self.pos_kind.get().startswith("WGS84"):
                lat = hb.parse_angle(self.ent_a.get())
                lon = hb.parse_angle(self.ent_b.get())
            else:
                N = hb.parse_number(self.ent_a.get())
                E = hb.parse_number(self.ent_b.get())
                r = hk1980.hk1980_to_wgs84(N, E, height, strict=self.strict.get(),
                                           height_type=LABEL_TO_HEIGHT[self.from_type.get()])
                lat, lon = r.lat, r.lon
        except ValueError as exc:
            self._show("Input error:\n  %s" % exc)
            return

        try:
            hm = hk1980.default_height_model()
            if self.strict.get() and not hm.contains(lat, lon):
                raise ValueError(
                    "position (%.6f, %.6f) is outside the Height Model coverage %s"
                    % (lat, lon, hm.bounds))
            sep = hm.separation(lat, lon)
        except ValueError as exc:
            self._show("Conversion error:\n  %s" % exc)
            return

        if LABEL_TO_HEIGHT[self.from_type.get()] == ELLIPSOIDAL:
            H, h = height, height - sep
        else:
            h, H = height, height + sep

        L = ["POSITION  (WGS84)",
             "  Latitude        %16.9f" % lat,
             "  Longitude       %16.9f" % lon,
             "",
             "HEIGHTS",
             "  Ellipsoidal     %16.3f m   (above the WGS84 ellipsoid)" % H,
             "  HKPD level      %16.3f m   (above Hong Kong Principal Datum)" % h,
             "  Separation      %16.3f m   (N_sep = H - h)" % sep,
             "",
             "Model accuracy at the control points: 0.023 m rms (leave-one-out)."]
        self._show("\n".join(L))


# ==========================================================================
# Batch file
# ==========================================================================

class BatchTab(ttk.Frame):
    def __init__(self, master) -> None:
        super().__init__(master, padding=12)
        self.in_path = tk.StringVar()
        self.out_path = tk.StringVar()
        self.direction = tk.StringVar(value=WGS84_TO_HK)
        self.height_type = tk.StringVar(value=HEIGHT_LABELS[ELLIPSOIDAL])
        self.strict = tk.BooleanVar(value=True)
        self.col_a = tk.StringVar()
        self.col_b = tk.StringVar()
        self.col_h = tk.StringVar()
        self._kind = None          # "csv" | "shp"
        self._header = []
        self._queue = queue.Queue()
        self._worker = None
        self._build()
        self._on_direction()

    def _build(self) -> None:
        top = ttk.LabelFrame(self, text="Input file", padding=8)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        ttk.Entry(top, textvariable=self.in_path, font=MONO).grid(row=0, column=0, sticky="ew")
        ttk.Button(top, text="Browse...", command=self.browse_in).grid(row=0, column=1, padx=6)
        self.info = ttk.Label(top, text="CSV (.csv) or ESRI shapefile (.shp)",
                              foreground="#666")
        self.info.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        dirbox = ttk.LabelFrame(self, text="Direction", padding=8)
        dirbox.pack(fill="x", pady=(10, 0))
        for text in (WGS84_TO_HK, HK_TO_WGS84):
            ttk.Radiobutton(dirbox, text=text, value=text, variable=self.direction,
                            command=self._on_direction).pack(side="left", padx=(0, 20))

        self.mapbox = ttk.LabelFrame(self, text="Columns", padding=8)
        self.mapbox.pack(fill="x", pady=(10, 0))
        self.mapbox.columnconfigure(1, weight=1)
        self.mapbox.columnconfigure(3, weight=1)

        self.lbl_ca = ttk.Label(self.mapbox, text="Latitude")
        self.lbl_ca.grid(row=0, column=0, sticky="w", pady=3)
        self.cmb_a = ttk.Combobox(self.mapbox, textvariable=self.col_a, state="readonly")
        self.cmb_a.grid(row=0, column=1, sticky="ew", padx=6)

        self.lbl_cb = ttk.Label(self.mapbox, text="Longitude")
        self.lbl_cb.grid(row=0, column=2, sticky="w", pady=3)
        self.cmb_b = ttk.Combobox(self.mapbox, textvariable=self.col_b, state="readonly")
        self.cmb_b.grid(row=0, column=3, sticky="ew", padx=6)

        ttk.Label(self.mapbox, text="Height").grid(row=1, column=0, sticky="w", pady=3)
        self.cmb_h = ttk.Combobox(self.mapbox, textvariable=self.col_h, state="readonly")
        self.cmb_h.grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Label(self.mapbox, text="is a").grid(row=1, column=2, sticky="w")
        ttk.Combobox(self.mapbox, textvariable=self.height_type, state="readonly",
                     values=[HEIGHT_LABELS[ELLIPSOIDAL], HEIGHT_LABELS[HKPD]]
                     ).grid(row=1, column=3, sticky="ew", padx=6)
        ttk.Checkbutton(self.mapbox, text="Restrict to Height Model coverage",
                        variable=self.strict).grid(row=2, column=1, columnspan=3,
                                                   sticky="w", padx=6, pady=(6, 0))

        outbox = ttk.LabelFrame(self, text="Output file", padding=8)
        outbox.pack(fill="x", pady=(10, 0))
        outbox.columnconfigure(0, weight=1)
        ttk.Entry(outbox, textvariable=self.out_path, font=MONO).grid(row=0, column=0, sticky="ew")
        ttk.Button(outbox, text="Browse...", command=self.browse_out).grid(row=0, column=1, padx=6)

        run = ttk.Frame(self)
        run.pack(fill="x", pady=10)
        self.btn = ttk.Button(run, text="Convert", command=self.start)
        self.btn.pack(side="left")
        self.bar = ttk.Progressbar(run, mode="determinate")
        self.bar.pack(side="left", fill="x", expand=True, padx=10)

        logbox = ttk.LabelFrame(self, text="Log", padding=8)
        logbox.pack(fill="both", expand=True)
        self.log = tk.Text(logbox, height=10, font=MONO, wrap="none", state="disabled",
                           background="#f7f7f7")
        scroll = ttk.Scrollbar(logbox, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

    # -- helpers -----------------------------------------------------------

    def _say(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_direction(self) -> None:
        to_hk = _direction_of(self.direction.get()) == hb.TO_HK1980
        self.lbl_ca.config(text="Latitude" if to_hk else "Northing")
        self.lbl_cb.config(text="Longitude" if to_hk else "Easting")
        self.height_type.set(HEIGHT_LABELS[ELLIPSOIDAL if to_hk else HKPD])
        if self._kind == "csv" and self._header:
            self._apply_guess()
        self._suggest_output()

    def _apply_guess(self) -> None:
        guess = hb.guess_mapping(self._header, _direction_of(self.direction.get()))
        self.col_a.set(guess["a"] or "")
        self.col_b.set(guess["b"] or "")
        self.col_h.set(guess["height"] or hb.NO_HEIGHT)

    def _suggest_output(self) -> None:
        path = self.in_path.get().strip()
        if not path:
            return
        stem, ext = os.path.splitext(path)
        tag = "hk1980" if _direction_of(self.direction.get()) == hb.TO_HK1980 else "wgs84"
        self.out_path.set("%s_%s%s" % (stem, tag, ext.lower()))

    # -- file selection ----------------------------------------------------

    def browse_in(self) -> None:
        path = filedialog.askopenfilename(
            title="Select input file",
            filetypes=[("CSV and shapefiles", "*.csv *.shp"),
                       ("CSV files", "*.csv"), ("Shapefiles", "*.shp"),
                       ("All files", "*.*")])
        if path:
            self.load(path)

    def browse_out(self) -> None:
        ext = ".shp" if self._kind == "shp" else ".csv"
        path = filedialog.asksaveasfilename(
            title="Save output as", defaultextension=ext,
            initialfile=os.path.basename(self.out_path.get() or ("output" + ext)),
            filetypes=[("Shapefiles", "*.shp")] if self._kind == "shp"
            else [("CSV files", "*.csv")])
        if path:
            self.out_path.set(path)

    def load(self, path: str) -> None:
        self.in_path.set(path)
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".shp":
                self._load_shp(path)
            else:
                self._load_csv(path)
        except Exception as exc:
            self._kind = None
            self.info.config(text="Could not read file: %s" % exc, foreground="#a00")
            return
        self._suggest_output()

    def _load_csv(self, path: str) -> None:
        header, sample = hb.sniff_csv(path)
        if not header:
            raise ValueError("file has no header row")
        self._kind = "csv"
        self._header = header
        choices = list(header)
        self.cmb_a.config(values=choices, state="readonly")
        self.cmb_b.config(values=choices, state="readonly")
        self.cmb_h.config(values=[hb.NO_HEIGHT] + choices, state="readonly")
        self._apply_guess()
        self.info.config(
            text="CSV, %d columns, %s%s" % (
                len(header), ", ".join(header[:6]), " ..." if len(header) > 6 else ""),
            foreground="#666")
        self._say("Loaded CSV: %s" % os.path.basename(path))
        if sample:
            self._say("  first row: %s" % ", ".join(str(v) for v in sample[0][:6]))

    def _load_shp(self, path: str) -> None:
        info = hb.describe_shapefile(path)
        self._kind = "shp"
        self._header = list(info["fields"])
        # Geometry supplies the coordinates, so only the height field is a choice.
        self.cmb_a.config(values=["<from geometry>"], state="disabled")
        self.cmb_b.config(values=["<from geometry>"], state="disabled")
        self.col_a.set("<from geometry>")
        self.col_b.set("<from geometry>")
        # A Z-aware layer supplies the height from its geometry unless the user
        # picks an attribute instead; say so rather than showing "none / 0".
        default = USE_Z if info["has_z"] else hb.NO_HEIGHT
        options = ([USE_Z] if info["has_z"] else []) + [hb.NO_HEIGHT] + self._header
        self.cmb_h.config(values=options, state="readonly")
        self.col_h.set(hb.guess_column(self._header, hb.HEIGHT_NAMES) or default)
        self.info.config(
            text="Shapefile %s, %d features, %d attribute fields%s"
                 % (info["shape_type_name"], info["count"], len(info["fields"]),
                    ", has Z" if info["has_z"] else ""),
            foreground="#666")
        self._say("Loaded shapefile: %s" % os.path.basename(path))
        if info["has_z"]:
            self._say("  Z ordinate present; used as the height unless a field is chosen")
        if info["prj"]:
            self._say("  .prj: %s" % info["prj"][:110])
        else:
            self._say("  no .prj found; the Direction setting decides the input system")
        if not info["is_point"]:
            self._say("  non-point layer: vertices transformed, no attributes added")

    # -- running -----------------------------------------------------------

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        src, dst = self.in_path.get().strip(), self.out_path.get().strip()
        if not src or not os.path.exists(src):
            messagebox.showerror(APP_TITLE, "Choose an input file that exists.")
            return
        if not dst:
            messagebox.showerror(APP_TITLE, "Choose an output file.")
            return
        if os.path.abspath(src) == os.path.abspath(dst):
            messagebox.showerror(APP_TITLE, "Output must differ from the input file.")
            return
        if os.path.exists(dst) and not messagebox.askyesno(
                APP_TITLE, "%s already exists.\n\nOverwrite it?" % os.path.basename(dst)):
            return

        # Read every tk variable here, on the main thread: Tk objects must not be
        # touched from the worker.
        direction = _direction_of(self.direction.get())
        height_type = LABEL_TO_HEIGHT[self.height_type.get()]
        strict = bool(self.strict.get())
        mapping = None
        height_field = None
        use_z = False
        if self._kind == "csv":
            if not self.col_a.get() or not self.col_b.get():
                messagebox.showerror(APP_TITLE, "Choose both coordinate columns.")
                return
            mapping = {"a": self.col_a.get(), "b": self.col_b.get(),
                       "height": None if self.col_h.get() == hb.NO_HEIGHT else self.col_h.get()}
        else:
            chosen = self.col_h.get()
            height_field = None if chosen in ("", hb.NO_HEIGHT, USE_Z) else chosen
            use_z = chosen == USE_Z

        self.btn.config(state="disabled")
        self.bar.config(value=0, maximum=100)
        self._say("")
        self._say("Converting %s -> %s" % (os.path.basename(src), os.path.basename(dst)))

        def progress(done, total):
            self._queue.put(("progress", (done, total)))

        def work():
            try:
                rep = hb.convert_file(src, dst, direction, mapping,
                                      strict=strict,
                                      height_field=height_field,
                                      progress=progress, height_type=height_type,
                                      use_z=use_z)
                self._queue.put(("done", rep))
            except Exception as exc:
                self._queue.put(("error", exc))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self.after(80, self._drain)

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "progress":
                    done, total = payload
                    self.bar.config(maximum=max(total, 1), value=done)
                elif kind == "done":
                    self._finish(payload)
                    return
                elif kind == "error":
                    self.bar.config(value=0)
                    self.btn.config(state="normal")
                    self._say("  ERROR: %s" % payload)
                    messagebox.showerror(APP_TITLE, str(payload))
                    return
        except queue.Empty:
            pass
        # Keep polling until a "done" or "error" message returns out of here;
        # work() always sends exactly one of the two, including on failure.
        self.after(80, self._drain)

    def _finish(self, rep) -> None:
        self.btn.config(state="normal")
        self.bar.config(value=rep.total, maximum=max(rep.total, 1))
        for m in rep.messages:
            self._say("  %s" % m)
        self._say("  %s" % rep.summary())
        for idx, msg in rep.errors[:15]:
            self._say("    row %d: %s" % (idx, msg))
        if len(rep.errors) > 15:
            self._say("    ... and %d more" % (len(rep.errors) - 15))
        self._say("  wrote %s" % rep.output_path)
        if rep.failed:
            messagebox.showwarning(
                APP_TITLE,
                "%s\n\nRows that failed are still written out, with the reason in the "
                "conv_status column." % rep.summary())
        else:
            messagebox.showinfo(APP_TITLE, "%s\n\nWrote %s"
                                % (rep.summary(), os.path.basename(rep.output_path)))


# ==========================================================================
# About
# ==========================================================================

ABOUT = """\
Hong Kong 1980 Grid Converter

Converts between WGS84 / ITRF96 geographic coordinates and HK1980 Grid
coordinates, in three dimensions, following the chain in the Lands Department
schematic diagram:

    WGS84 phi, lambda, H
      -> WGS84 cartesian X, Y, Z
      -> 7-parameter Helmert datum shift (7P_ITRF96_HK80_V1.0)
      -> HK80 cartesian X, Y, Z
      -> HK80 phi, lambda, H  (International 1910 ellipsoid)
      -> HK1980 Grid N, E     (Eq.1 - Eq.5, Explanatory Notes pages C9 - C10)

The HK1980 Grid itself is two-dimensional.  The usable third dimension is the
Hong Kong Principal Datum level, obtained from a separation model built on the
74 control points of the Height Model of Hong Kong v1.0:

    h_HKPD = H_WGS84 - N_sep(phi, lambda)

Accuracy
  Horizontal   agrees with an independent implementation (pyproj EPSG:2326)
               to 1.4 cm at worst over the 74 control points.
  HKPD level   0.023 m rms by leave-one-out cross-validation.
  Coverage     22.199 - 22.550 N, 113.853 - 114.372 E.  Outside that box the
               height model extrapolates; untick "Restrict to Height Model
               coverage" to allow it, and treat the level with suspicion.

Going from grid to WGS84, an approximate level must be supplied and the
ellipsoidal height returned is approximate - schematic note *6.

See README.md for the full derivation, validation figures and caveats,
including the provenance of the 7-parameter set.
"""


# ==========================================================================

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("760x680")
        self.minsize(700, 600)
        try:
            ttk.Style().theme_use("vista")
        except tk.TclError:
            pass

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        nb.add(SinglePointTab(nb), text="  Single point  ")
        nb.add(BatchTab(nb), text="  Batch file  ")
        nb.add(HeightTab(nb), text="  Height (HKPD)  ")

        about = ttk.Frame(nb, padding=12)
        txt = tk.Text(about, font=MONO, wrap="none", background="#f7f7f7")
        txt.insert("1.0", ABOUT)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        nb.add(about, text="  About  ")

        self.status = ttk.Label(self, relief="sunken", anchor="w", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")
        try:
            hm = hk1980.default_height_model()
            self.status.config(
                text="Height model: %d control points, coverage %.3f-%.3f N, %.3f-%.3f E"
                     % (len(hm.points), hm.bounds[0], hm.bounds[1], hm.bounds[2], hm.bounds[3]))
        except Exception as exc:
            self.status.config(text="Height model unavailable: %s" % exc, foreground="#a00")
            messagebox.showwarning(
                APP_TITLE,
                "Could not load hk_height_model.csv, so HKPD levels are unavailable:\n\n%s\n\n"
                "Regenerate it with:  python tools/extract_control_points.py" % exc)


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
