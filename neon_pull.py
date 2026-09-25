#!/usr/bin/env python3
"""
neon_pull.py
============

Pull any NEON data product, site, and date range into a directory tree, using
the official `neonutilities` package. Downloads the monthly packages, stacks
them into one table per data table, and leaves both on disk.

This replaces the hand-rolled neon_fetch.py. neonutilities handles the API,
the rate limiting, the unzipping, and the stacking, and NEON maintains it.

Layout produced under --out (default $NEON_ROOT):

    {out}/{SITE}/{DPID}/
        filesToStack{PRNUM}/     raw monthly folders as downloaded
        filesToStack{PRNUM}/stackedFiles/
                                 one csv per table, plus variables,
                                 sensor_positions, readme, issue log, citation

Usage
-----
    # what exists, no download
    python neon_pull.py --product DP1.00006.001 --site STER --dates

    # see the tables before committing to a full year
    python neon_pull.py --product DP1.00006.001 --site STER \
        --start 2024-06 --end 2024-06 --timeindex 30 --peek

    # the real pull
    python neon_pull.py --product DP1.00006.001 --site STER \
        --start 2024-01 --end 2024-12 --timeindex 30

Requires NEON_TOKEN in the environment.

Author: written with Claude for the AreandDee soil-twin work.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

import neonutilities as nu
import requests

API = "https://data.neonscience.org/api/v0"

DEFAULT_RELEASE = "RELEASE-2026"

KNOWN = {
    "DP1.00041.001": "Soil temperature",
    "DP1.00094.001": "Soil water content and water salinity",
    "DP1.00095.001": "Soil CO2 concentration",
    "DP1.00040.001": "Soil heat flux plate",
    "DP1.00006.001": "Precipitation",
    "DP1.00002.001": "Single aspirated air temperature",
    "DP1.00023.001": "Shortwave and longwave radiation (net radiometer)",
    "DP1.00024.001": "Photosynthetically active radiation (PAR)",
    "DP1.00098.001": "Relative humidity",
}


def token_or_die() -> str:
    t = os.environ.get("NEON_TOKEN")
    if not t:
        raise SystemExit(
            "NEON_TOKEN is not set. Downloads will fall back to the public\n"
            "rate limit (burst 200, 2 req/s) and will be slow.\n"
            "  export NEON_TOKEN='...'")
    return t


def available_dates(product: str, site: str, token: str | None) -> bool:
    """
    Release tags and months for one product at one site, via /sites/{SITE}.

    neonutilities.list_available_dates() reads /products/{dpid} instead, and
    crashes with `TypeError: object of type 'NoneType' has no len()` when that
    response carries a null siteCodes field. The /sites endpoint returns the
    same availability information per product and does not have that problem.
    Returns True if it found the product.
    """
    hdr = {"X-API-Token": token} if token else {}
    r = requests.get(f"{API}/sites/{site}", headers=hdr, timeout=60)
    r.raise_for_status()
    prods = (r.json().get("data") or {}).get("dataProducts") or []
    for pr in prods:
        if pr.get("dataProductCode") != product:
            continue
        print(f"  {pr.get('dataProductTitle', '')}")
        rels = pr.get("availableReleases") or []
        if rels:
            for entry in rels:
                months = entry.get("availableMonths") or []
                print(f"\n{entry.get('release')}  ({len(months)} months)")
                print("  " + ", ".join(months))
        else:
            months = pr.get("availableMonths") or []
            print(f"\n(no release breakdown)  {len(months)} months")
            print("  " + ", ".join(months))
        return True
    print(f"  {product} is not listed at {site}.")
    print("  Products available there:")
    for pr in sorted(prods, key=lambda x: x.get("dataProductCode", "")):
        print(f"    {pr.get('dataProductCode')}  {pr.get('dataProductTitle')}")
    return False


def describe(stacked: dict) -> None:
    """Print the shape of every table in a stacked result."""
    print(f"\n{'table':<45} {'rows':>10} {'cols':>6}")
    print("-" * 63)
    for k in sorted(stacked):
        v = stacked[k]
        if hasattr(v, "shape"):
            print(f"{k:<45} {v.shape[0]:>10,} {v.shape[1]:>6}")
        else:
            print(f"{k:<45} {'(text)':>10}")


def peek(stacked: dict, n: int = 3) -> None:
    """Show columns and a few rows of each data table, skipping metadata."""
    meta = ("variables", "readme", "issueLog", "citation", "sensor_positions",
            "science_review_flags")
    for k in sorted(stacked):
        v = stacked[k]
        if any(k.startswith(m) for m in meta) or not hasattr(v, "columns"):
            continue
        print(f"\n=== {k}  {v.shape[0]:,} rows ===")
        print("columns:", list(v.columns))
        with_pos = [c for c in ("horizontalPosition", "verticalPosition")
                    if c in v.columns]
        for c in with_pos:
            print(f"  {c}: {sorted(v[c].dropna().unique())}")
        print(v.head(n).to_string())


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Pull a NEON product into a directory tree via neonutilities.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--product", "--dpid", dest="product", required=True,
                   help="Product code, e.g. DP1.00006.001")
    p.add_argument("--site", default="STER")
    p.add_argument("--start", help="First month, YYYY-MM")
    p.add_argument("--end", help="Last month, YYYY-MM")
    p.add_argument("--timeindex", default="30",
                   help="Averaging interval in minutes, or 'all'. "
                        "Sensor (IS) products only.")
    p.add_argument("--table", default="all",
                   help="Single table name. Observational (OS) products only.")
    p.add_argument("--package", default="basic", choices=["basic", "expanded"])
    p.add_argument("--release", default=DEFAULT_RELEASE,
                   help="Use 'current' to include the newest data, but see "
                        "--provisional.")
    p.add_argument("--provisional", action="store_true",
                   help="Include PROVISIONAL data. Off by default: provisional "
                        "data can be revised or withdrawn.")
    p.add_argument("--out", default=os.environ.get("NEON_ROOT", "."),
                   help="Output root")
    p.add_argument("--dates", action="store_true",
                   help="List available releases and months, then exit")
    p.add_argument("--peek", action="store_true",
                   help="Print columns, positions, and head of each data table")
    p.add_argument("--in-memory", action="store_true",
                   help="Load to memory only, do not write files")
    a = p.parse_args(argv)

    title = KNOWN.get(a.product, "")
    print(f"{a.product}  {title}" if title else a.product)

    if a.dates:
        token = os.environ.get("NEON_TOKEN")
        try:
            available_dates(a.product, a.site, token)
        except Exception as exc:
            print(f"  /sites lookup failed ({type(exc).__name__}: {exc}), "
                  "falling back to neonutilities", file=sys.stderr)
            nu.list_available_dates(a.product, a.site)
        return 0

    if not a.start or not a.end:
        raise SystemExit("--start and --end are required unless using --dates.")

    # Observational (OS) products are sampled by field crews, not sensors.
    # --timeindex is meaningless for them and passing 30 returns nothing.
    prnum = a.product[4:9]
    is_observational = prnum.startswith("1")
    timeindex = a.timeindex
    if is_observational and timeindex != "all":
        print(f"  {a.product} is an observational product; "
              f"ignoring --timeindex {timeindex} (use --table to pick a table).")
        timeindex = "all"

    token = token_or_die()
    dest = os.path.join(a.out, a.site, a.product)
    os.makedirs(dest, exist_ok=True)

    common = dict(dpid=a.product, site=a.site,
                  startdate=a.start, enddate=a.end,
                  package=a.package, release=a.release,
                  timeindex=timeindex, tabl=a.table,
                  include_provisional=a.provisional,
                  check_size=False,          # never block on a prompt
                  progress=True, token=token)

    if a.in_memory:
        stacked = nu.load_by_product(**common)
        describe(stacked)
        if a.peek:
            peek(stacked)
        return 0

    print(f"\nDownloading to {dest}")
    nu.zips_by_product(savepath=dest, **common)

    stackdir = os.path.join(dest, f"filesToStack{prnum}")
    if not os.path.isdir(stackdir):
        raise SystemExit(f"Expected {stackdir} after download; nothing there. "
                         "Check the messages above.")

    print(f"\nStacking {stackdir}")
    nu.stack_by_table(filepath=stackdir, progress=True)

    out = os.path.join(stackdir, "stackedFiles")
    files = sorted(glob.glob(os.path.join(out, "*")))
    print(f"\n{len(files)} stacked file(s) in {out}")
    for f in files:
        print(f"  {os.path.basename(f):<50} {os.path.getsize(f) / 1e6:>8.2f} MB")

    if a.peek:
        stacked = nu.load_by_product(**common)
        describe(stacked)
        peek(stacked)

    print("\nNote: stacked tables contain only the rows NEON published. "
          "Missing intervals are absent, not NaN. Reindex onto a regular "
          "grid (neon_soil.regular_grid) before windowing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
