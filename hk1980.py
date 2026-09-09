"""3-D coordinate conversion between WGS84 and the Hong Kong 1980 Grid.

Implements, in full, the chain drawn in ``SchematicDiagram.pdf``:

    WGS84 geographic  (phi, lambda, H)          [WGS84 ellipsoid]
        |  (1)  geodetic -> cartesian
    WGS84 cartesian   (X, Y, Z)
        |  (2)  7-parameter Helmert, ITRF96 -> HK80
    HK80 cartesian    (X, Y, Z)
        |  (3)  cartesian -> geodetic
    HK80 geographic   (phi, lambda, H)          [International 1910 ellipsoid]
        |  (4)  Transverse Mercator, Eq.1 - Eq.3
    HK1980 Grid       (N, E)

and the exact reverse.  Every step is invertible, so ``wgs84_to_hk1980`` and
``hk1980_to_wgs84`` round-trip to numerical precision.

The projection formulae (Eq.1 - Eq.5), the projection parameter sets and the
reference examples used by the self-test all come from "Explanatory Notes on
Geodetic Datums in Hong Kong" (Survey and Mapping Office, Lands Department,
1995, minor revision 2018), pages C7 - C10.

THE THIRD DIMENSION
-------------------
The HK1980 Grid is a 2-D projected system: it has no height component.  The
chain above does carry a height through, but the ``H`` that falls out at the
HK80 end is an *ellipsoidal* height on the International 1910 ellipsoid -- a
mathematical by-product of the datum shift, not a level anyone surveys to.  The
usable third dimension in Hong Kong is the **Hong Kong Principal Datum (HKPD)**
level, and getting to it needs a separation model, not a datum transformation.

This module therefore reports both, and keeps them clearly apart:

    h_hkpd = H_wgs84 - N_sep(phi, lambda)

``N_sep`` is interpolated from the 74 published control points of the "Height
Model of Hong Kong (Version 1.0)" (see ``hk_height_model.csv``), which tabulate
ITRF96 ellipsoidal height against HKPD level.  This is the part that ``pyproj``
alone will not give you: there is no public EPSG geoid grid for HKPD, so a
pyproj pipeline can only ever hand back an ellipsoidal height.

Per note *6 of the schematic, the reverse direction needs an approximate level
as input, and the ellipsoidal height it returns is only approximate.

No third-party dependencies; standard library only.
"""

from __future__ import annotations

import csv
import math
import os
from typing import Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "dms",
    "to_dms",
    "Ellipsoid",
    "WGS84",
    "INTERNATIONAL_1910",
    "Helmert7",
    "HK80_TO_ITRF96",
    "TMParams",
    "HK1980_GRID",
    "UTM_WGS84_49Q",
    "UTM_WGS84_50Q",
    "UTM_HK80_49Q",
    "UTM_HK80_50Q",
    "geodetic_to_cartesian",
    "cartesian_to_geodetic",
    "geographic_to_grid",
    "grid_to_geographic",
    "meridian_distance",
    "footpoint_latitude",
    "wgs84_to_hk80_geographic",
    "hk80_to_wgs84_geographic",
    "wgs84_to_hk1980",
    "hk1980_to_wgs84",
    "HeightModel",
    "default_height_model",
    "ELLIPSOIDAL",
    "HKPD",
    "separation",
    "ellipsoidal_to_hkpd",
    "hkpd_to_ellipsoidal",
]

#: Height convention tags, for the ``height_type`` arguments below.
ELLIPSOIDAL = "ellipsoidal"   # above the WGS84 ellipsoid
HKPD = "hkpd"                 # above Hong Kong Principal Datum

_HERE = os.path.dirname(os.path.abspath(__file__))
_HEIGHT_MODEL_CSV = os.path.join(_HERE, "hk_height_model.csv")

_ARCSEC = math.pi / (180.0 * 3600.0)


# --------------------------------------------------------------------------
# angle helpers
# --------------------------------------------------------------------------

def dms(degrees: float, minutes: float = 0.0, seconds: float = 0.0) -> float:
    """Degrees/minutes/seconds -> decimal degrees.

    The sign of ``degrees`` carries the whole value, so
    ``dms(-114, 10, 42.80)`` is -114.178555...
    """
    sign = -1.0 if (degrees < 0 or math.copysign(1.0, degrees) < 0) else 1.0
    return sign * (abs(degrees) + minutes / 60.0 + seconds / 3600.0)


def to_dms(decimal_degrees: float, precision: int = 5) -> Tuple[int, int, float]:
    """Decimal degrees -> (degrees, minutes, seconds), sign on the degrees."""
    sign = -1 if decimal_degrees < 0 else 1
    total = abs(decimal_degrees)
    d = int(total)
    rem = (total - d) * 60.0
    m = int(rem)
    s = round((rem - m) * 60.0, precision)
    if s >= 60.0:  # rounding carried
        s -= 60.0
        m += 1
    if m >= 60:
        m -= 60
        d += 1
    return sign * d, m, s


# --------------------------------------------------------------------------
# reference ellipsoids
# --------------------------------------------------------------------------

class Ellipsoid(object):
    """A reference ellipsoid, defined by semi-major axis and flattening.

    Notation follows page C8 of the Explanatory Notes: ``nu`` is the radius of
    curvature in the prime vertical, ``rho`` the radius of curvature in the
    meridian, ``psi = nu / rho`` the isometric latitude.
    """

    __slots__ = ("name", "a", "f", "e2", "b")

    def __init__(self, name: str, a: float, f: float) -> None:
        self.name = name
        self.a = a
        self.f = f
        self.e2 = 2.0 * f - f * f          # first eccentricity squared, page C8
        self.b = a * (1.0 - f)

    def nu(self, lat_rad: float) -> float:
        """Radius of curvature in the prime vertical."""
        return self.a / math.sqrt(1.0 - self.e2 * math.sin(lat_rad) ** 2)

    def rho(self, lat_rad: float) -> float:
        """Radius of curvature in the meridian."""
        return self.a * (1.0 - self.e2) / math.sqrt((1.0 - self.e2 * math.sin(lat_rad) ** 2) ** 3)

    def psi(self, lat_rad: float) -> float:
        """Isometric latitude nu / rho."""
        return self.nu(lat_rad) / self.rho(lat_rad)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "Ellipsoid(%r, a=%.4f, 1/f=%.10f)" % (self.name, self.a, 1.0 / self.f)


