"""Independent validation of hk1980.py.

Three checks, in increasing order of independence:

1. ``hk1980.selftest()`` -- the published reference examples and constants from
   the Explanatory Notes (pages C7-C10) and the datum shift from page B6.

2. The HKPD height model against its own control points, by leave-one-out
   cross-validation.  Fitting and testing on the same 74 points would be
   circular, so each point is predicted from the other 73.

3. The horizontal chain against ``pyproj`` EPSG:2326 (Hong Kong 1980 Grid
   System) -- a completely separate implementation of the same datum and
   projection.  Skipped if pyproj is not installed.

Run:  python validate.py
"""

from __future__ import annotations

import csv
import math
import os
import sys

import hk1980
from hk1980 import default_height_model, wgs84_to_hk1980, hk1980_to_wgs84

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "hk_height_model.csv")


def _stats(values):
    n = len(values)
    return (sum(values) / n,
            math.sqrt(sum(v * v for v in values) / n),
            max(abs(v) for v in values))


def load_points():
    with open(CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check_height_model():
    print("\n" + "=" * 72)
    print("2. HKPD height model, leave-one-out cross-validation")
    print("=" * 72)
    hm = default_height_model()
    s = hm.leave_one_out()
    print("  control points        : %d" % s["n"])
    seps = [p[2] for p in hm.points]
    print("  separation N_sep      : %.3f .. %.3f m" % (min(seps), max(seps)))
    print("  LOO mean error        : %+.4f m" % s["mean"])
    print("  LOO rms error         : %.4f m" % s["rms"])
    print("  LOO max abs error     : %.4f m" % s["max_abs"])
    ok = s["rms"] < 0.05 and s["max_abs"] < 0.15
    print("  -> %s (rms < 5 cm, max < 15 cm)" % ("PASS" if ok else "FAIL"))
    return ok


def check_against_pyproj():
    print("\n" + "=" * 72)
    print("3. Horizontal chain vs pyproj EPSG:2326")
    print("=" * 72)
    try:
        from pyproj import Transformer
    except ImportError:
        print("  pyproj not installed -- skipped")
        return True

    fwd = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    rows = load_points()
    dn, de = [], []
    for r in rows:
        lat, lon = float(r["lat_deg"]), float(r["lon_deg"])
        res = wgs84_to_hk1980(lat, lon, float(r["H_itrf96"]))
        e_ref, n_ref = fwd.transform(lon, lat)
        dn.append(res.N - n_ref)
        de.append(res.E - e_ref)

    for label, vals in (("dN", dn), ("dE", de)):
        mean, rms, mx = _stats(vals)
        print("  %s over %d points   : mean %+.4f m   rms %.4f m   max %.4f m"
              % (label, len(vals), mean, rms, mx))
    ok = max(_stats(dn)[2], _stats(de)[2]) < 0.05
    print("  -> %s (agreement better than 5 cm)" % ("PASS" if ok else "FAIL"))
    print("  Residuals are the published Eq.1/Eq.2 series truncation; pyproj")
    print("  evaluates the full Transverse Mercator series.")
    return ok


def check_round_trip():
    print("\n" + "=" * 72)
    print("4. Full 3-D round trip over the control point set")
    print("=" * 72)
    rows = load_points()
    dlat, dlon, dh = [], [], []
    for r in rows:
        lat, lon, H = float(r["lat_deg"]), float(r["lon_deg"]), float(r["H_itrf96"])
        f = wgs84_to_hk1980(lat, lon, H)
        b = hk1980_to_wgs84(f.N, f.E, f.h_hkpd)
        dlat.append((b.lat - lat) * 111320.0)
        dlon.append((b.lon - lon) * 111320.0 * math.cos(math.radians(lat)))
        dh.append(b.height - H)
    for label, vals in (("north", dlat), ("east", dlon), ("height", dh)):
        mean, rms, mx = _stats(vals)
        print("  %-7s residual      : rms %.6f m   max %.6f m" % (label, rms, mx))
    ok = max(_stats(v)[2] for v in (dlat, dlon, dh)) < 1e-3
    print("  -> %s (closes below 1 mm)" % ("PASS" if ok else "FAIL"))
    return ok


def check_hkpd_recovery():
    print("\n" + "=" * 72)
    print("5. HKPD level recovered at the control points (in-sample)")
    print("=" * 72)
    rows = load_points()
    errs = []
    for r in rows:
        lat, lon = float(r["lat_deg"]), float(r["lon_deg"])
        res = wgs84_to_hk1980(lat, lon, float(r["H_itrf96"]))
        errs.append(res.h_hkpd - float(r["h_hkpd"]))
    mean, rms, mx = _stats(errs)
    print("  h_hkpd error          : mean %+.5f m   rms %.5f m   max %.5f m"
          % (mean, rms, mx))
    print("  (In-sample by construction -- the honest figure is the LOO rms above.)")
    ok = mx < 1e-6
    print("  -> %s (control points reproduced exactly)" % ("PASS" if ok else "FAIL"))
    return ok


def main() -> int:
    print("=" * 72)
    print("1. Published reference examples (Explanatory Notes pages B6, C7-C10)")
    print("=" * 72)
    results = [hk1980.selftest(verbose=True)]
    results.append(check_height_model())
    results.append(check_against_pyproj())
    results.append(check_round_trip())
    results.append(check_hkpd_recovery())

    print("\n" + "=" * 72)
    ok = all(results)
    print("OVERALL: %s" % ("PASS" if ok else "FAIL"))
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
