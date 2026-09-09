# HKProjection — WGS84 ↔ HK1980 Grid, in three dimensions

**English** · [繁體中文](README.zh-HK.md)

Converts between WGS84 / ITRF96 geographic coordinates and Hong Kong 1980 Grid
coordinates, including height, with a desktop GUI, a batch converter for CSV and
shapefiles, and a scriptable Python library.

It implements the full transformation chain published by the Survey and Mapping
Office, Lands Department — not just the horizontal projection — and adds the
piece that general-purpose libraries leave out: conversion to **Hong Kong
Principal Datum (HKPD)** levels.

| | |
|---|---|
| Horizontal accuracy | agrees with `pyproj` EPSG:2326 to **1.4 cm** worst case over 74 control points |
| HKPD level accuracy | **0.023 m rms** by leave-one-out cross-validation |
| Coverage | 22.199 – 22.550 N, 113.853 – 114.372 E |
| Dependencies | none for the core library; tkinter for the GUI, `pyshp` for shapefiles |

---

## Why not just use pyproj?

`pyproj` handles the horizontal transformation well — `EPSG:4326 → EPSG:2326` is
correct and this project agrees with it to about a centimetre. What it cannot do
is heights.

The HK1980 Grid is a two-dimensional projected system. Any height carried
through a `pyproj` pipeline stays an **ellipsoidal** height, because there is no
public EPSG geoid grid for Hong Kong Principal Datum. Ellipsoidal heights are
2–4 m away from the levels on every Hong Kong survey drawing, and the offset
varies across the territory.

This project closes that gap using the published control point data:

```
h_HKPD = H_WGS84 − N_sep(φ, λ)
```

---

## Quick start

Requires Python 3.7 or newer (developed and tested on 3.9).

```bash
git clone https://github.com/Insight-Robotics/HKProjection.git
cd HKProjection
python hkgui.py
```

The core library and the GUI need nothing beyond the standard library. Optional
extras, each needed only for one feature:

```bash
pip install pyshp        # shapefile support in the GUI and batch converter
pip install pyproj       # only for validate.py's independent cross-check
pip install pdfplumber   # only to regenerate the control point CSV from the PDF
```

Sanity-check the installation at any time:

```bash
python hk1980.py selftest     # published reference examples
python validate.py            # full validation suite
```

---

## The GUI

```bash
python hkgui.py
```

Four tabs, built on tkinter:

- **Single point** — type coordinates in either direction and read the answer
  out. Latitude and longitude accept decimal degrees or degrees/minutes/seconds
  in most common spellings: `22.3193`, `22 19 09.5 N`, `22°19'09.5"N`,
  `N22 19 09.5`, `22d19m09.5s`, `114 10 42.80W`. Results show the grid
  coordinates, both heights, the separation, and the intermediate HK80 values.
- **Batch file** — convert a whole CSV or shapefile; see below.
- **Height (HKPD)** — ellipsoidal height ↔ HKPD level at a given position, with
  the position supplied as either WGS84 lat/lon or HK1980 grid N/E.
- **About** — the transformation chain, accuracy figures and caveats.

Every tab has a *Restrict to Height Model coverage* checkbox. Leave it ticked
unless you deliberately want to extrapolate outside Hong Kong.

### Batch conversion

Pick an input file and a direction; everything else is inferred and can be
overridden.

- **CSV** — coordinate and height columns are auto-detected from their headers
  (`lat`/`latitude`/`y`, `northing`, `h`/`height`/`z`/`elevation`, …) and can be
  reassigned from dropdowns. Input columns are preserved and the computed ones
  appended. Rows that fail are still written out, with the reason in a
  `conv_status` column, so nothing disappears silently.
- **Shapefile** — coordinates come from the geometry. Point, polyline and
  polygon layers all work: every vertex is transformed and parts are preserved.
  For point layers the computed values are appended as attributes. Height comes
  from the Z ordinate of a Z-aware layer, or an attribute you pick, or zero. A
  `.prj` is written alongside the output.