#: WGS84 ellipsoid.  Flattening as printed on page B3 of the Explanatory Notes.
WGS84 = Ellipsoid("WGS84", 6378137.0, 1.0 / 298.2572235634)

#: International (Hayford) 1910 ellipsoid -- the HK80 datum ellipsoid, page B3.
INTERNATIONAL_1910 = Ellipsoid("International 1910", 6378388.0, 1.0 / 297.0)


# --------------------------------------------------------------------------
# step 1 / 3 : geodetic <-> cartesian
# --------------------------------------------------------------------------

def geodetic_to_cartesian(lat_deg: float, lon_deg: float, height: float,
                          ellipsoid: Ellipsoid) -> Tuple[float, float, float]:
    """(phi, lambda, H) -> (X, Y, Z), the boxed formulae *1 / *4 of the schematic.

        X = (nu + H) cos(phi) cos(lambda)
        Y = (nu + H) cos(phi) sin(lambda)
        Z = ((1 - e^2) nu + H) sin(phi)
    """
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    nu = ellipsoid.nu(phi)
    x = (nu + height) * math.cos(phi) * math.cos(lam)
    y = (nu + height) * math.cos(phi) * math.sin(lam)
    z = ((1.0 - ellipsoid.e2) * nu + height) * math.sin(phi)
    return x, y, z


def cartesian_to_geodetic(x: float, y: float, z: float, ellipsoid: Ellipsoid,
                          tol: float = 1e-13, max_iter: int = 100) -> Tuple[float, float, float]:
    """(X, Y, Z) -> (phi, lambda, H), the boxed formulae *1 / *4 of the schematic.

        tan(lambda) = Y / X
        tan(phi)    = (Z + e^2 nu sin(phi)) / sqrt(X^2 + Y^2)
        H           = X sec(lambda) sec(phi) - nu

    The latitude formula is implicit -- ``nu`` and ``sin(phi)`` on the right
    depend on ``phi`` -- so it is solved by fixed-point iteration, seeded with
    the spherical approximation.  It converges in ~4 iterations for terrestrial
    heights.

    ``H`` is evaluated as ``sqrt(X^2 + Y^2) / cos(phi) - nu``, which is
    algebraically identical to ``X sec(lambda) sec(phi) - nu`` but does not blow
    up as X -> 0 near the 90 deg meridians.  Near the poles (|phi| > 89.9 deg)
    the equivalent polar form is used instead.
    """
    e2 = ellipsoid.e2
    lam = math.atan2(y, x)
    p = math.hypot(x, y)

    if p < 1e-9:  # on the spin axis
        phi = math.copysign(math.pi / 2.0, z)
        return math.degrees(phi), math.degrees(lam), abs(z) - ellipsoid.b

    phi = math.atan2(z, p * (1.0 - e2))  # seed
    nu = ellipsoid.nu(phi)
    for _ in range(max_iter):
        nu = ellipsoid.nu(phi)
        phi_next = math.atan2(z + e2 * nu * math.sin(phi), p)
        if abs(phi_next - phi) < tol:
            phi = phi_next
            nu = ellipsoid.nu(phi)
            break
        phi = phi_next
    else:  # pragma: no cover - unreachable for terrestrial input
        raise RuntimeError("cartesian_to_geodetic did not converge")

    if abs(phi) < math.radians(89.9):
        height = p / math.cos(phi) - nu
    else:
        height = z / math.sin(phi) - (1.0 - e2) * nu
    return math.degrees(phi), math.degrees(lam), height


# --------------------------------------------------------------------------
# step 2 : 7-parameter Helmert datum transformation
# --------------------------------------------------------------------------

