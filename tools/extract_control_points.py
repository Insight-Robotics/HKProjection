"""Extract the Height Model control point table from ControlPointsofHKHeightModelv1.0.pdf.

Writes hk_height_model.csv with columns:
    point, group, lat_deg, lon_deg, H_itrf96, h_hkpd, N_sep

N_sep = H - h  is the separation between the ITRF96 ellipsoid and Hong Kong
Principal Datum at the point -- this is the data behind the "WGS84 and HKPD
Height Difference Contour Map" on page C6 of the Explanatory Notes.

Run from the project root:  python tools/extract_control_points.py
"""

import csv
import os
import re

import pdfplumber

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(HERE, "ControlPointsofHKHeightModelv1.0.pdf")
OUT = os.path.join(HERE, "hk_height_model.csv")

# The group column is printed only once per block, on the row that happens to be
# vertically centred in it, so it cannot be carried forward from the text alone.
# Blocks are contiguous in table order; these are the first point of each.
GROUP_STARTS = {"141": "I", "72": "II", "236": "III", "75": "IV"}
GROUP_COUNTS = {"I": 35, "II": 23, "III": 11, "IV": 5}

# point  [group]  lat d m s  lon d m s  H  h
ROW = re.compile(
    r"^(?P<point>[A-Za-z0-9.]+)\s+"
    r"(?:(?P<group>I{1,3}V?|IV)\s+)?"
    r"(?P<lat_d>\d{1,3})\s+(?P<lat_m>\d{1,2})\s+(?P<lat_s>\d{1,2}\.\d+)\s+"
    r"(?P<lon_d>\d{1,3})\s+(?P<lon_m>\d{1,2})\s+(?P<lon_s>\d{1,2}\.\d+)\s+"
    r"(?P<H>-?\d+\.\d+)\s+(?P<h>-?\d+\.\d+)\s*$"
)


def dms(d, m, s):
    return int(d) + int(m) / 60.0 + float(s) / 3600.0


def main():
    rows = []
    group = ""
    with pdfplumber.open(PDF) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                m = ROW.match(line.strip())
                if not m:
                    continue
                group = GROUP_STARTS.get(m.group("point"), group)
                H = float(m.group("H"))
                h = float(m.group("h"))
                rows.append(
                    {
                        "point": m.group("point"),
                        "group": group,
                        "lat_deg": round(dms(m.group("lat_d"), m.group("lat_m"), m.group("lat_s")), 9),
                        "lon_deg": round(dms(m.group("lon_d"), m.group("lon_m"), m.group("lon_s")), 9),
                        "H_itrf96": H,
                        "h_hkpd": h,
                        "N_sep": round(H - h, 4),
                    }
                )

    from collections import Counter

    got = Counter(r["group"] for r in rows)
    assert got == Counter(GROUP_COUNTS), "group split %r != expected %r" % (dict(got), GROUP_COUNTS)
    assert len(set(r["point"] for r in rows)) == len(rows), "duplicate point numbers"

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    seps = [r["N_sep"] for r in rows]
    print("wrote %d points -> %s" % (len(rows), OUT))
    print("N_sep range: %.3f .. %.3f m" % (min(seps), max(seps)))
    print("lat range:   %.5f .. %.5f" % (min(r["lat_deg"] for r in rows), max(r["lat_deg"] for r in rows)))
    print("lon range:   %.5f .. %.5f" % (min(r["lon_deg"] for r in rows), max(r["lon_deg"] for r in rows)))


if __name__ == "__main__":
    main()
