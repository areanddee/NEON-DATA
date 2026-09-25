#!/usr/bin/env python3
"""
neon_soil.py
============

Walk an unzipped NEON instrumented-systems (IS) download, pull a chosen
HOR/VER subset, and produce (a) a tidy long-format table and (b) a
time-series plot for a given year.

Built for DP1.00041.001 (soil temperature) but written generically, so it
also works for DP1.00094.001 (soil water content / salinity),
DP1.00095.001 (soil CO2), DP1.00096.001 (soil heat flux), etc.

NEON IS file naming convention
------------------------------
    NEON.DOM.SITE.DPL.PRNUM.REV.HOR.VER.TMI.DESC.YYYY-MM.PKGTYPE.GENTIME.csv

e.g. NEON.D10.STER.DP1.00041.001.003.502.030.ST_30_minute.2019-07.basic.20211220T024744Z.csv
     DOM=D10  SITE=STER  HOR=003 (soil plot 3)  VER=502 (measurement level 2)
     TMI=030 (30-minute averaging)  DESC=ST_30_minute  month=2019-07

For soil sensors: HOR 001-005 = the five sensor-based soil plots,
VER 501-509 = measurement levels down the profile (shallow to deep).
Actual depths come from the sensor_positions file (zOffset, metres,
negative = below the soil surface), not from the VER index, because
depths differ site to site.

Usage
-----
    # 1. See what is actually in the download (do this first)
    python neon_soil.py --root /path/to/NEON_temp-soil --inventory

    # 2. Pull a subset and plot one year
    python neon_soil.py --root /path/to/NEON_temp-soil \
        --hor 003 --ver 501 502 504 --year 2021 \
        --out-csv ster_soilT_2021.csv --out-png ster_soilT_2021.png

    # 3. Export the whole 2018-2025 record for one profile, no plot
    python neon_soil.py --root /path/to/NEON_temp-soil \
        --hor 003 --ver all --out-csv ster_profile3_all.csv --no-plot

Author: written with Claude for the AreandDee soil-twin work.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from glob import glob

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# Filename parsing
# ----------------------------------------------------------------------

# NEON.D10.STER.DP1.00041.001.003.502.030.ST_30_minute.2019-07.basic.2021....csv
FNAME_RE = re.compile(
    r"^NEON\."
    r"(?P<dom>D\d{2})\."
    r"(?P<site>[A-Z]{4})\."
    r"(?P<dpl>DP\d)\."
    r"(?P<prnum>\d{5})\."
    r"(?P<rev>\d{3})\."
    r"(?P<hor>\d{3})\."
    r"(?P<ver>\d{3})\."
    r"(?P<tmi>\d{3})\."
    r"(?P<desc>[^.]+)\."
    r"(?P<month>\d{4}-\d{2})\."
    r"(?P<pkg>basic|expanded)\."
    r"(?P<gentime>[^.]+)\.csv$"
)


def scan_files(root: str) -> pd.DataFrame:
    """Recursively find every NEON IS data csv under root and parse its name."""
    rows = []
    for path in glob(os.path.join(root, "**", "*.csv"), recursive=True):
        m = FNAME_RE.match(os.path.basename(path))
        if m:
            d = m.groupdict()
            d["path"] = path
            rows.append(d)
    if not rows:
        raise SystemExit(
            f"No NEON IS data csvs found under {root!r}.\n"
            "Point --root at the folder that contains the monthly "
            "NEON.<DOM>.<SITE>.DP1.<prnum>.001.<YYYY-MM>.<pkg>.<gentime> directories."
        )
    df = pd.DataFrame(rows)
    df["month_dt"] = pd.to_datetime(df["month"], format="%Y-%m")
    # If NEON reissued a month, keep only the newest generation time.
    key = ["site", "prnum", "hor", "ver", "tmi", "month", "pkg"]
    df = (df.sort_values("gentime")
            .drop_duplicates(subset=key, keep="last")
            .reset_index(drop=True))
    return df


# ----------------------------------------------------------------------
# sensor_positions: HOR.VER -> actual depth
# ----------------------------------------------------------------------

def load_sensor_positions(root: str) -> pd.DataFrame:
    """
    Read every sensor_positions file under root and return a deduplicated
    table with columns: hor, ver, zOffset (m), depth_cm, and any date range.
    Handles both the older 'HOR.VER' single-column layout and the newer
    horizontalPosition / verticalPosition layout.
    """
    paths = glob(os.path.join(root, "**", "*sensor_positions*.csv"), recursive=True)
    if not paths:
        return pd.DataFrame(columns=["hor", "ver", "zOffset", "depth_cm"])

    frames = []
    for p in paths:
        try:
            sp = pd.read_csv(p, dtype=str)
        except Exception as exc:  # pragma: no cover
            print(f"  ! could not read {os.path.basename(p)}: {exc}", file=sys.stderr)
            continue

        if "HOR.VER" in sp.columns:
            hv = sp["HOR.VER"].astype(str).str.split(".", expand=True)
            sp["hor"], sp["ver"] = hv[0].str.zfill(3), hv[1].str.zfill(3)
        elif {"horizontalPosition", "verticalPosition"} <= set(sp.columns):
            sp["hor"] = sp["horizontalPosition"].astype(str).str.zfill(3)
            sp["ver"] = sp["verticalPosition"].astype(str).str.zfill(3)
        else:
            continue

        keep = ["hor", "ver"]
        for c in ("zOffset", "positionStartDateTime", "positionEndDateTime",
                  "referenceLatitude", "referenceLongitude", "referenceElevation"):
            if c in sp.columns:
                keep.append(c)
        frames.append(sp[keep])

    if not frames:
        return pd.DataFrame(columns=["hor", "ver", "zOffset", "depth_cm"])

    pos = pd.concat(frames, ignore_index=True)
    if "zOffset" in pos.columns:
        pos["zOffset"] = pd.to_numeric(pos["zOffset"], errors="coerce")
        # zOffset is metres, negative below surface. Report positive depth in cm.
        pos["depth_cm"] = (-pos["zOffset"] * 100).round(1)
    else:
        pos["zOffset"] = np.nan
        pos["depth_cm"] = np.nan

    pos = (pos.sort_values(["hor", "ver"])
              .drop_duplicates(subset=["hor", "ver", "zOffset"])
              .reset_index(drop=True))
    return pos


def ensure_parent(path: str | None) -> None:
    """Create the parent directory of an output path if it does not exist."""
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
        print(f"Created output directory {parent}")


def pick_default_desc(files: pd.DataFrame) -> str:
    """Prefer the coarsest averaging interval, e.g. ST_30_minute over ST_1_minute."""
    tmi = files.groupby("desc")["tmi"].max().astype(int)
    return tmi.idxmax()


# ----------------------------------------------------------------------
# The mislabelled NEON.DOC "pdf" files
# ----------------------------------------------------------------------

def doc_titles(root: str) -> None:
    """
    NEON ships supporting docs named *.pdf that are often actually zip
    archives of per-page jpeg + txt. Print the real title of each so you can
    tell which are worth reading.
    """
    import zipfile
    paths = sorted(glob(os.path.join(root, "**", "NEON.*.pdf"), recursive=True))
    if not paths:
        print("No NEON.DOC / NEON.QSG files found under root.")
        return
    print(f"\n=== {len(paths)} SUPPORTING DOCUMENTS ===")
    for p in paths:
        name = os.path.basename(p)
        try:
            with open(p, "rb") as fh:
                magic = fh.read(4)
            if magic[:2] == b"PK":
                with zipfile.ZipFile(p) as z:
                    txt = z.read("1.txt").decode("utf-8", "replace")
                title = " ".join(txt.split("\r\n")[0:2])[:160]
                kind = "zip"
            elif magic == b"%PDF":
                import subprocess
                txt = subprocess.run(["pdftotext", "-f", "1", "-l", "1", p, "-"],
                                     capture_output=True, text=True).stdout
                title = " ".join(txt.strip().splitlines()[:2])[:160]
                kind = "pdf"
            else:
                title, kind = "(unrecognised format)", magic.hex()
        except Exception as exc:
            title, kind = f"(could not read: {exc})", "?"
        print(f"  {name:<32} [{kind}] {title}")


# ----------------------------------------------------------------------
# Inventory
# ----------------------------------------------------------------------

def inventory(files: pd.DataFrame, pos: pd.DataFrame) -> None:
    print("\n=== SITES / PRODUCTS / AVERAGING INTERVALS ===")
    grp = (files.groupby(["site", "prnum", "desc", "tmi", "pkg"])
                .agg(n_files=("path", "size"),
                     first=("month", "min"),
                     last=("month", "max"))
                .reset_index())
    print(grp.to_string(index=False))

    print("\n=== HOR x VER GRID (file counts, all months) ===")
    for (site, prnum, desc), sub in files.groupby(["site", "prnum", "desc"]):
        print(f"\n-- {site} / DP1.{prnum} / {desc}")
        pivot = sub.pivot_table(index="ver", columns="hor",
                                values="path", aggfunc="size", fill_value=0)
        print(pivot.to_string())

    if not pos.empty and pos["depth_cm"].notna().any():
        print("\n=== SENSOR DEPTHS from sensor_positions (cm below surface) ===")
        dep = pos.pivot_table(index="ver", columns="hor",
                              values="depth_cm", aggfunc="mean")
        print(dep.round(1).to_string())
    else:
        print("\n(no sensor_positions file found, so depths are unknown; "
              "VER 501 is shallowest and increases downward)")

    print("\n=== MONTH COVERAGE ===")
    months = sorted(files["month"].unique())
    print(f"{len(months)} distinct months: {months[0]} .. {months[-1]}")
    full = pd.period_range(months[0], months[-1], freq="M").astype(str)
    missing = [m for m in full if m not in set(months)]
    if missing:
        print(f"missing months ({len(missing)}): {missing}")
    else:
        print("no gaps in the monthly sequence")


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------

def detect_value_columns(df: pd.DataFrame) -> list[str]:
    """Return the statistic columns for whichever NEON IS product this is."""
    skip = {"startDateTime", "endDateTime", "horizontalPosition",
            "verticalPosition", "publicationDate", "release"}
    return [c for c in df.columns if c not in skip]


def load_subset(files: pd.DataFrame,
                hor: list[str] | None,
                ver: list[str] | None,
                desc: str | None,
                tmi: str | None,
                years: list[int] | None,
                value_col: str | None,
                qf_max: int | None,
                months: list[str] | None = None,
                verbose: bool = True) -> pd.DataFrame:
    sel = files.copy()
    if desc:
        sel = sel[sel["desc"] == desc]
    if tmi:
        sel = sel[sel["tmi"] == tmi.zfill(3)]
    if months:
        sel = sel[sel["month"].isin(months)]
    if hor:
        sel = sel[sel["hor"].isin([h.zfill(3) for h in hor])]
    if ver:
        sel = sel[sel["ver"].isin([v.zfill(3) for v in ver])]
    if years:
        sel = sel[sel["month_dt"].dt.year.isin(years)]

    if sel.empty:
        raise SystemExit("Selection matched no files. Re-run with --inventory "
                         "to see what HOR/VER/DESC values exist.")

    if verbose:
        print(f"Reading {len(sel)} files "
              f"({sel['month'].min()} .. {sel['month'].max()}, "
              f"HOR={sorted(sel['hor'].unique())}, "
              f"VER={sorted(sel['ver'].unique())})")

    frames = []
    for i, r in enumerate(sel.sort_values(["hor", "ver", "month"]).itertuples(), 1):
        try:
            d = pd.read_csv(r.path)
        except Exception as exc:
            print(f"  ! skipping {os.path.basename(r.path)}: {exc}", file=sys.stderr)
            continue
        if d.empty:
            continue
        d["hor"] = r.hor
        d["ver"] = r.ver
        d["site"] = r.site
        d["desc"] = r.desc
        frames.append(d)
        if verbose and i % 100 == 0:
            print(f"  ... {i}/{len(sel)}")

    if not frames:
        raise SystemExit("All matched files were empty or unreadable.")

    df = pd.concat(frames, ignore_index=True)
    df["startDateTime"] = pd.to_datetime(df["startDateTime"], utc=True,
                                         format="mixed", errors="coerce")
    if "endDateTime" in df.columns:
        df["endDateTime"] = pd.to_datetime(df["endDateTime"], utc=True,
                                           format="mixed", errors="coerce")
    df = df.dropna(subset=["startDateTime"])

    # Pick the value column if not specified: prefer a *Mean column.
    if value_col is None:
        cands = [c for c in df.columns if c.endswith("Mean")
                 and not c.endswith("StdErMean")]
        if not cands:
            cands = detect_value_columns(df)
        value_col = cands[0]
        if verbose:
            print(f"Value column: {value_col} "
                  f"(others available: {[c for c in detect_value_columns(df)]})")
    if value_col not in df.columns:
        raise SystemExit(f"--value-col {value_col!r} not in {list(df.columns)}")

    df["value"] = pd.to_numeric(df[value_col], errors="coerce")
    df["value_col"] = value_col

    # QA/QC. finalQF == 0 means the record passed all tests.
    if qf_max is not None and "finalQF" in df.columns:
        before = df["value"].notna().sum()
        df.loc[pd.to_numeric(df["finalQF"], errors="coerce") > qf_max, "value"] = np.nan
        after = df["value"].notna().sum()
        if verbose:
            pct = 100 * (before - after) / max(before, 1)
            print(f"QA/QC: dropped {before - after} of {before} values "
                  f"({pct:.1f}%) with finalQF > {qf_max}")

    df = df.sort_values(["hor", "ver", "startDateTime"]).reset_index(drop=True)
    return df


def attach_depths(df: pd.DataFrame, pos: pd.DataFrame) -> pd.DataFrame:
    if pos.empty:
        df["depth_cm"] = np.nan
        return df
    lut = (pos.groupby(["hor", "ver"])["depth_cm"].mean().reset_index())
    return df.merge(lut, on=["hor", "ver"], how="left")


def label(hor: str, ver: str, depth_cm: float) -> str:
    if pd.notna(depth_cm):
        return f"plot {int(hor)}, {depth_cm:.0f} cm (VER {ver})"
    return f"plot {int(hor)}, VER {ver}"


# ----------------------------------------------------------------------
# Gaps
# ----------------------------------------------------------------------

def regular_grid(s: pd.Series) -> pd.Series:
    """
    Reindex a time-indexed Series onto a complete, evenly spaced grid at its
    own median sampling interval. Timestamps NEON never published become NaN
    instead of vanishing, so a gap plots as a break rather than as a straight
    line drawn across it.
    """
    if len(s) < 3:
        return s
    step = s.index.to_series().diff().median()
    if pd.isna(step) or step <= pd.Timedelta(0):
        return s
    full = pd.date_range(s.index.min(), s.index.max(), freq=step)
    return s.reindex(full)


def gap_report(df: pd.DataFrame, top: int = 5) -> None:
    """Print, per position, how the missing half-hours are distributed."""
    print("\n=== GAP STRUCTURE (missing or QA/QC-failed) ===")
    for (hor, ver), g in df.groupby(["hor", "ver"], sort=True):
        s = g.set_index("startDateTime")["value"]
        s = regular_grid(s[~s.index.duplicated(keep="first")])
        na = s.isna()
        n_exp, n_bad = len(s), int(na.sum())
        depth = g["depth_cm"].dropna()
        depth = depth.iloc[0] if len(depth) else np.nan
        print(f"\n-- {label(hor, ver, depth)}")
        print(f"   expected {n_exp:,} slots, missing {n_bad:,} "
              f"({100 * n_bad / max(n_exp, 1):.2f}%)")
        if n_bad == 0:
            continue
        # contiguous runs of NaN
        runs = na.ne(na.shift()).cumsum()[na]      # indexed by the NaN timestamps
        sizes = runs.value_counts().sort_values(ascending=False)
        step = s.index.to_series().diff().median()
        singles = int((sizes == 1).sum())
        print(f"   {len(sizes)} separate gaps; {singles} are a single slot")
        print(f"   longest {min(top, len(sizes))}:")
        for run_id, n in sizes.head(top).items():
            t0 = runs.index[runs == run_id][0]
            dur = n * step
            print(f"     {t0:%Y-%m-%d %H:%M} UTC  {n:>5,} slots  ({dur})")


# ----------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------

def plot_series(df: pd.DataFrame, year: int | None, out_png: str | None,
                resample: str | None, title: str | None) -> None:
    import matplotlib
    if out_png:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    d = df
    if year is not None:
        d = d[d["startDateTime"].dt.year == year]
        if d.empty:
            raise SystemExit(f"No records in {year} for this selection.")

    groups = list(d.groupby(["hor", "ver"], sort=True))
    fig, ax = plt.subplots(figsize=(13, 5.5))
    cmap = plt.get_cmap("viridis", max(len(groups), 2))

    for i, ((hor, ver), g) in enumerate(groups):
        s = g.set_index("startDateTime")["value"]
        s = s[~s.index.duplicated(keep="first")]
        s = regular_grid(s)          # gaps become NaN -> matplotlib breaks the line
        if resample:
            s = s.resample(resample).mean()
        depth = g["depth_cm"].dropna()
        depth = depth.iloc[0] if len(depth) else np.nan
        ax.plot(s.index, s.values, lw=(0.8 if resample else 0.45), color=cmap(i),
                label=label(hor, ver, depth))

    vcol = df["value_col"].iloc[0]
    units = {"soilTempMean": "deg C", "VSWCMean": "m3/m3",
             "VSICMean": "S/m", "soilCO2concentrationMean": "ppm",
             "SHFMean": "W/m2"}.get(vcol, "")
    ax.set_ylabel(f"{vcol}" + (f" [{units}]" if units else ""))
    ax.set_xlabel("date (UTC)")
    site = df["site"].iloc[0]
    ax.set_title(title or
                 f"NEON {site} {vcol}" + (f", {year}" if year else "") +
                 (f", {resample} means" if resample else ""))
    ax.grid(alpha=0.3, lw=0.5)
    ax.legend(fontsize=8, ncol=2, loc="best", framealpha=0.9)
    if year is not None:
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    fig.tight_layout()

    if out_png:
        fig.savefig(out_png, dpi=150)
        print(f"Wrote {out_png}")
    else:
        plt.show()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Subset and plot NEON instrumented-systems soil sensor data.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--root", required=True,
                   help="Top of the unzipped NEON download")
    p.add_argument("--inventory", action="store_true",
                   help="Print what is in the download, then exit")
    p.add_argument("--docs", action="store_true",
                   help="Identify the NEON.DOC supporting files, then exit")
    p.add_argument("--month", nargs="+", default=None,
                   help="Restrict to specific months, e.g. --month 2018-01 2018-02")
    p.add_argument("--hor", nargs="+", default=None,
                   help="Horizontal positions (soil plots), e.g. --hor 001 003. "
                        "Omit or 'all' for every plot.")
    p.add_argument("--ver", nargs="+", default=None,
                   help="Vertical positions (depth levels), e.g. --ver 501 502. "
                        "Omit or 'all' for every level.")
    p.add_argument("--desc", default=None,
                   help="Table name, e.g. ST_30_minute or ST_1_minute. "
                        "Defaults to the most common one in the download.")
    p.add_argument("--tmi", default=None,
                   help="Averaging interval index, e.g. 030 or 001")
    p.add_argument("--year", type=int, default=None,
                   help="Year to plot. Loading is restricted to this year "
                        "unless --load-years is given.")
    p.add_argument("--load-years", nargs="+", type=int, default=None,
                   help="Years to load (defaults to --year, else all)")
    p.add_argument("--value-col", default=None,
                   help="Column to plot, e.g. soilTempMean. Auto-detected by default.")
    p.add_argument("--qf-max", type=int, default=0,
                   help="Keep records with finalQF <= this. Use -1 to disable filtering.")
    p.add_argument("--resample", default=None,
                   help="Pandas offset for averaging before plotting, e.g. D, 6h, W. "
                        "Lowercase h; uppercase H is invalid on pandas 3.x.")
    p.add_argument("--out-csv", default=None, help="Write tidy long table here")
    p.add_argument("--out-parquet", default=None, help="Write tidy long table here")
    p.add_argument("--out-png", default=None, help="Write the figure here")
    p.add_argument("--title", default=None)
    p.add_argument("--gap-report", action="store_true",
                   help="Print how the missing half-hours are distributed")
    p.add_argument("--no-plot", action="store_true")
    a = p.parse_args(argv)

    if a.docs:
        doc_titles(a.root)
        return 0

    files = scan_files(a.root)
    pos = load_sensor_positions(a.root)

    if a.inventory:
        inventory(files, pos)
        return 0

    desc = a.desc
    if desc is None:
        desc = pick_default_desc(files)
        print(f"Using table {desc} (override with --desc). "
              f"Available: {sorted(files['desc'].unique())}")

    hor = None if (a.hor is None or "all" in a.hor) else a.hor
    ver = None if (a.ver is None or "all" in a.ver) else a.ver

    years = a.load_years if a.load_years else ([a.year] if a.year else None)
    qf_max = None if a.qf_max is not None and a.qf_max < 0 else a.qf_max

    df = load_subset(files, hor, ver, desc, a.tmi, years, a.value_col, qf_max,
                     months=a.month)
    df = attach_depths(df, pos)

    out_cols = ["site", "hor", "ver", "depth_cm", "startDateTime",
                "endDateTime", "value", "value_col"]
    out_cols += [c for c in ("finalQF", "soilTempExpUncert", "VSWCExpUncert")
                 if c in df.columns]
    tidy = df[[c for c in out_cols if c in df.columns]]

    print(f"\nLoaded {len(tidy):,} rows, "
          f"{tidy['value'].notna().sum():,} passing QA/QC")
    print(tidy.groupby(["hor", "ver"])["value"]
              .agg(["count", "mean", "min", "max"]).round(2).to_string())

    if a.gap_report:
        gap_report(df)

    for path in (a.out_csv, a.out_parquet, a.out_png):
        ensure_parent(path)

    if a.out_csv:
        tidy.to_csv(a.out_csv, index=False)
        print(f"Wrote {a.out_csv}")
    if a.out_parquet:
        tidy.to_parquet(a.out_parquet, index=False)
        print(f"Wrote {a.out_parquet}")

    if not a.no_plot:
        plot_series(df, a.year, a.out_png, a.resample, a.title)

    return 0


if __name__ == "__main__":
    sys.exit(main())