class Helmert7(object):
    """7-parameter similarity transformation, in the sense drawn on the schematic.

        | X |          | dX |   | (1+S)   tz     -ty  | | X |
        | Y |        = | dY | + |  -tz   (1+S)    tx  | | Y |
        | Z |_target   | dZ |   |   ty    -tx   (1+S) | | Z |_source

    Note the sign pattern: rotations are transposed relative to the EPSG 9606
    "position vector" convention, i.e. this is the EPSG 9607 "coordinate frame"
    convention.  The parameters below are published for exactly this layout, so
    the matrix is coded literally as drawn -- do not feed these numbers to a
    library that assumes position-vector rotations without flipping the signs of
    ``rx``, ``ry``, ``rz``.

    Translations are metres, rotations arc-seconds, scale parts per million.
    """

    __slots__ = ("name", "dx", "dy", "dz", "rx", "ry", "rz", "s")

    def __init__(self, name: str, dx: float, dy: float, dz: float,
                 rx: float, ry: float, rz: float, s: float) -> None:
        self.name = name
        self.dx, self.dy, self.dz = dx, dy, dz
        self.rx, self.ry, self.rz = rx, ry, rz
        self.s = s

    def _matrix(self) -> List[List[float]]:
        k = 1.0 + self.s * 1e-6
        tx, ty, tz = self.rx * _ARCSEC, self.ry * _ARCSEC, self.rz * _ARCSEC
        return [
            [k, tz, -ty],
            [-tz, k, tx],
            [ty, -tx, k],
        ]

    def forward(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        """Source -> target (for ``ITRF96_TO_HK80``: WGS84/ITRF96 -> HK80)."""
        m = self._matrix()
        return (
            self.dx + m[0][0] * x + m[0][1] * y + m[0][2] * z,
            self.dy + m[1][0] * x + m[1][1] * y + m[1][2] * z,
            self.dz + m[2][0] * x + m[2][1] * y + m[2][2] * z,
        )

    def inverse(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        """Target -> source, by exact inversion of the 3x3 system.

        The schematic shows a separately published reverse parameter set
        (note *3).  Inverting the forward transform exactly is preferable: it
        makes the round trip closed to numerical precision, whereas the two
        published sets are each rounded independently and disagree at the
        sub-millimetre level.
        """
        m = self._matrix()
        return _solve3(m, (x - self.dx, y - self.dy, z - self.dz))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "Helmert7(%r)" % (self.name,)


def _solve3(m: Sequence[Sequence[float]], b: Sequence[float]) -> Tuple[float, float, float]:
    """Solve a 3x3 linear system by Cramer's rule."""
    def det3(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))

    d = det3(m)
    if abs(d) < 1e-30:  # pragma: no cover - impossible for a similarity transform
        raise ZeroDivisionError("singular transformation matrix")
    out = []
    for col in range(3):
        mc = [list(row) for row in m]
        for row in range(3):
            mc[row][col] = b[row]
        out.append(det3(mc) / d)
    return out[0], out[1], out[2]


#: Transformation parameter set ``7P_ITRF96_HK80_V1.0``, published by the
#: Geodetic Survey Section, Survey and Mapping Office, Lands Department, in
#: "Geodetic Datum Transformation and Map Projection Parameter Set for
#: Computation between ITRF96 Geodetic Coordinates (or Cartesian Coordinates)
#: and HK1980 Grid Coordinates".
#:
#: DIRECTION.  Fed to the matrix exactly as drawn on the schematic, these values
#: transform **HK80 -> ITRF96/WGS84** (schematic note *3).  The opposite
#: direction, note *2, is obtained with :meth:`Helmert7.inverse`.  This is the
#: same set, in the same sense, as EPSG:1825 "Hong Kong 1980 to WGS 84 (1)":
#: EPSG states it under the position-vector convention with rotations
#: (+0.067753, -2.243648, -1.158828), whose rotation matrix is identical to the
#: coordinate-frame matrix drawn on the schematic with the signs below.
#:
#: PROVENANCE.  The parameter document is *not* among the PDFs in this folder --
#: the schematic only names it.  ``selftest()`` therefore validates these values
#: against an independent figure in the Explanatory Notes: page B6 gives the
#: HK80-minus-WGS84 geographic shift over Hong Kong as dphi = +5.5",
#: dlambda = -8.8", each +/- 0.1".  Applied in the direction documented above
#: they reproduce it as +5.511", -8.831".  Confirm against the Lands Department
#: document before survey use.
HK80_TO_ITRF96 = Helmert7(
    "7P_ITRF96_HK80_V1.0",
    dx=-162.619, dy=-276.959, dz=-161.764,   # metres
    rx=-0.067753, ry=2.243648, rz=1.158828,  # arc-seconds
    s=-1.094246,                             # ppm
)


# --------------------------------------------------------------------------
# step 4 : Transverse Mercator, Eq.1 - Eq.5
# --------------------------------------------------------------------------

class TMParams(object):
    """A Transverse Mercator parameter set (Explanatory Notes page C10)."""

    __slots__ = ("name", "ellipsoid", "lat0", "lon0", "N0", "E0", "m0")

    def __init__(self, name: str, ellipsoid: Ellipsoid, lat0: float, lon0: float,
                 N0: float, E0: float, m0: float) -> None:
        self.name = name
        self.ellipsoid = ellipsoid
        self.lat0 = lat0    # latitude of projection origin, decimal degrees
        self.lon0 = lon0    # central meridian, decimal degrees
        self.N0 = N0        # false northing, metres
        self.E0 = E0        # false easting, metres
        self.m0 = m0        # scale factor on the central meridian

    @property
    def M0(self) -> float:
        """Meridian distance to the projection origin, Eq.3 with phi = phi0."""
        return meridian_distance(math.radians(self.lat0), self.ellipsoid)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "TMParams(%r)" % (self.name,)


#: HK1980 Grid.  Origin "Patridge Hill" trig station no. 2, page B4 / C10.
HK1980_GRID = TMParams(
    "HK1980 Grid", INTERNATIONAL_1910,
    lat0=dms(22, 18, 43.68), lon0=dms(114, 10, 42.80),
    N0=819069.80, E0=836694.05, m0=1.0,
)

#: UTM zones as used in Hong Kong, both datums (page C10).
UTM_WGS84_49Q = TMParams("UTM 49Q (WGS84)", WGS84, 0.0, 111.0, 0.0, 500000.0, 0.9996)
UTM_WGS84_50Q = TMParams("UTM 50Q (WGS84)", WGS84, 0.0, 117.0, 0.0, 500000.0, 0.9996)
UTM_HK80_49Q = TMParams("UTM 49Q (HK80)", INTERNATIONAL_1910, 0.0, 111.0, 0.0, 500000.0, 0.9996)
UTM_HK80_50Q = TMParams("UTM 50Q (HK80)", INTERNATIONAL_1910, 0.0, 117.0, 0.0, 500000.0, 0.9996)


def meridian_distance(lat_rad: float, ellipsoid: Ellipsoid) -> float:
    """Eq.3 -- meridian arc distance from the Equator.

        M = a [A0' phi - A2' sin(2 phi) + A4' sin(4 phi)]

    with A0' = 1 - e^2/4 - 3 e^4/64,  A2' = (3/8)(e^2 + e^4/4),  A4' = (15/256) e^4.

    This is the series as published, truncated at e^4; the e^6 term is omitted.
    That costs ~2 m in the absolute arc length, but Eq.1 and Eq.5 only ever use
    *differences* of M taken over Hong Kong's 0.4 deg of latitude, where the
    truncation is common-mode and cancels to well under a millimetre.  It is
    kept exactly as printed so that results match Lands Department computations.
    """
    e2 = ellipsoid.e2
    e4 = e2 * e2
    a0 = 1.0 - e2 / 4.0 - 3.0 * e4 / 64.0
    a2 = (3.0 / 8.0) * (e2 + e4 / 4.0)
    a4 = (15.0 / 256.0) * e4
    return ellipsoid.a * (a0 * lat_rad - a2 * math.sin(2.0 * lat_rad) + a4 * math.sin(4.0 * lat_rad))


def footpoint_latitude(M: float, ellipsoid: Ellipsoid,
                       tol: float = 1e-13, max_iter: int = 100) -> float:
    """Invert Eq.3: the latitude phi_p whose meridian distance is ``M``.

    Note 1 under Eq.5 requires this be done "by iteration using Eq.3".  Newton's
    method on Eq.3 is used, with dM/dphi = rho(phi); it converges in 3-4 steps.
    """
    e2 = ellipsoid.e2
    e4 = e2 * e2
    a0 = 1.0 - e2 / 4.0 - 3.0 * e4 / 64.0
    phi = M / (ellipsoid.a * a0)  # seed
    for _ in range(max_iter):
        dM = M - meridian_distance(phi, ellipsoid)
        step = dM / ellipsoid.rho(phi)
        phi += step
        if abs(step) < tol:
            return phi
    raise RuntimeError("footpoint_latitude did not converge")  # pragma: no cover


def geographic_to_grid(lat_deg: float, lon_deg: float, params: TMParams) -> Tuple[float, float]:
    """Eq.1 and Eq.2 -- (phi, lambda) -> (N, E) on the given projection.

        N = N0 + m0 { (M - M0) + nu sin(phi) (dlam^2/2) cos(phi) }
        E = E0 + m0 { nu dlam cos(phi) + nu cos^3(phi) (psi - t^2) dlam^3/6 }
    """
    ell = params.ellipsoid
    phi = math.radians(lat_deg)
    dlam = math.radians(lon_deg - params.lon0)

    nu = ell.nu(phi)
    psi = ell.psi(phi)
    t = math.tan(phi)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    M = meridian_distance(phi, ell)

    N = params.N0 + params.m0 * ((M - params.M0)
                                 + nu * sin_phi * (dlam ** 2 / 2.0) * cos_phi)
    E = params.E0 + params.m0 * (nu * dlam * cos_phi
                                 + nu * cos_phi ** 3 * (psi - t * t) * (dlam ** 3 / 6.0))
    return N, E


def grid_to_geographic(N: float, E: float, params: TMParams,
                       exact: bool = False) -> Tuple[float, float]:
    """Eq.4 and Eq.5 -- (N, E) -> (phi, lambda) on the given projection.

        lambda = lambda0 + sec(phi_p) (dE / (m0 nu_p))
                         - sec(phi_p) (dE^3 / (6 m0^3 nu_p^3)) (psi_p + 2 t_p^2)
        phi    = phi_p - (t_p / (m0 rho_p)) (dE^2 / (2 m0 nu_p))

    where phi_p is the foot-point latitude for M = (dN + M0) / m0, and every
    subscripted quantity is evaluated at phi_p.

    Eq.2 and Eq.4 are independent truncations of the same series, so applying
    them in sequence does not close perfectly.  Over Hong Kong the gap is
    sub-millimetre, but ``exact=True`` removes it entirely by refining the
    result with two Newton steps against ``geographic_to_grid``, making the
    inverse a true inverse of the forward formulae.
    """
    ell = params.ellipsoid
    dN = N - params.N0
    dE = E - params.E0

    phi_p = footpoint_latitude((dN + params.M0) / params.m0, ell)

    nu_p = ell.nu(phi_p)
    rho_p = ell.rho(phi_p)
    psi_p = ell.psi(phi_p)
    t_p = math.tan(phi_p)
    sec_p = 1.0 / math.cos(phi_p)
    m0 = params.m0

    lam = (math.radians(params.lon0)
           + sec_p * (dE / (m0 * nu_p))
           - sec_p * (dE ** 3 / (6.0 * m0 ** 3 * nu_p ** 3)) * (psi_p + 2.0 * t_p ** 2))
    phi = phi_p - (t_p / (m0 * rho_p)) * (dE ** 2 / (2.0 * m0 * nu_p))

    lat_deg, lon_deg = math.degrees(phi), math.degrees(lam)
    if exact:
        lat_deg, lon_deg = _refine_inverse(N, E, lat_deg, lon_deg, params)
    return lat_deg, lon_deg


def _refine_inverse(N: float, E: float, lat_deg: float, lon_deg: float,
                    params: TMParams, iterations: int = 3) -> Tuple[float, float]:
    """Newton-refine (lat, lon) so that ``geographic_to_grid`` reproduces (N, E)."""
    ell = params.ellipsoid
    for _ in range(iterations):
        n_i, e_i = geographic_to_grid(lat_deg, lon_deg, params)
        dn, de = N - n_i, E - e_i
        if abs(dn) < 1e-9 and abs(de) < 1e-9:
            break
        phi = math.radians(lat_deg)
        # Diagonal Jacobian: dN/dphi ~ m0 rho, dE/dlambda ~ m0 nu cos(phi).
        lat_deg += math.degrees(dn / (params.m0 * ell.rho(phi)))
        lon_deg += math.degrees(de / (params.m0 * ell.nu(phi) * math.cos(phi)))
    return lat_deg, lon_deg


# --------------------------------------------------------------------------
# datum-level convenience wrappers (steps 1 - 3)
# --------------------------------------------------------------------------

def wgs84_to_hk80_geographic(lat_deg: float, lon_deg: float,
                             height: float) -> Tuple[float, float, float]:
    """WGS84/ITRF96 geographic -> HK80 geographic, via cartesian (steps 1-3).

    This is schematic note *2, so the parameter set is applied in reverse -- see
    the direction note on :data:`HK80_TO_ITRF96`.
    """
    x, y, z = geodetic_to_cartesian(lat_deg, lon_deg, height, WGS84)
    x, y, z = HK80_TO_ITRF96.inverse(x, y, z)
    return cartesian_to_geodetic(x, y, z, INTERNATIONAL_1910)


def hk80_to_wgs84_geographic(lat_deg: float, lon_deg: float,
                             height: float) -> Tuple[float, float, float]:
    """HK80 geographic -> WGS84/ITRF96 geographic (schematic note *3)."""
    x, y, z = geodetic_to_cartesian(lat_deg, lon_deg, height, INTERNATIONAL_1910)
    x, y, z = HK80_TO_ITRF96.forward(x, y, z)
    return cartesian_to_geodetic(x, y, z, WGS84)


# --------------------------------------------------------------------------
# the third dimension: HKPD height model
# --------------------------------------------------------------------------

class HeightModel(object):
    """Ellipsoidal height <-> Hong Kong Principal Datum level.

    Built from the 74 control points of the "Height Model of Hong Kong
    (Version 1.0)", each of which gives an ITRF96 ellipsoidal height ``H`` and a
    levelled HKPD height ``h``.  The separation

        N_sep = H - h

    runs from -4.06 m in the south-west (Lantau) to -2.08 m in the east, varying
    smoothly.  It is modelled as a least-squares quadratic trend surface in
    (dlon, dlat) plus inverse-distance-weighted interpolation of the residuals,
    which keeps the surface smooth while honouring the control points closely.

    Accuracy: see ``leave_one_out()``.  Outside the convex hull of the control
    points the trend surface extrapolates and should not be trusted; the
    ``strict`` flag on the conversion helpers rejects such positions.
    """

    #: Bounding box of the control point set, (lat_min, lat_max, lon_min, lon_max).
    def __init__(self, points: Sequence[Tuple[float, float, float]],
                 power: float = 2.0, neighbours: int = 10) -> None:
        if not points:
            raise ValueError("height model needs at least one control point")
        self.points = [(float(a), float(b), float(c)) for a, b, c in points]
        self.power = power
        self.neighbours = neighbours

        lats = [p[0] for p in self.points]
        lons = [p[1] for p in self.points]
        self.lat0 = sum(lats) / len(lats)
        self.lon0 = sum(lons) / len(lons)
        self.bounds = (min(lats), max(lats), min(lons), max(lons))

        self._coeffs = self._fit_trend(self.points)
        self._residuals = [(p[0], p[1], p[2] - self._trend(p[0], p[1])) for p in self.points]

    # -- trend surface -----------------------------------------------------

    def _basis(self, lat: float, lon: float) -> List[float]:
        u = lon - self.lon0
        v = lat - self.lat0
        return [1.0, u, v, u * u, u * v, v * v]

    def _fit_trend(self, points: Sequence[Tuple[float, float, float]]) -> List[float]:
        n = len(self._basis(0.0, 0.0))
        if len(points) < n:  # too few points for a quadratic; fall back to the mean
            mean = sum(p[2] for p in points) / len(points)
            return [mean] + [0.0] * (n - 1)
        ata = [[0.0] * n for _ in range(n)]
        atb = [0.0] * n
        for lat, lon, val in points:
            g = self._basis(lat, lon)
            for i in range(n):
                atb[i] += g[i] * val
                for j in range(n):
                    ata[i][j] += g[i] * g[j]
        return _solve(ata, atb)

    def _trend(self, lat: float, lon: float) -> float:
        return sum(c * g for c, g in zip(self._coeffs, self._basis(lat, lon)))

    # -- interpolation -----------------------------------------------------

    def separation(self, lat_deg: float, lon_deg: float) -> float:
        """Interpolated N_sep = H_ellipsoidal - h_HKPD, in metres (negative in HK)."""
        return self._separation(lat_deg, lon_deg, self._residuals, self._coeffs)

    def _separation(self, lat_deg, lon_deg, residuals, coeffs) -> float:
        trend = sum(c * g for c, g in zip(coeffs, self._basis(lat_deg, lon_deg)))

        # Distances in metres-ish: scale longitude by cos(lat) so the weighting
        # is isotropic on the ground rather than in degrees.
        cos_lat = math.cos(math.radians(lat_deg))
        scored = []
        for lat, lon, res in residuals:
            dy = lat - lat_deg
            dx = (lon - lon_deg) * cos_lat
            d2 = dx * dx + dy * dy
            if d2 < 1e-18:  # exactly on a control point
                return trend + res
            scored.append((d2, res))
        scored.sort(key=lambda t: t[0])
        if self.neighbours > 0:
            scored = scored[: self.neighbours]

        wsum = 0.0
        vsum = 0.0
        for d2, res in scored:
            w = 1.0 / (d2 ** (self.power / 2.0))
            wsum += w
            vsum += w * res
        return trend + vsum / wsum

    def contains(self, lat_deg: float, lon_deg: float, margin: float = 0.02) -> bool:
        """Is the position inside the control point bounding box (+ margin, deg)?"""
        lat_min, lat_max, lon_min, lon_max = self.bounds
        return (lat_min - margin <= lat_deg <= lat_max + margin
                and lon_min - margin <= lon_deg <= lon_max + margin)

    # -- conversions -------------------------------------------------------

    def to_hkpd(self, lat_deg: float, lon_deg: float, ellipsoidal_height: float) -> float:
        """WGS84/ITRF96 ellipsoidal height -> HKPD level."""
        return ellipsoidal_height - self.separation(lat_deg, lon_deg)

    def to_ellipsoidal(self, lat_deg: float, lon_deg: float, hkpd_height: float) -> float:
        """HKPD level -> WGS84/ITRF96 ellipsoidal height."""
        return hkpd_height + self.separation(lat_deg, lon_deg)

    # -- validation --------------------------------------------------------

    def leave_one_out(self) -> dict:
        """Leave-one-out cross-validation of the separation model, in metres."""
        errs = []
        for i, (lat, lon, val) in enumerate(self.points):
            rest = self.points[:i] + self.points[i + 1:]
            coeffs = self._fit_trend(rest)
            resid = [(p[0], p[1], p[2] - sum(c * g for c, g in zip(coeffs, self._basis(p[0], p[1]))))
                     for p in rest]
            errs.append(self._separation(lat, lon, resid, coeffs) - val)
        n = len(errs)
        return {
            "n": n,
            "mean": sum(errs) / n,
            "rms": math.sqrt(sum(e * e for e in errs) / n),
            "max_abs": max(abs(e) for e in errs),
        }


def _solve(a: List[List[float]], b: List[float]) -> List[float]:
    """Solve a dense linear system by Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-30:
            raise ZeroDivisionError("singular normal equations")
        m[col], m[piv] = m[piv], m[col]
        pv = m[col][col]
        for r in range(col + 1, n):
            factor = m[r][col] / pv
            if factor:
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = s / m[r][r]
    return x


def load_height_model(path: Optional[str] = None, **kwargs) -> HeightModel:
    """Build a :class:`HeightModel` from ``hk_height_model.csv``."""
    path = path or _HEIGHT_MODEL_CSV
    pts = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            pts.append((float(row["lat_deg"]), float(row["lon_deg"]), float(row["N_sep"])))
    return HeightModel(pts, **kwargs)


_DEFAULT_HEIGHT_MODEL = None  # type: Optional[HeightModel]


def default_height_model() -> HeightModel:
    """The shared height model, loaded from disk on first use."""
    global _DEFAULT_HEIGHT_MODEL
    if _DEFAULT_HEIGHT_MODEL is None:
        _DEFAULT_HEIGHT_MODEL = load_height_model()
    return _DEFAULT_HEIGHT_MODEL


def separation(lat_deg: float, lon_deg: float,
               height_model: Optional[HeightModel] = None) -> float:
    """N_sep = H_ellipsoidal - h_HKPD at a WGS84 position, metres."""
    return (height_model or default_height_model()).separation(lat_deg, lon_deg)


def ellipsoidal_to_hkpd(lat_deg: float, lon_deg: float, ellipsoidal_height: float,
                        height_model: Optional[HeightModel] = None) -> float:
    """WGS84 ellipsoidal height -> Hong Kong Principal Datum level, metres.

    The position is only used to look up the separation, so a WGS84 latitude and
    longitude good to a few hundred metres is ample.
    """
    return ellipsoidal_height - separation(lat_deg, lon_deg, height_model)


def hkpd_to_ellipsoidal(lat_deg: float, lon_deg: float, hkpd_height: float,
                        height_model: Optional[HeightModel] = None) -> float:
    """Hong Kong Principal Datum level -> WGS84 ellipsoidal height, metres."""
    return hkpd_height + separation(lat_deg, lon_deg, height_model)


# --------------------------------------------------------------------------
# top-level 3-D conversions
# --------------------------------------------------------------------------

class Result(dict):
    """A dict that also exposes its keys as attributes, for readable call sites."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def wgs84_to_hk1980(lat_deg: float, lon_deg: float, height: float = 0.0,
                    height_model: Optional[HeightModel] = None,
                    strict: bool = True,
                    height_type: str = ELLIPSOIDAL) -> Result:
    """WGS84/ITRF96 (phi, lambda, height) -> HK1980 Grid (N, E) + heights.

    Parameters
    ----------
    lat_deg, lon_deg : decimal degrees, WGS84 (ITRF96) geographic.
    height : the input height, metres, in the convention named by
        ``height_type``.  Pass ``height_type=HKPD`` if what you have is a
        levelled Hong Kong Principal Datum height rather than an ellipsoidal one.
    height_model : override the default HKPD separation model.
    strict : raise if the position falls outside the height model's coverage.

    Returns a :class:`Result` with:

        N, E                 HK1980 Grid coordinates, metres
        h_hkpd               level above Hong Kong Principal Datum, metres
        ellipsoidal_height   height above the WGS84 ellipsoid, metres
        separation           N_sep applied, metres
        hk80_lat/lon         HK80 datum geographic coordinates, decimal degrees
        hk80_height          HK80 *ellipsoidal* height -- a by-product of the
                             datum shift, NOT a level; use ``h_hkpd`` for that

    Both heights are always reported, so the same call converts position and
    height convention together.
    """
    hm = height_model or default_height_model()
    if strict and not hm.contains(lat_deg, lon_deg):
        raise ValueError(
            "position (%.6f, %.6f) is outside the Height Model of Hong Kong coverage "
            "%s; pass strict=False to extrapolate anyway" % (lat_deg, lon_deg, hm.bounds))

    sep = hm.separation(lat_deg, lon_deg)
    if height_type == HKPD:
        h_hkpd = height
        ellipsoidal_height = height + sep
    elif height_type == ELLIPSOIDAL:
        ellipsoidal_height = height
        h_hkpd = height - sep
    else:
        raise ValueError("height_type must be %r or %r, got %r"
                         % (ELLIPSOIDAL, HKPD, height_type))

    hk_lat, hk_lon, hk_h = wgs84_to_hk80_geographic(lat_deg, lon_deg, ellipsoidal_height)
    N, E = geographic_to_grid(hk_lat, hk_lon, HK1980_GRID)

    return Result(
        N=N, E=E,
        h_hkpd=h_hkpd,
        ellipsoidal_height=ellipsoidal_height,
        separation=sep,
        hk80_lat=hk_lat, hk80_lon=hk_lon, hk80_height=hk_h,
    )


