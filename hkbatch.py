"""Batch conversion between WGS84 and the HK1980 Grid, for CSV and shapefiles.

The conversion itself lives in :mod:`hk1980`; this module is the file-handling
layer around it, kept separate from the GUI so it can be tested and scripted on
its own.

    python hkbatch.py in.csv out.csv --to hk1980
    python hkbatch.py points.shp out.shp --to wgs84

Shapefile support needs ``pyshp`` (``import shapefile``); CSV support does not.
"""

from __future__ import annotations

import csv
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import hk1980
from hk1980 import ELLIPSOIDAL, HKPD, hk1980_to_wgs84, wgs84_to_hk1980

TO_HK1980 = "hk1980"
TO_WGS84 = "wgs84"

#: Height conventions, re-exported from :mod:`hk1980` for convenience.
HEIGHT_TYPES = (ELLIPSOIDAL, HKPD)

#: Column name candidates, lower-cased, in priority order.
LAT_NAMES = ("lat", "latitude", "phi", "wgs84_lat", "y", "lat_deg", "緯度")
LON_NAMES = ("lon", "long", "longitude", "lambda", "wgs84_lon", "x", "lon_deg", "經度")
NORTH_NAMES = ("n", "northing", "north", "hk1980_n", "hk_n", "y", "北")
EAST_NAMES = ("e", "easting", "east", "hk1980_e", "hk_e", "x", "東")
HEIGHT_NAMES = ("h", "height", "z", "elev", "elevation", "ellipsoidal_height",
                "h_itrf96", "hkpd", "hkpd_level", "level", "高程")

NO_HEIGHT = "<none / 0>"


# --------------------------------------------------------------------------
# angle parsing
# --------------------------------------------------------------------------

_HEMI_HEAD = re.compile(r"^\s*([NSEWnsew])\s*")
_HEMI_TAIL = re.compile(r"\s*([NSEWnsew])\s*$")
_SEPARATORS = "°º′″’”'\"dDmMsS:,´"


def parse_angle(text) -> float:
    """Parse a latitude or longitude in decimal degrees or degrees/minutes/seconds.

    Accepts, among others::

        22.3193            22 19 09.5          22 19 09.5 N
        22°19'09.5"N       N22 19 09.5         22d19m09.5s
        -114.1694          114:10:42.80 E      114 10 42.80W

    A hemisphere letter of S or W, or a leading minus sign, makes the result
    negative.  Raises ``ValueError`` on anything it cannot make sense of.
    """
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s:
        raise ValueError("empty value")

    # Letter unit markers first, so that the trailing "s" of "22d19m09.5s" is
    # read as seconds rather than as a South hemisphere letter.  A trailing s is
    # only treated as a unit if the value also uses a d or m marker; that keeps
    # "22 19 09.5S" meaning 22 degrees south.
    lettered = re.search(r"\d\s*[dDmM]\s*\d", s) is not None
    s = re.sub(r"(?<=\d)\s*[dD](?=\s*\d)", " ", s)
    s = re.sub(r"(?<=\d)\s*[mM](?=\s*\d)", " ", s)
    if lettered:
        s = re.sub(r"(?<=\d)\s*[sS]\s*$", "", s)

    hemi = ""
    m = _HEMI_HEAD.match(s)
    if m:
        hemi = m.group(1).upper()
        s = s[m.end():]
    m = _HEMI_TAIL.search(s)
    if m:
        if hemi:
            raise ValueError("two hemisphere letters in %r" % (text,))
        hemi = m.group(1).upper()
        s = s[: m.start()]

    s = s.strip()
    negative = s.startswith("-")
    if negative:
        s = s[1:]

    for ch in _SEPARATORS:
        s = s.replace(ch, " ")
    parts = s.split()
    if not parts or len(parts) > 3:
        raise ValueError("cannot parse angle %r" % (text,))
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError("cannot parse angle %r" % (text,))

    if len(nums) > 1 and any(n < 0 for n in nums[1:]):
        raise ValueError("minutes and seconds must not be negative in %r" % (text,))
    if len(nums) > 1 and nums[1] >= 60:
        raise ValueError("minutes out of range in %r" % (text,))
    if len(nums) > 2 and nums[2] >= 60:
        raise ValueError("seconds out of range in %r" % (text,))

    value = nums[0] + (nums[1] / 60.0 if len(nums) > 1 else 0.0) \
        + (nums[2] / 3600.0 if len(nums) > 2 else 0.0)

    if negative or hemi in ("S", "W"):
        value = -value
    return value


