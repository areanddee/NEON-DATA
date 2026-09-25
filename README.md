# NEON-DATA

Tools for pulling NSF NEON soil and atmospheric data and turning it into
analysis-ready time series.

**Sites:** STER (North Sterling, CO, D10 Central Plains) and NOGP (Northern
Great Plains Research Laboratory, ND, D09 Northern Plains).

**Goal:** a soil "digital twin" predicting soil state and microbial activity
from continuous environmental forcing.

| Script | What it does |
|---|---|
| `neon_pull.py` | Downloads any product/site/date range and stacks it. Wraps `neonutilities`. |
| `neon_dict.py` | Renders NEON's `variables` files as a readable data dictionary. |
| `neon_soil.py` | Subsets a soil profile product by plot and depth, reports gaps, plots time series. |

---

## 1. Quick start on Medicine Bow

### 1.1 Group access

The project space is mode 770, so you must be in the `cowy-hydro-ml` group
before you can see any of it. Logging in successfully is not the same thing.

```bash
id
cd /project/cowy-hydro-ml && ls
```

If `cowy-hydro-ml` is missing from `id`, file a ticket with ARCC. That gates
everything else. Note the path is `/project`, singular.

### 1.2 Clone

```bash
git clone git@github.com:areanddee/NEON-DATA.git
cd NEON-DATA
```

Clone per user. Two people sharing one working tree is a permissions and merge
mess. Code comes from git; data lives in `/project` and is never committed.

### 1.3 Build the conda environment (one time, by whoever gets there first)

`arcc/1.0` is already sticky-loaded at login, so `miniconda3` is the only
module needed. The ARCC module does its own shell initialization, so
`conda activate` works immediately afterward. Do **not** run `conda init`.

```bash
module load miniconda3/24.3.0

# keep conda's multi-GB package cache out of a quota-limited home dir
export CONDA_PKGS_DIRS=/project/cowy-hydro-ml/software/conda-pkgs

conda env create -f environment.yml \
    --prefix /project/cowy-hydro-ml/software/envs/neon
```

Everyone after that just activates it:

```bash
module load miniconda3/24.3.0
conda activate /project/cowy-hydro-ml/software/envs/neon
```

Rollback, if the build goes wrong:

```bash
conda env remove --prefix /project/cowy-hydro-ml/software/envs/neon
```

**Do not install these packages into a JAX or training environment.** Nothing
here needs JAX, and a dependency solve for pandas can move numpy out from
under `jaxlib`, which fails confusingly and much later.

If activation or imports feel sluggish from the shared filesystem (conda envs
are tens of thousands of small files), fall back to a per-user env built from
the same file: `conda env create -f environment.yml -n neon`.

### 1.4 Verify the environment

```bash
python -c "import pandas, matplotlib, neonutilities, pyarrow; print('pandas', pandas.__version__)"
```

Expected: `pandas 3.0.5`. Anything else means the pin did not take and the
resample-alias behavior in §4 will differ between users.

Versions from the reference build (2026-09-25, Medicine Bow, Python 3.11):

| Package | Version |
|---|---|
| pandas | 3.0.5 (pinned) |
| neonutilities | 2.0.2 (pinned) |
| numpy | 2.4.6 |
| matplotlib | 3.11.2 |
| pyarrow | 25.0.1 |
| duckdb | 1.5.5 |
| h5py | 3.16.0 |
| pyproj | 3.7.2 |

Note that this environment resolves a different numpy than a JAX training
environment will. That is the point of keeping them separate: installing these
packages alongside `jaxlib` can move numpy out from under it.

### 1.5 Get your own NEON API token