def hk1980_to_wgs84(N: float, E: float, height: float = 0.0,
                    height_model: Optional[HeightModel] = None,
                    strict: bool = True, iterations: int = 6,
                    height_type: str = HKPD) -> Result:
    """HK1980 Grid (N, E) + height -> WGS84/ITRF96 (phi, lambda, H).

    ``height_type`` says which convention the supplied height is in; it defaults
    to :data:`HKPD`, since a levelled Hong Kong Principal Datum height is what
    normally accompanies a grid coordinate.  Pass :data:`ELLIPSOIDAL` if you
    already have a height above the WGS84 ellipsoid.

    Per note *6 of the schematic an approximate level must be supplied going
    upward, and the ellipsoidal height that comes back is approximate -- it is
    only as good as the level you fed in plus the separation model.

    The unknown fed into the datum shift is the *HK80 ellipsoidal* height, but
    what is known is the level, which pins the *WGS84 ellipsoidal* height once
    the separation is known -- and the separation depends on the WGS84 position,
    which is what we are solving for.  The two are resolved by a short
    fixed-point loop.  Ellipsoidal heights on the two datums differ by a
    quantity that varies only over hundreds of metres of horizontal movement, so
    the residual drives to zero in two or three passes.
    """
    if height_type not in (ELLIPSOIDAL, HKPD):
        raise ValueError("height_type must be %r or %r, got %r"
                         % (ELLIPSOIDAL, HKPD, height_type))
    hm = height_model or default_height_model()

    # Horizontal first: the projection carries no height, so this is final.
    hk_lat, hk_lon = grid_to_geographic(N, E, HK1980_GRID, exact=True)

    hk_h = float(height)  # seed; wrong by the datum separation, ~2-4 m
    lat, lon, H = hk80_to_wgs84_geographic(hk_lat, hk_lon, hk_h)
    for _ in range(max(1, iterations)):
        target_H = height if height_type == ELLIPSOIDAL else height + hm.separation(lat, lon)
        residual = target_H - H
        if abs(residual) < 1e-6:
            break
        hk_h += residual  # dH_wgs84 / dH_hk80 == 1 to many digits
        lat, lon, H = hk80_to_wgs84_geographic(hk_lat, hk_lon, hk_h)

    if strict and not hm.contains(lat, lon):
        raise ValueError(
            "position (%.6f, %.6f) is outside the Height Model of Hong Kong coverage "
            "%s; pass strict=False to extrapolate anyway" % (lat, lon, hm.bounds))

    sep = hm.separation(lat, lon)
    return Result(
        lat=lat, lon=lon, height=H,
        ellipsoidal_height=H, h_hkpd=H - sep,
        separation=sep,
        hk80_lat=hk_lat, hk80_lon=hk_lon, hk80_height=hk_h,
    )