def parse_number(text) -> float:
    """Parse a plain number (metres), tolerating thousands separators and blanks."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(",", "").replace("_", "")
    if not s:
        raise ValueError("empty value")
    try:
        return float(s)
    except ValueError:
        raise ValueError("cannot parse number %r" % (text,))


# --------------------------------------------------------------------------
# result bookkeeping
# --------------------------------------------------------------------------

class Report(object):
    """Outcome of a batch run."""

    def __init__(self) -> None:
        self.total = 0
        self.converted = 0
        self.failed = 0
        self.errors: List[Tuple[int, str]] = []   # (1-based row/feature, message)
        self.output_path: Optional[str] = None
        self.messages: List[str] = []

    def fail(self, index: int, message: str) -> None:
        self.failed += 1
        if len(self.errors) < 200:
            self.errors.append((index, message))

    def note(self, message: str) -> None:
        self.messages.append(message)

    def summary(self) -> str:
        bits = ["%d of %d converted" % (self.converted, self.total)]
        if self.failed:
            bits.append("%d failed" % self.failed)
        return ", ".join(bits)


ProgressFn = Optional[Callable[[int, int], None]]


# --------------------------------------------------------------------------
# the per-point conversion
# --------------------------------------------------------------------------

def convert_point(a: float, b: float, height: float, direction: str,
                  strict: bool, height_type: str = ELLIPSOIDAL) -> Dict[str, float]:
    """Convert one point.  ``a``/``b`` are lat/lon or northing/easting.

    ``height_type`` declares the convention of the *input* height; both the
    ellipsoidal height and the HKPD level are always returned.
    """
    if direction == TO_HK1980:
        r = wgs84_to_hk1980(a, b, height, strict=strict, height_type=height_type)
        return {
            "hk1980_N": r.N,
            "hk1980_E": r.E,
            "hkpd_level": r.h_hkpd,
            "ellip_h": r.ellipsoidal_height,
            "separation": r.separation,
            "hk80_lat": r.hk80_lat,
            "hk80_lon": r.hk80_lon,
        }
    r = hk1980_to_wgs84(a, b, height, strict=strict, height_type=height_type)
    return {
        "wgs84_lat": r.lat,
        "wgs84_lon": r.lon,
        "ellip_h": r.ellipsoidal_height,
        "hkpd_level": r.h_hkpd,
        "separation": r.separation,
    }


def output_fields(direction: str) -> List[str]:
    """Names of the computed columns, all <= 10 characters for dBase."""
    if direction == TO_HK1980:
        return ["hk1980_N", "hk1980_E", "hkpd_level", "ellip_h",
                "separation", "hk80_lat", "hk80_lon"]
    return ["wgs84_lat", "wgs84_lon", "ellip_h", "hkpd_level", "separation"]


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def sniff_csv(path: str, sample_rows: int = 5) -> Tuple[List[str], List[List[str]]]:
    """Return (header, first few data rows) for previewing and column mapping."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        try:
            dialect = csv.Sniffer().sniff(fh.read(8192)) or csv.excel
        except csv.Error:
            dialect = csv.excel
        fh.seek(0)
        reader = csv.reader(fh, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return [], []
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= sample_rows:
                break
    return header, rows


def guess_column(header: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    """Pick the header entry that best matches ``candidates``."""
    lowered = {h.strip().lower(): h for h in header}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    # Substring fallback, longest candidate first so "latitude" beats "lat".
    for cand in sorted(candidates, key=len, reverse=True):
        for low, original in lowered.items():
            if cand in low:
                return original
    return None


def guess_mapping(header: Sequence[str], direction: str) -> Dict[str, Optional[str]]:
    """Auto-detect which columns hold the coordinates."""
    if direction == TO_HK1980:
        a = guess_column(header, LAT_NAMES)
        b = guess_column(header, LON_NAMES)
    else:
        a = guess_column(header, NORTH_NAMES)
        b = guess_column(header, EAST_NAMES)
    h = guess_column(header, HEIGHT_NAMES)
    if h is not None and h in (a, b):
        h = None
    return {"a": a, "b": b, "height": h}


def convert_csv(in_path: str, out_path: str, direction: str,
                mapping: Dict[str, Optional[str]], strict: bool = True,
                progress: ProgressFn = None,
                height_type: str = ELLIPSOIDAL) -> Report:
    """Convert a CSV, appending the computed columns to each row.

    ``mapping`` names the input columns: ``a`` (latitude or northing), ``b``
    (longitude or easting) and optionally ``height``.  Rows that cannot be
    converted are still written out, with the reason in ``conv_status``.
    """
    rep = Report()
    col_a, col_b = mapping.get("a"), mapping.get("b")
    col_h = mapping.get("height")
    if col_h == NO_HEIGHT:
        col_h = None
    if not col_a or not col_b:
        raise ValueError("both coordinate columns must be chosen")

    with open(in_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    rep.total = len(rows)

    missing = [c for c in (col_a, col_b, col_h) if c and rows and c not in rows[0]]
    if missing:
        raise ValueError("column(s) not found in file: %s" % ", ".join(missing))

    new_cols = output_fields(direction) + ["conv_status"]
    in_cols = list(rows[0].keys()) if rows else []
    fieldnames = in_cols + [c for c in new_cols if c not in in_cols]

    parse_a = parse_angle if direction == TO_HK1980 else parse_number
    parse_b = parse_angle if direction == TO_HK1980 else parse_number

    out_rows = []
    for i, row in enumerate(rows, 1):
        record = dict(row)
        try:
            a = parse_a(row[col_a])
            b = parse_b(row[col_b])
            h = parse_number(row[col_h]) if col_h and str(row[col_h]).strip() else 0.0
            record.update(convert_point(a, b, h, direction, strict, height_type))
            record["conv_status"] = "ok"
            rep.converted += 1
        except Exception as exc:
            record["conv_status"] = str(exc)
            for c in output_fields(direction):
                record.setdefault(c, "")
            rep.fail(i, str(exc))
        out_rows.append(record)
        if progress and (i % 200 == 0 or i == rep.total):
            progress(i, rep.total)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in out_rows:
            writer.writerow(record)

    rep.output_path = out_path
    return rep


# --------------------------------------------------------------------------
# shapefile
# --------------------------------------------------------------------------

#: Minimal but valid .prj content for the two output systems.
PRJ_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
PRJ_HK1980 = (
    'PROJCS["Hong_Kong_1980_Grid_System",'
    'GEOGCS["GCS_Hong_Kong_1980",DATUM["D_Hong_Kong_1980",'
    'SPHEROID["International_1924",6378388.0,297.0]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",836694.05],'
    'PARAMETER["False_Northing",819069.8],'
    'PARAMETER["Central_Meridian",114.1785555555556],'
    'PARAMETER["Scale_Factor",1.0],'
    'PARAMETER["Latitude_Of_Origin",22.31213333333333],'
    'UNIT["Meter",1.0]]'
)

_POINT_TYPES = (1, 11, 21)
_Z_TYPES = (11, 13, 15, 18)


def _require_pyshp():
    try:
        import shapefile  # noqa: F401
    except ImportError:
        raise ImportError(
            "shapefile support needs pyshp -- install it with 'pip install pyshp'")
    return __import__("shapefile")


def describe_shapefile(path: str) -> Dict[str, object]:
    """Peek at a shapefile: geometry type, feature count, fields, .prj text."""
    sf = _require_pyshp()
    with sf.Reader(path) as r:
        info = {
            "shape_type": r.shapeType,
            "shape_type_name": sf.SHAPETYPE_LOOKUP.get(r.shapeType, str(r.shapeType)),
            "count": len(r),
            "fields": [f[0] for f in r.fields if f[0] != "DeletionFlag"],
            "is_point": r.shapeType in _POINT_TYPES,
            "has_z": r.shapeType in _Z_TYPES,
        }
    prj = os.path.splitext(path)[0] + ".prj"
    info["prj"] = ""
    if os.path.exists(prj):
        try:
            with open(prj, "r", encoding="utf-8", errors="replace") as fh:
                info["prj"] = fh.read().strip()
        except OSError:
            pass
    return info


def convert_shapefile(in_path: str, out_path: str, direction: str,
                      strict: bool = True, height_field: Optional[str] = None,
                      progress: ProgressFn = None,
                      height_type: str = ELLIPSOIDAL, use_z: bool = True) -> Report:
    """Convert a shapefile of any geometry type.

    Every vertex is transformed, so point, polyline and polygon layers all work
    and keep their parts and attributes.  For point layers the computed values
    are appended as new attribute fields; for line and polygon layers only the
    geometry is transformed, since the outputs are per-vertex.

    Height source, in order of preference: ``height_field`` if given, then the Z
    ordinate for a PointZ/PolyLineZ/PolygonZ layer when ``use_z`` is set, then 0.
    Pass ``use_z=False`` to ignore a Z ordinate that is present but meaningless.
    """
    sf = _require_pyshp()
    rep = Report()

    with sf.Reader(in_path) as reader:
        shape_type = reader.shapeType
        is_point = shape_type in _POINT_TYPES
        has_z = shape_type in _Z_TYPES
        read_z = has_z and use_z and not height_field
        src_fields = [f for f in reader.fields if f[0] != "DeletionFlag"]
        names = [f[0] for f in src_fields]
        if height_field and height_field not in names:
            raise ValueError("height field %r not in shapefile" % height_field)

        rep.total = len(reader)
        rep.note("input: %s, %d features" %
                 (sf.SHAPETYPE_LOOKUP.get(shape_type, shape_type), rep.total))
        if not is_point:
            rep.note("non-point layer: geometry transformed, no attributes added")

        new_names: List[str] = []
        with sf.Writer(out_path, shapeType=shape_type) as writer:
            for f in src_fields:
                writer.field(*f)
            if is_point:
                for name in output_fields(direction):
                    trimmed = name[:10]  # dBase field names cap at 10 characters
                    new_names.append(trimmed)
                    writer.field(trimmed, "N", 24, 8)
                writer.field("CONV_STAT", "C", 80)

            for i, sr in enumerate(reader.iterShapeRecords(), 1):
                shape, record = sr.shape, sr.record
                values = list(record)

                height = 0.0
                if height_field:
                    try:
                        height = parse_number(record[height_field])
                    except Exception:
                        height = 0.0

                status = "ok"
                computed: Dict[str, float] = {}
                pts = list(getattr(shape, "points", []))
                zs = list(getattr(shape, "z", [])) if has_z else []

                new_pts = []
                new_zs = []
                for j, (x, y) in enumerate(pts):
                    # Shapefiles store x first: lon/easting, then lat/northing.
                    h = height
                    if read_z and j < len(zs):
                        h = zs[j]
                    try:
                        out = convert_point(y, x, h, direction, strict, height_type)
                        if direction == TO_HK1980:
                            new_pts.append((out["hk1980_E"], out["hk1980_N"]))
                            new_zs.append(out["hkpd_level"])
                        else:
                            new_pts.append((out["wgs84_lon"], out["wgs84_lat"]))
                            new_zs.append(out["ellip_h"])
                        if j == 0:
                            computed = out
                    except Exception as exc:
                        status = str(exc)
                        new_pts.append((x, y))
                        new_zs.append(h)

                if status == "ok":
                    rep.converted += 1
                else:
                    rep.fail(i, status)

                shape.points = new_pts
                if has_z:
                    shape.z = new_zs
                writer.shape(shape)

                if is_point:
                    for name in output_fields(direction):
                        values.append(computed.get(name, 0.0) if computed else 0.0)
                    values.append(status[:80])
                writer.record(*values)

                if progress and (i % 200 == 0 or i == rep.total):
                    progress(i, rep.total)

    prj_path = os.path.splitext(out_path)[0] + ".prj"
    with open(prj_path, "w", encoding="utf-8") as fh:
        fh.write(PRJ_HK1980 if direction == TO_HK1980 else PRJ_WGS84)
    rep.note("wrote %s" % os.path.basename(prj_path))

    rep.output_path = out_path
    return rep


# --------------------------------------------------------------------------
# dispatch + CLI
# --------------------------------------------------------------------------

def convert_file(in_path: str, out_path: str, direction: str,
                 mapping: Optional[Dict[str, Optional[str]]] = None,
                 strict: bool = True, height_field: Optional[str] = None,
                 progress: ProgressFn = None,
                 height_type: str = ELLIPSOIDAL, use_z: bool = True) -> Report:
    """Convert a CSV or shapefile, chosen by the input file's extension."""
    ext = os.path.splitext(in_path)[1].lower()
    if ext == ".shp":
        return convert_shapefile(in_path, out_path, direction, strict,
                                 height_field, progress, height_type, use_z)
    if mapping is None:
        header, _ = sniff_csv(in_path)
        mapping = guess_mapping(header, direction)
    return convert_csv(in_path, out_path, direction, mapping, strict, progress,
                       height_type)


def _main(argv=None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="hkbatch",
        description="Batch WGS84 <-> HK1980 Grid conversion for CSV and shapefiles.")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--to", choices=(TO_HK1980, TO_WGS84), default=TO_HK1980,
                   help="target system (default: hk1980)")
    p.add_argument("--lat-col", help="CSV column holding latitude or northing")
    p.add_argument("--lon-col", help="CSV column holding longitude or easting")
    p.add_argument("--height-col", help="CSV column holding height")
    p.add_argument("--height-field", help="shapefile attribute holding height")
    p.add_argument("--ignore-z", action="store_true",
                   help="ignore the Z ordinate of a Z-aware shapefile")
    p.add_argument("--height-type", choices=HEIGHT_TYPES, default=None,
                   help="convention of the input height (default: ellipsoidal "
                        "when converting to hk1980, hkpd when converting to wgs84)")
    p.add_argument("--loose", action="store_true",
                   help="allow extrapolation outside the height model coverage")
    args = p.parse_args(argv)

    mapping = None
    if args.lat_col or args.lon_col or args.height_col:
        mapping = {"a": args.lat_col, "b": args.lon_col, "height": args.height_col}

    def show(done, total):
        print("\r  %d / %d" % (done, total), end="", flush=True)

    try:
        height_type = args.height_type or (
            ELLIPSOIDAL if args.to == TO_HK1980 else HKPD)
        rep = convert_file(args.input, args.output, args.to, mapping,
                           strict=not args.loose, height_field=args.height_field,
                           progress=show, height_type=height_type,
                           use_z=not args.ignore_z)
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    print("\r" + " " * 30 + "\r", end="")
    for m in rep.messages:
        print("  %s" % m)
    print(rep.summary())
    for idx, msg in rep.errors[:10]:
        print("  row %d: %s" % (idx, msg))
    if len(rep.errors) > 10:
        print("  ... and %d more" % (len(rep.errors) - 10))
    print("wrote %s" % rep.output_path)
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