See [Appendix A](#appendix-a--getting-your-own-neon-api-token). Tokens are tied
to individual accounts and are not shared. **Never commit one.**

### 1.6 Shell setup

Add to `~/.bashrc`:

```bash
alias neon='module load miniconda3/24.3.0 && conda activate /project/cowy-hydro-ml/software/envs/neon'

export NEON_ROOT=/project/cowy-hydro-ml/neon/data/NEON_temp-soil
export NEON_TOKEN='paste-your-token-here'
```

Then `neon` at any prompt drops you into the environment, from a fresh login
or after a dropped connection.

`$NEON_ROOT` must point at wherever the NEON downloads actually live. The
shared location above is the intent; if the data is still under a personal
directory, either move it or change this line, but the whole group needs read
access to whichever path is used.

**Do not put `module load` itself in `~/.bashrc`.** It writes to stderr, and
any output from `~/.bashrc` in a non-interactive shell breaks `scp` and
`rsync` with errors that point nowhere near the cause. An alias only runs when
invoked, so it sidesteps the problem. Plain `export` lines produce no output
and are safe.

To check whether your startup files are noisy, from your laptop:

```bash
ssh <user>@medicinebow.arcc.uwyo.edu 'true'
```

Any output at all is a problem. Silence is correct.

### 1.7 Verify the token

```bash
curl -s -D - -o /dev/null -H "X-API-Token: $NEON_TOKEN" \
  https://data.neonscience.org/api/v0/products/DP1.00094.001 | grep -i ratelimit
```

| Response | Meaning |
|---|---|
| `x-ratelimit-limit: 2000` | Working |
| `x-ratelimit-limit: 200` | **Not** being seen; you are at the anonymous rate |
| nothing | `$NEON_TOKEN` is empty, or the shell was not re-sourced |

A missing token does not error. It silently slows you down, which is why this
check is worth running once up front.

### 1.8 Where to run what

**Download on a login node** (`mblog1`, `mblog2`). Compute nodes on most
ARCC-style systems have no outbound network, so `neon_pull.py` will hang or
fail there. If a download stalls with no error, check this first.

Analysis and plotting run anywhere.

**Run long pulls inside `tmux` or `screen`.** A multi-product, multi-year
download runs long enough that a dropped ssh connection becomes a real risk,
and a bare foreground job dies with the session.

```bash
tmux new -s neon          # start
                          # ctrl-b then d to detach
tmux attach -t neon       # reattach later, from any session
```

If neither is on `PATH`, try `module avail tmux`.

Nothing is lost if a connection drops between commands: the conda environment
and any completed downloads persist on disk. Reconnect, re-activate with the
`neon` alias, and continue.

---

## 2. What to download

### Tier 1: core soil state (start here)

Continuous sensor data, 30-minute cadence. The twin's state variables.

| Product | Variable | Notes |
|---|---|---|
| `DP1.00041.001` | Soil temperature | 5 plots x 9 depths at STER |
| `DP1.00094.001` | Soil water content + salinity | `VSWC`, `VSIC`. See depth warning in §4. |
| `DP1.00095.001` | Soil CO2 concentration | Shallow only, ~3 levels. Closest proxy for microbial activity. |
| `DP1.00040.001` | Soil heat flux plate | Surface energy balance |

### Tier 2: atmospheric forcing

| Product | Variable |
|---|---|
| `DP1.00045.001` | Precipitation, tipping bucket |
| `DP1.00002.001` | Single aspirated air temperature |
| `DP1.00098.001` | Relative humidity |
| `DP1.00023.001` | Shortwave + longwave radiation (net radiometer) |
| `DP1.00024.001` | PAR |
| `DP1.00001.001` | 2D wind speed and direction |
| `DP1.00004.001` | Barometric pressure |

Barometric pressure is not optional if you work with soil CO2: concentration
and partial pressure differ, and the conversion needs pressure.

STER does **not** publish `DP1.00006.001` (bundled precipitation). It publishes
tipping bucket and throughfall separately. Never assume two sites carry the
same instruments.

### Tier 3: biological targets (periodic, not continuous)

*Observational* products: field crews sampling a few times a year. These are
what you would predict, not what you predict from.

| Product | Variable |
|---|---|
| `DP1.10081.001` / `.002` | Soil microbe community composition / taxonomy |
| `DP1.10104.001` | Soil microbe biomass |
| `DP1.10109.001` | Soil microbe group abundances |
| `DP1.10086.001` | Soil physical + chemical properties, periodic |
| `DP1.10023.001` | Herbaceous clip harvest (aboveground biomass) |
| `DP1.10055.001` | Plant phenology observations |

**Sampling-rate mismatch is the central research problem.** Forcing is
30-minute; microbial sampling is a handful of bouts per year. Four orders of
magnitude apart. Check actual bout and plot counts before designing anything
around these.

`DP4.00200.001` (bundled eddy covariance) is large and structurally different;
use `nu.stack_eddy()` rather than the normal path.

---

## 3. Workflow

### Step 1: what does the site have?

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP --dates
```

Prints release tags and available months. If the product is not at that site,
it prints the site's full catalog instead. Read that list.

`RELEASE-2026` is curated and citable. `PROVISIONAL` can be revised or
withdrawn; the scripts exclude it by default. Keep it that way.

### Step 2: look before you leap

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP \
    --start 2024-06 --end 2024-06 --timeindex 30 --in-memory --peek
```

`--peek` prints every table's columns, the distinct horizontal and vertical
positions, and the first rows. This is how you learn real column names instead
of guessing them.

### Step 3: download and stack

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP \
    --start 2024-01 --end 2024-12 --timeindex 30
```

Lands in `$NEON_ROOT/NOGP/DP1.00094.001/filesToStack00094/stackedFiles/`: one
CSV per table, plus `variables`, `sensor_positions`, `readme`, issue log, and
citation.

For observational products `--timeindex` does not apply (the script ignores it
automatically). Use `--table` instead.

### Step 4: decode the variables

```bash
python neon_dict.py --root "$NEON_ROOT/NOGP/DP1.00094.001"
python neon_dict.py --root "$NEON_ROOT" --grep "water content"
python neon_dict.py --root "$NEON_ROOT" --md dictionary.md
```

Suffix conventions, shared across all sensor products:

| Suffix | Meaning |
|---|---|
| `Mean` / `Minimum` / `Maximum` / `Variance` | statistics over the interval |
| `NumPts` | raw points behind the average (0.1 Hz sampling, so 180 per 30 min) |
| `ExpUncert` | expanded uncertainty, ~95% half-width |
| `StdErMean` | standard error of the mean |
| `finalQF` | **0 = passed, 1 = failed** |
| `AlphaQM` / `BetaQM` / `PassQM` | percent failing / unevaluable / passing |

### Step 5: find the real depths

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" --inventory
```

Prints the plot x level grid and the **actual depths in cm** from
`sensor_positions`.

### Step 6: check coverage, per plot

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" \
    --ver 502 503 --hor all --year 2024 --no-plot
```

Compare counts against the theoretical maximum: **17,520 for a normal year,
17,568 for a leap year** (365 or 366 days x 48 half-hours).

### Step 7: extract and inspect

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" \
    --hor 004 --ver 502 503 --year 2024 --gap-report \
    --out-csv out/nogp_2024.csv --out-png out/nogp_2024.png
```

`--gap-report` shows how missing intervals are distributed: how many separate
gaps, how many are isolated single slots, and the start and duration of the
longest. Add `--resample D` for daily means on the plot; the CSV stays at
native cadence either way.

---

## 4. Gotchas

These cost real time.

**File counts are not data coverage.** Every plot/level at STER shows 84 of 84
monthly files for 2018-2024, which looks perfect. But plot 001 at 16 cm lost
**more than half** of 2024: NEON publishes a monthly file whether or not the
sensor was alive. Always check *row* counts after QA/QC.

**VER is an ordinal, not a depth.** Level 502 is 6 cm at STER plots 001 and
003, 5 cm at plot 002, and 7 cm at plots 004 and 005. Depths are set by each
plot's soil horizons. Always read `sensor_positions`. NOGP will differ again.

**Gaps are absent, not NaN.** Stacked tables contain only rows NEON published.
Plotting a datetime-indexed series draws a straight line across a two-week
outage and it looks like data. Use `neon_soil.regular_grid()` to reindex onto
a complete grid before plotting, resampling, or windowing.

**Moisture depths are wrong in `sensor_positions`.** NEON says so, for
`DP1.00094.001` specifically, and ships `swc_depthsV3.csv` with the real
installation depths. Check whether it appears in your stacked output.
`neon_soil.py` reads `zOffset`, which is right for temperature and wrong for
moisture.

**Precipitation sums, it does not average.** Use `.sum()` when resampling.
The absence of `Mean` and `NumPts` columns is the tell.

**Tipping buckets undercatch frozen precipitation.** Roughly Nov-Mar the gauge
underreports and mistimes snow. At STER 2024, 24% of intervals were flagged
and the unflagged sum (445 mm) is a *floor*, not the annual total. Do not
treat winter precipitation as reliable model input.

**All timestamps are UTC.** Sterling and Mandan are UTC-6/-7. Building
hour-of-day features straight from `startDateTime` puts the diurnal maximum
near hour 21 instead of 14. Store UTC; derive local time as a feature.

**pandas 3.x resample aliases are lowercase.** `6h` works; `6H` raises
`ValueError: Invalid frequency`. `D` and `W` are unaffected.

**`nu.list_available_dates()` crashes on some products** with
`TypeError: object of type 'NoneType' has no len()` (an airborne-oriented
function with a missing null check). Use `neon_pull.py --dates`, which routes
around it.

**`check_size=False`.** `load_by_product` defaults to prompting interactively
about download size, which hangs batch jobs. The script forces it off.

---

## 5. Site contrast: STER vs NOGP

A deliberate contrast, not a replicate pair.

|  | STER | NOGP |
|---|---|---|
| Domain | D10 Central Plains | D09 Northern Plains |
| Location | North Sterling, CO | ~10 km W of Bismarck, ND |
| Ecosystem | Shortgrass steppe / cropland | Northern mixed-grass prairie / cropland |
| Soils | - | Mollisols (Typic Argiustolls), thick mollic horizons |
| Mean annual temp | - | ~5.9 °C |
| Mean annual precip | ~445 mm (2024, measured) | ~455 mm |

NOGP is colder with a longer freeze season, so more precipitation falls as snow
and the frozen-soil problems above bite harder. Its Mollisols carry much more
organic matter than STER's. That contrast is the scientific point.

Confirm NOGP's own numbers with `--dates` and `--inventory` before relying on
that table. The STER precipitation figure is measured from our own 2024 pull;
the NOGP figures come from NEON's site description page.

---

## 6. Suggested first task

1. Run `--dates` on NOGP for all four Tier 1 products. Note which exist and
   whether 2024 is fully released.
2. Pull soil temperature for NOGP, 2024, 30-minute.
3. Run `--inventory` and record NOGP's actual depth ladder.
4. Run the per-plot coverage check and pick the healthiest plot.
5. Extract that plot at the two shallowest levels with `--gap-report`, and
   plot the year.
6. Compare against STER's figure. Seasonal amplitude, winter behavior, and
   diurnal damping with depth should all differ, and those differences are
   physically interpretable.

The output of steps 3 and 4 is what's needed before any modeling conversation.

---

## Appendix A — Getting your own NEON API token

**Do not use someone else's.** NEON states that a token is unique to your
account and should not be shared. Rate limits are tracked per token, so two
people sharing one contend for the same budget. Tokens also let NEON see who
is using which data, which is visibility this collaboration wants.

### Step 1: create a NEON user account

- Data Portal: https://data.neonscience.org
- Account info: https://www.neonscience.org/data/about-data/data-portal-user-accounts

Use your university email address.

### Step 2: generate the token

1. Sign in at https://data.neonscience.org
2. Go to your **My Account** profile page
3. Scroll to the **bottom** of that page
4. Click **GET API TOKEN**
5. Click **Copy** when it appears

The token is a very long string. Copy it; do not retype it.

### Step 3: store it

`~/.bashrc` on the cluster, as in §1.6. On a shared system, check your
startup files are not world-readable:

```bash
chmod 600 ~/.bashrc
ls -ld ~
```

For code you will share publicly, never paste a token into a script. Either
keep `NEON_TOKEN = "..."` in an uncommitted `neon_token_source.py` and import
it, or use `python-dotenv` with a `.env` file. Both filenames are already in
`.gitignore`.

The scripts read `os.environ["NEON_TOKEN"]`, so the `.bashrc` approach needs no
code changes.

### Step 4: verify

See §1.7.

### Reference

NEON's own tutorial, with screenshots:
https://www.neonscience.org/resources/learning-hub/tutorials/neon-api-tokens-tutorial