# --------------------------------------------------------------------------
# self-test against the published reference examples
# --------------------------------------------------------------------------

def selftest(verbose: bool = True) -> bool:
    """Check the implementation against the Explanatory Notes, page C10.

    All reference values there are quoted to whole metres / 0.01", so the
    tolerances below are set to the quoting precision.
    """
    ok = True

    def check(label, got, want, tol, unit=""):
        nonlocal ok
        good = abs(got - want) <= tol
        ok = ok and good
        if verbose:
            print("  [%s] %-46s got %14.4f  want %14.4f  (d=%+.4f%s)"
                  % ("ok" if good else "FAIL", label, got, want, got - want, unit))

    if verbose:
        print("Derived constants vs page C10")
    # The tabulated nu / rho / psi are evaluated at the HK1980 Grid origin, each
    # on its own datum: the HK80 columns at the origin latitude 22 18 43.68, the
    # WGS84 column at the WGS84 latitude of that same physical point, which is
    # 5.5" further south (page B6).  Solving nu = 6381215.957 for latitude
    # returns 22 18 38.17, confirming it.
    origin_hk80 = math.radians(HK1980_GRID.lat0)
    origin_wgs84 = math.radians(HK1980_GRID.lat0 - 5.5 / 3600.0)
    for ell, phi, nu_ref, rho_ref, psi_ref in (
        (WGS84, origin_wgs84, 6381215.957, 6344618.793, 1.005768221),
        (INTERNATIONAL_1910, origin_hk80, 6381480.502, 6344727.809, 1.005792635),
    ):
        check("%s nu" % ell.name, ell.nu(phi), nu_ref, 0.05, " m")
        check("%s rho" % ell.name, ell.rho(phi), rho_ref, 0.15, " m")
        check("%s psi" % ell.name, ell.psi(phi), psi_ref, 5e-9)
    check("WGS84 e^2", WGS84.e2, 6.69437999e-3, 5e-11)
    check("International 1910 e^2", INTERNATIONAL_1910.e2, 6.722670022e-3, 5e-12)
    check("HK1980 Grid M0 (Eq.3)", HK1980_GRID.M0, 2468395.728, 5e-4, " m")

    if verbose:
        print("\nUTM reference examples, page C10 (tolerance 2.5 m / 0.25\", see note)")
    # Hong Kong sits 2.8 deg off the 117 E central meridian of zone 50Q, and
    # Eq.1/Eq.2 are truncated at dlambda^3.  The omitted dlambda^4 term is worth
    # ~2.3 m of northing there, so these examples cannot be reproduced to the
    # metre by any faithful implementation of the published series -- and the
    # published examples are not self-consistent either: the forward example
    # prints 2483566 N while the inverse example feeds 2483568 N for what is
    # nominally the same point.  Checked against an independent high-order
    # Transverse Mercator, the easting computed here is right to 2 cm and it is
    # the printed 209194 that is the outlier.  The HK1980 Grid, whose central
    # meridian runs through the territory, is unaffected -- see below.
    n, e = geographic_to_grid(dms(22, 26, 1.26), dms(114, 10, 29.31), UTM_WGS84_50Q)
    check("WGS84 phi/lam -> UTM N", n, 2483566.0, 2.5, " m")
    check("WGS84 phi/lam -> UTM E", e, 209194.0, 2.5, " m")

    lat, lon = grid_to_geographic(2483568.0, 209192.0, UTM_WGS84_50Q)
    check("WGS84 UTM -> phi (arcsec)", lat * 3600.0, dms(22, 26, 1.16) * 3600.0, 0.25, '"')
    check("WGS84 UTM -> lam (arcsec)", lon * 3600.0, dms(114, 10, 29.24) * 3600.0, 0.25, '"')

    n, e = geographic_to_grid(dms(22, 26, 6.76), dms(114, 10, 20.46), UTM_HK80_50Q)
    check("HK80 phi/lam -> UTM N", n, 2483772.0, 2.5, " m")
    check("HK80 phi/lam -> UTM E", e, 208932.0, 2.5, " m")

    lat, lon = grid_to_geographic(2483775.0, 208930.0, UTM_HK80_50Q)
    check("HK80 UTM -> phi (arcsec)", lat * 3600.0, dms(22, 26, 6.89) * 3600.0, 0.25, '"')
    check("HK80 UTM -> lam (arcsec)", lon * 3600.0, dms(114, 10, 20.39) * 3600.0, 0.25, '"')

    if verbose:
        print("\nHK1980 Grid reference example, page C10 (tolerance 0.5 m / 0.05\")")
    # Eq.1/Eq.2 and Eq.4/Eq.5 on the projection this module exists for.
    n, e = geographic_to_grid(dms(22, 26, 6.76), dms(114, 10, 20.46), HK1980_GRID)
    check("HK80 phi/lam -> HK1980 N", n, 832699.0, 0.5, " m")
    check("HK80 phi/lam -> HK1980 E", e, 836055.0, 0.5, " m")

    lat, lon = grid_to_geographic(832699.0, 836055.0, HK1980_GRID)
    check("HK1980 -> phi (arcsec)", lat * 3600.0, dms(22, 26, 6.76) * 3600.0, 0.05, '"')
    check("HK1980 -> lam (arcsec)", lon * 3600.0, dms(114, 10, 20.45) * 3600.0, 0.05, '"')

    # Eq.2 and Eq.4 are independent truncations of the same series, so
    # forward-then-inverse is not required to close exactly.  Swept on a grid
    # over 22.1-22.6 N, 113.8-114.5 E -- a box wider than the territory -- the
    # worst case is 1.6 mm, at the far south-west corner.  Negligible against
    # the 0.2" / 5 m datum transformation accuracy quoted on page C4, and
    # removable entirely with grid_to_geographic(..., exact=True).
    worst = 0.0
    for la in (22.15, 22.30, 22.45, 22.58):
        for lo in (113.82, 114.00, 114.20, 114.44):
            n, e = geographic_to_grid(la, lo, HK1980_GRID)
            bl, bo = grid_to_geographic(n, e, HK1980_GRID)
            worst = max(worst, abs(bl - la) * 111320.0, abs(bo - lo) * 102900.0)
    check("Eq.1-5 closure over HK (mm)", worst * 1000.0, 0.0, 3.0, " mm")

    worst_exact = 0.0
    for la in (22.15, 22.30, 22.45, 22.58):
        for lo in (113.82, 114.00, 114.20, 114.44):
            n, e = geographic_to_grid(la, lo, HK1980_GRID)
            bl, bo = grid_to_geographic(n, e, HK1980_GRID, exact=True)
            worst_exact = max(worst_exact, abs(bl - la) * 111320.0, abs(bo - lo) * 102900.0)
    check("  ... with exact=True (mm)", worst_exact * 1000.0, 0.0, 1e-3, " mm")

    if verbose:
        print("\nDatum shift vs page B6 (HK80 - WGS84 = +5.5\", -8.8\", each +/-0.1\")")
    # Checked at the centre of the territory; the published figures are a
    # territory-wide approximation, hence the 0.15" tolerance.
    hk_lat, hk_lon, _ = wgs84_to_hk80_geographic(22.3, 114.17, 20.0)
    check("d(phi)  arcsec", (hk_lat - 22.3) * 3600.0, 5.5, 0.15, '"')
    check("d(lam)  arcsec", (hk_lon - 114.17) * 3600.0, -8.8, 0.15, '"')

    if verbose:
        print("\nRound trips")
    for lat, lon, h in ((22.3193, 114.1694, 25.0), (22.2, 113.9, 300.0), (22.55, 114.37, 5.0)):
        r = wgs84_to_hk1980(lat, lon, h)
        b = hk1980_to_wgs84(r.N, r.E, r.h_hkpd)
        check("round trip lat (mm)", (b.lat - lat) * 111320.0 * 1000.0, 0.0, 1.0, " mm")
        check("round trip lon (mm)",
              (b.lon - lon) * 111320.0 * math.cos(math.radians(lat)) * 1000.0, 0.0, 1.0, " mm")
        check("round trip height (mm)", (b.height - h) * 1000.0, 0.0, 1.0, " mm")

    if verbose:
        print("\nHKPD height model, leave-one-out cross validation")
        stats = default_height_model().leave_one_out()
        print("  n=%d  mean=%+.3f m  rms=%.3f m  max|e|=%.3f m"
              % (stats["n"], stats["mean"], stats["rms"], stats["max_abs"]))
        print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return ok


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="hk1980",
        description="3-D conversion between WGS84 and the HK1980 Grid.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fwd", help="WGS84 lat lon H -> HK1980 Grid N E + HKPD level")
    f.add_argument("lat", type=float)
    f.add_argument("lon", type=float)
    f.add_argument("height", type=float, nargs="?", default=0.0,
                   help="ellipsoidal height, metres (default 0)")
    f.add_argument("--loose", action="store_true", help="allow extrapolation outside HK")

    i = sub.add_parser("inv", help="HK1980 Grid N E + HKPD level -> WGS84 lat lon H")
    i.add_argument("N", type=float)
    i.add_argument("E", type=float)
    i.add_argument("height", type=float, nargs="?", default=0.0,
                   help="approximate HKPD level, metres (default 0)")
    i.add_argument("--loose", action="store_true", help="allow extrapolation outside HK")

    sub.add_parser("selftest", help="run the reference-example checks")

    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return 0 if selftest() else 1

    try:
        return _run(args)
    except ValueError as exc:
        import sys
        print("error: %s" % exc, file=sys.stderr)
        return 2