Output format follows the input format, and the output path is suggested
automatically. Conversion runs on a background thread with a progress bar, so
large files do not freeze the window.

The same engine is scriptable, with a command line of its own:

```bash
python hkbatch.py in.csv out.csv --to hk1980
python hkbatch.py points.shp out.shp --to wgs84 --height-type hkpd
python hkbatch.py in.csv out.csv --to hk1980 --lat-col Y --lon-col X --height-col ELEV
python hkbatch.py points.shp out.shp --to hk1980 --ignore-z
```

---

## Library usage

```python
from hk1980 import wgs84_to_hk1980, hk1980_to_wgs84, ELLIPSOIDAL, HKPD

r = wgs84_to_hk1980(22.3193, 114.1694, 25.0)   # lat, lon, ellipsoidal height
r.N                   # 820032.934   HK1980 Grid northing, m
r.E                   # 835497.922   HK1980 Grid easting, m
r.h_hkpd              #     27.738   level above Hong Kong Principal Datum, m
r.ellipsoidal_height  #     25.000   above the WGS84 ellipsoid, m
r.separation          #     -2.738   N_sep applied, m
r.hk80_lat, r.hk80_lon, r.hk80_height   # intermediate HK80 datum values

b = hk1980_to_wgs84(820032.934, 835497.922, 27.738)   # N, E, HKPD level
b.lat, b.lon, b.height                                # back to WGS84
```

Both functions take a `height_type` of `ELLIPSOIDAL` or `HKPD` saying which
convention the *input* height is in, and both always report both heights — so
position and height convention convert in a single call. The default is
`ELLIPSOIDAL` going to the grid and `HKPD` coming back, the usual case each way.

```python
wgs84_to_hk1980(22.3193, 114.1694, 27.738, height_type=HKPD)
hk1980_to_wgs84(820032.9, 835497.9, 25.0, height_type=ELLIPSOIDAL)
```

Height conversion on its own, without touching position:

```python
from hk1980 import separation, ellipsoidal_to_hkpd, hkpd_to_ellipsoidal

separation(22.3193, 114.1694)                  # -2.7379 m
ellipsoidal_to_hkpd(22.3193, 114.1694, 25.0)   # 27.738 m
hkpd_to_ellipsoidal(22.3193, 114.1694, 27.738) # 25.000 m
```

Positions outside the height model's coverage raise `ValueError`; pass
`strict=False` to extrapolate anyway.

Lower-level pieces are exposed too — `geodetic_to_cartesian`,
`cartesian_to_geodetic`, `HK80_TO_ITRF96`, `geographic_to_grid`,
`grid_to_geographic`, `meridian_distance`, `footpoint_latitude`, and the
parameter sets `HK1980_GRID`, `UTM_WGS84_49Q/50Q`, `UTM_HK80_49Q/50Q`.

Command line:

```bash
python hk1980.py fwd 22.3193 114.1694 25.0    # WGS84 -> HK1980 Grid + HKPD
python hk1980.py inv 820032.9 835497.9 27.7   # HK1980 Grid + HKPD -> WGS84
python hk1980.py selftest
```

---

## The algorithm

The chain drawn in `Reference/SchematicDiagram.pdf`:

```
WGS84 / ITRF96 geographic   φ, λ, H            [WGS84 ellipsoid]
        │
        │  ①  geodetic → cartesian
        │      X = (ν + H) cos φ cos λ
        │      Y = (ν + H) cos φ sin λ
        │      Z = ((1 − e²) ν + H) sin φ
        ▼
WGS84 cartesian             X, Y, Z
        │
        │  ②  7-parameter Helmert  (7P_ITRF96_HK80_V1.0, inverse sense)
        │      ⎡X⎤      ⎡ΔX⎤   ⎡(1+S)   θz    −θy ⎤ ⎡X⎤
        │      ⎢Y⎥    = ⎢ΔY⎥ + ⎢ −θz   (1+S)   θx ⎥ ⎢Y⎥
        │      ⎣Z⎦_HK80 ⎣ΔZ⎦   ⎣  θy    −θx  (1+S)⎦ ⎣Z⎦_WGS84
        ▼
HK80 cartesian              X, Y, Z
        │
        │  ③  cartesian → geodetic   (φ implicit, solved by iteration)
        │      tan λ = Y / X
        │      tan φ = (Z + e² ν sin φ) / √(X² + Y²)
        │      H     = X sec λ sec φ − ν
        ▼
HK80 geographic             φ, λ, H            [International 1910 ellipsoid]
        │
        │  ④  Transverse Mercator, Eq.1 – Eq.3
        │      N = N₀ + m₀{ (M − M₀) + ν sin φ (Δλ²/2) cos φ }
        │      E = E₀ + m₀{ ν Δλ cos φ + ν cos³φ (ψ − t²) Δλ³/6 }
        ▼
HK1980 Grid                 N, E
```

The reverse is the same chain upward, using Eq.4 and Eq.5 for the projection and
the exact inverse of the Helmert matrix for the datum shift.

### The third dimension

The HK1980 Grid has no height component. The chain does carry a height through,
but the `H` that falls out at the HK80 end is an **ellipsoidal height on the
International 1910 ellipsoid** — a by-product of the datum shift, not a level
anyone surveys to. It is reported as `hk80_height` and should not be used as an
elevation.

The usable third dimension is the **Hong Kong Principal Datum (HKPD)** level,
which needs a separation model rather than a datum transformation. `N_sep` is
interpolated from the 74 control points of the *Height Model of Hong Kong
(Version 1.0)*, extracted from the source PDF into `hk_height_model.csv`. Each
point gives an ITRF96 ellipsoidal height against a levelled HKPD height. The
separation runs from **−4.06 m** in the south-west (Lantau) to **−2.08 m** in
the east.

It is modelled as a least-squares quadratic trend surface in (Δλ, Δφ) plus
inverse-distance-weighted interpolation of the residuals over the 10 nearest
control points.

---

## Validation

`python validate.py` — all checks pass.

**Published reference examples** (Explanatory Notes page C10), HK1980 Grid:

| | computed | published | Δ |
|---|---|---|---|
| φ,λ → N | 832699.106 | 832699 | +0.11 m |
| φ,λ → E | 836055.198 | 836055 | +0.20 m |
| N,E → φ | 22°26′06.7565″ | 22°26′06.76″ | −0.004″ |
| N,E → λ | 114°10′20.4531″ | 114°10′20.45″ | +0.003″ |

**Derived constants** reproduce page C10 to the printed precision: ν, ρ, ψ on
both ellipsoids, both e² values, and M₀ = 2 468 395.728 m from Eq.3 to 0.2 mm.

**Datum shift** reproduces page B6 (dφ = +5.5″, dλ = −8.8″, each ±0.1″) as
+5.511″, −8.831″.

**Independent cross-check** against `pyproj` EPSG:2326 over all 74 control
points — a separate implementation of the same datum and projection:

```
dN   mean -0.002 m   rms 0.003 m   max 0.008 m
dE   mean +0.004 m   rms 0.005 m   max 0.014 m
```

**HKPD height model**, leave-one-out cross-validation over the 74 points (each
predicted from the other 73):

```
mean +0.002 m   rms 0.023 m   max 0.061 m
```

**Round trip** over all control points closes below 1 µm in all three
components.

---

## Accuracy and caveats

- **Horizontal, WGS84 → HK1980 Grid** — limited by the datum transformation, not
  by this code. Page C4 of the Explanatory Notes quotes 0.2″ / 5 m for datum
  transformation generally; the 7-parameter set does considerably better in
  practice. The cm-level differences against pyproj above come from the
  published Eq.1/Eq.2 series being truncated at Δλ³.

