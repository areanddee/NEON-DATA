#!/usr/bin/env python3
"""
neon_dict.py
============

Turn NEON's machine-readable `variables` files into a data dictionary you can
actually read, so you know what a column means before you model it.

NEON ships a variables file with every download. Its columns are:
    table, fieldName, description, dataType, units, downloadPkg, pubFormat

Three ways in:

    # 1. Any NEON download on disk (portal zip already unzipped, or
    #    neonutilities output). Finds every variables*.csv underneath.
    python neon_dict.py --root /project/.../NEON_temp-soil

    # 2. One specific variables file
    python neon_dict.py --file NEON.D10.STER.DP1.00041.001.variables.*.csv

    # 3. From a neonutilities load_by_product() result, in Python:
    #       from neon_dict import dict_from_stacked
    #       dict_from_stacked(swc)

Filters and output:
    --table ST_30_minute     only that table
    --grep temp              only fields whose name or description matches
    --md dict.md             write markdown instead of printing
    --csv dict.csv           write a tidy csv

Author: written with Claude for the AreandDee soil-twin work.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import textwrap

import pandas as pd

VAR_COLS = ["table", "fieldName", "description", "dataType", "units",
            "downloadPkg", "pubFormat"]

# What NEON's column-name suffixes mean. These are conventions across all
# instrumented-systems products, not per-product definitions.
SUFFIXES = {
    "Mean": "arithmetic mean over the averaging interval",
    "Minimum": "smallest value in the interval",
    "Maximum": "largest value in the interval",
    "Variance": "variance of the values in the interval",
    "NumPts": "number of raw points that went into the average",
    "ExpUncert": "expanded uncertainty, roughly a 95% confidence half-width",
    "StdErMean": "standard error of the mean",
    "FinalQF": "final quality flag, 0 = passed, 1 = failed",
    "FinalQFSciRvw": "final quality flag set by manual science review",
    "AlphaQM": "percent of points failing at least one quality test",
    "BetaQM": "percent of points that could not be evaluated",
    "PassQM": "percent of points passing all quality tests",
}


def find_variables_files(root: str) -> list[str]:
    pats = ["**/*variables*.csv", "**/variables_*.csv"]
    out: list[str] = []
    for p in pats:
        out += glob.glob(os.path.join(root, p), recursive=True)
    return sorted(set(out))


def load_variables(paths: list[str]) -> pd.DataFrame:
    frames = []
    for p in paths:
        try:
            v = pd.read_csv(p, dtype=str)
        except Exception as exc:
            print(f"  ! skipping {os.path.basename(p)}: {exc}", file=sys.stderr)
            continue
        if "fieldName" not in v.columns:
            continue
        m = re.search(r"DP\d\.(\d{5})\.\d{3}", os.path.basename(p))
        v["product"] = f"DP1.{m.group(1)}.001" if m else os.path.basename(p)
        frames.append(v)
    if not frames:
        raise SystemExit("No usable variables files found.")
    df = pd.concat(frames, ignore_index=True)
    keep = [c for c in VAR_COLS if c in df.columns] + ["product"]
    return (df[keep]
            .drop_duplicates(subset=[c for c in ("table", "fieldName") if c in df])
            .reset_index(drop=True))


def dict_from_stacked(stacked: dict) -> pd.DataFrame:
    """Build the dictionary from a neonutilities load_by_product() result."""
    frames = []
    for k, v in stacked.items():
        if k.startswith("variables") and hasattr(v, "columns") \
                and "fieldName" in v.columns:
            v = v.copy()
            v["product"] = k.replace("variables_", "DP1.") + ".001"
            frames.append(v)
    if not frames:
        raise ValueError("No variables_* table in that dict. Keys: "
                         f"{sorted(stacked)}")
    return pd.concat(frames, ignore_index=True)


def render(df: pd.DataFrame, width: int = 96) -> str:
    lines = []
    group_cols = [c for c in ("product", "table") if c in df.columns]
    for key, g in df.groupby(group_cols, sort=True):
        head = " / ".join(str(k) for k in (key if isinstance(key, tuple) else (key,)))
        lines.append(f"\n{'=' * width}\n{head}\n{'=' * width}")
        for r in g.itertuples():
            units = getattr(r, "units", "") or ""
            units = "" if str(units).lower() in ("na", "nan", "") else f"  [{units}]"
            dtype = getattr(r, "dataType", "") or ""
            lines.append(f"\n  {r.fieldName}{units}   ({dtype})")
            desc = str(getattr(r, "description", "") or "").strip()
            for ln in textwrap.wrap(desc, width - 6):
                lines.append(f"      {ln}")
    return "\n".join(lines)


def suffix_key() -> str:
    out = ["\n" + "=" * 96,
           "COLUMN-NAME SUFFIXES (conventions shared across NEON IS products)",
           "=" * 96]
    for k, v in SUFFIXES.items():
        out.append(f"  {k:<16} {v}")
    out += ["", "  Position and time columns:",
            "    horizontalPosition (HOR)  which plot / location in the horizontal plane",
            "    verticalPosition   (VER)  which level in the vertical profile",
            "    startDateTime, endDateTime  bounds of the averaging interval, always UTC",
            "    Depths come from the sensor_positions table (zOffset, metres,",
            "    negative = below the reference point), not from the VER index."]
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Render NEON variables files as a readable data dictionary.")
    p.add_argument("--root", help="Search this tree for variables files")
    p.add_argument("--file", nargs="+", help="Specific variables file(s)")
    p.add_argument("--table", help="Restrict to one table, e.g. ST_30_minute")
    p.add_argument("--grep", help="Only fields matching this (name or description)")
    p.add_argument("--md", help="Write markdown here")
    p.add_argument("--csv", help="Write tidy csv here")
    p.add_argument("--no-key", action="store_true",
                   help="Omit the suffix conventions key")
    a = p.parse_args(argv)

    if not a.root and not a.file:
        p.error("give --root or --file")

    paths = list(a.file) if a.file else find_variables_files(a.root)
    if not paths:
        raise SystemExit(f"No variables files under {a.root!r}")
    print(f"Reading {len(paths)} variables file(s)", file=sys.stderr)

    df = load_variables(paths)
    if a.table and "table" in df.columns:
        df = df[df["table"].astype(str).str.contains(a.table, case=False, na=False)]
    if a.grep:
        hay = (df["fieldName"].astype(str) + " "
               + df.get("description", pd.Series("", index=df.index)).astype(str))
        df = df[hay.str.contains(a.grep, case=False, na=False)]
    if df.empty:
        raise SystemExit("Nothing matched those filters.")

    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"Wrote {a.csv}")
    if a.md:
        with open(a.md, "w") as fh:
            gcols = [c for c in ("product", "table") if c in df.columns]
            for key, g in df.groupby(gcols, sort=True):
                head = " / ".join(str(k) for k in
                                  (key if isinstance(key, tuple) else (key,)))
                fh.write(f"\n## {head}\n\n")
                fh.write("| field | units | type | description |\n")
                fh.write("|---|---|---|---|\n")
                for r in g.itertuples():
                    d = str(getattr(r, "description", "") or "").replace("|", "/")
                    fh.write(f"| `{r.fieldName}` | {getattr(r, 'units', '')} | "
                             f"{getattr(r, 'dataType', '')} | {d} |\n")
        print(f"Wrote {a.md}")
    if not a.csv and not a.md:
        print(render(df))
        if not a.no_key:
            print(suffix_key())
    return 0


if __name__ == "__main__":
    sys.exit(main())