def _run(args) -> int:
    if args.cmd == "fwd":
        r = wgs84_to_hk1980(args.lat, args.lon, args.height, strict=not args.loose)
        print("HK1980 Grid   N = %12.3f m" % r.N)
        print("              E = %12.3f m" % r.E)
        print("HKPD level    h = %12.3f m   (separation %+.3f m)" % (r.h_hkpd, r.separation))
        print("HK80 geographic  %s  %s  H=%.3f m (ellipsoidal, not a level)"
              % (_fmt(r.hk80_lat, "NS"), _fmt(r.hk80_lon, "EW"), r.hk80_height))
    else:
        r = hk1980_to_wgs84(args.N, args.E, args.height, strict=not args.loose)
        print("WGS84  lat = %s   (%.9f)" % (_fmt(r.lat, "NS"), r.lat))
        print("       lon = %s   (%.9f)" % (_fmt(r.lon, "EW"), r.lon))
        print("       H   = %.3f m ellipsoidal (approximate, per note *6; separation %+.3f m)"
              % (r.height, r.separation))
    return 0


def _fmt(deg: float, hemis: str) -> str:
    d, m, s = to_dms(deg)
    return "%d %02d %08.5f %s" % (abs(d), m, s, hemis[0] if deg >= 0 else hemis[1])


if __name__ == "__main__":
    raise SystemExit(_main())