- **HKPD level** — ~2.3 cm rms by cross-validation, valid only inside the
  control point coverage (22.199–22.550 N, 113.853–114.372 E). The Height Model
  is version 1.0 (2006); note 6 of that document warns the ellipsoidal heights
  were due for re-survey, so the separations may since have been revised.

- **Reverse direction** — per note \*6 of the schematic, an approximate level
  must be supplied going upward, and the ellipsoidal height returned is only as
  good as the level supplied plus the separation model.

- **7-parameter provenance** — the schematic names the parameter document
  ("Geodetic Datum Transformation and Map Projection Parameter Set for
  Computation between ITRF96 Geodetic Coordinates … and HK1980 Grid
  Coordinates") but that PDF is **not included here**. The values in
  `HK80_TO_ITRF96` are the published `7P_ITRF96_HK80_V1.0` set; they are
  validated against the independent page B6 figure and against EPSG:1825, which
  they match exactly. Confirm them against the Lands Department document before
  survey use.

- **Direction of the parameter set** — fed to the matrix exactly as drawn on the
  schematic, these values transform **HK80 → WGS84** (schematic note \*3). The
  note \*2 direction is obtained by inversion. This is the coordinate-frame
  (EPSG 9607) rotation convention; the sign pattern is transposed relative to the
  more common position-vector convention, so do not hand these rotations to a
  library expecting EPSG 9606 without flipping their signs.

- **UTM** — the UTM parameter sets are included for completeness, but Eq.1–Eq.5
  are truncated series and Hong Kong lies 2.8° off the 117°E central meridian of
  zone 50Q, where the omitted Δλ⁴ term is worth ~2.3 m of northing. The C10 UTM
  reference examples are themselves internally inconsistent by about 2 m — the
  forward example prints 2 483 566 N while the inverse example feeds
  2 483 568 N for nominally the same point — so no faithful implementation of
  the published series can match both. Checked against a high-order Transverse
  Mercator, the easting computed here is correct to 2 cm and the printed
  209 194 is the outlier. **For UTM work, use a full TM implementation.** The
  HK1980 Grid, whose central meridian runs through the territory, is unaffected:
  forward-then-inverse closes to 1.6 mm at worst over a box wider than Hong
  Kong, and exactly with `grid_to_geographic(..., exact=True)`.

---

## Files

| Path | |
|---|---|
| `hkgui.py` | desktop GUI — start here |
| `hk1980.py` | conversion library, self-contained, standard library only |
| `hkbatch.py` | CSV / shapefile batch engine, with its own CLI |
| `hk_height_model.csv` | 74 control points, extracted from the source PDF |
| `validate.py` | full validation suite |
| `tools/extract_control_points.py` | regenerates the CSV from the source PDF |
| `Reference/` | the Lands Department source documents |
| `Reference/notes_extracted.txt` | text dump of the Explanatory Notes, for searching |

---

## Sources

All formulae, parameters and control point data come from publications of the
Survey and Mapping Office, Lands Department, Government of the Hong Kong SAR,
included in `Reference/`:

- *Explanatory Notes on Geodetic Datums in Hong Kong* (1995, minor revision
  2018) — Eq.1 to Eq.5, projection parameters, reference examples, datum shift.
- *Schematic Diagram Showing Transformation of Coordinates between WGS84
  Geographic Coordinates and HK 1980 Grid Coordinates* — the transformation
  chain.
- *Control Points for Height Model of Hong Kong (Version 1.0)* (2006) — the 74
  control points behind the HKPD separation model.

Those documents are © the Government of the Hong Kong SAR and are reproduced
here for reference only. This project is an independent implementation and is
not endorsed by the Lands Department. **Verify against official sources before
using it for survey, statutory or safety-critical work.**
