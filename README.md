# NEON Data Pipeline — Getting Started

**Sites:** STER (North Sterling, CO — D10 Central Plains) and NOGP (Northern
Great Plains Research Laboratory, ND — D09 Northern Plains)

**Goal:** build a soil "digital twin" from NEON data — predicting soil state and
microbial activity from continuous environmental forcing.

---

## 0. Setup (once)

You need an environment with `pandas`, `matplotlib`, and `neonutilities`, and a
NEON API token.

```bash
conda activate <your-env>
pip install neonutilities pandas matplotlib pyarrow
```

**Get your own token.** See **Appendix A** at the end of this guide for the
full procedure. You need your own — tokens are tied to individual accounts and
are not shared.

```bash
export NEON_TOKEN='...'
export NEON_ROOT=/project/cowy-hydro-ml/richardloft/NEON_temp-soil
```

Put both in your `~/.bashrc`. **Do not commit the token to git.**

Verify the token is actually registering:

```bash
curl -s -D - -o /dev/null -H "X-API-Token: $NEON_TOKEN" \
  https://data.neonscience.org/api/v0/products/DP1.00094.001 | grep -i ratelimit
```

`x-ratelimit-limit: 2000` means it works. `200` means it is not being seen.

**Where to work.** Run downloads and extraction on the cluster, next to the
data. Copy small stacked CSVs down to your laptop only when you want to look at
something.

---

## 1. The three scripts

| Script | What it does |
|---|---|
| `neon_pull.py` | Downloads any product/site/date range and stacks it. Wraps `neonutilities`. |
| `neon_dict.py` | Turns NEON's `variables` files into a readable data dictionary. |
| `neon_soil.py` | Subsets a soil profile product by plot and depth, reports gaps, plots time series. |

---

## 2. What to download

### Tier 1 — core soil state (start here)

Continuous sensor data, 30-minute cadence. These are the twin's state variables.

| Product | Variable | Notes |
|---|---|---|
| `DP1.00041.001` | Soil temperature | 5 plots x 9 depths at STER |
| `DP1.00094.001` | Soil water content + salinity | `VSWC`, `VSIC`. See depth warning below. |
| `DP1.00095.001` | Soil CO2 concentration | Shallow only, ~3 levels. Closest thing to a microbial-activity signal. |
| `DP1.00040.001` | Soil heat flux plate | Energy balance at the surface |

### Tier 2 — atmospheric forcing

What drives the soil from above.

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

### Tier 3 — biological targets (periodic, not continuous)

These are *observational* (OS) products: field crews sampling a few times a
year, not sensors. This is what you would predict, not what you predict from.

| Product | Variable |
|---|---|
| `DP1.10081.001` / `.002` | Soil microbe community composition / taxonomy |
| `DP1.10104.001` | Soil microbe biomass |
| `DP1.10109.001` | Soil microbe group abundances |
| `DP1.10086.001` | Soil physical + chemical properties, periodic |
| `DP1.10047.001` | Soil phys/chem, distributed initial characterization |
| `DP1.10023.001` | Herbaceous clip harvest (aboveground biomass) |
| `DP1.10055.001` | Plant phenology observations |

**Sampling-rate mismatch is the central problem.** Forcing is 30-minute;
microbial sampling is a handful of bouts per year. Four orders of magnitude
apart. Check the actual bout and plot counts before designing anything around
these.

### Not in Tier 1–3 but worth knowing

`DP4.00200.001` is the bundled eddy-covariance package (fluxes, NEE). Large and
structurally different; use `nu.stack_eddy()` rather than the normal path.

---

## 3. Workflow

### Step 1 — What does the site have?

**Never assume two sites carry the same instruments.** STER does not publish
`DP1.00006.001` (bundled precipitation) at all; it publishes tipping bucket and
throughfall separately. NOGP may differ again.

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP --dates
```

Prints release tags and available months. If the product is not at that site,
it prints the site's full product catalog instead — read that list.

Watch the release tags. `RELEASE-2026` is curated and citable. `PROVISIONAL`
data can be revised or withdrawn; the scripts exclude it by default. Keep it
that way.

### Step 2 — Look before you leap

Pull **one month** into memory and inspect the shape before committing to years
of data.

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP \
    --start 2024-06 --end 2024-06 --timeindex 30 --in-memory --peek
```

`--peek` prints every table's columns, the distinct horizontal and vertical
positions, and the first rows. This is how you learn the real column names
instead of guessing.

### Step 3 — Download and stack

```bash
python neon_pull.py --product DP1.00094.001 --site NOGP \
    --start 2024-01 --end 2024-12 --timeindex 30
```

Lands in:

```
$NEON_ROOT/NOGP/DP1.00094.001/filesToStack00094/stackedFiles/
```

One CSV per table, plus `variables`, `sensor_positions`, `readme`, the issue
log, and the citation.

For **observational** products, `--timeindex` does not apply (the script
ignores it automatically). Use `--table` to pick a table instead.

### Step 4 — Decode the variables

```bash
python neon_dict.py --root "$NEON_ROOT/NOGP/DP1.00094.001"
python neon_dict.py --root "$NEON_ROOT" --grep "water content"
python neon_dict.py --root "$NEON_ROOT" --md dictionary.md
```

Column-name suffix conventions, shared across all sensor products:

| Suffix | Meaning |
|---|---|
| `Mean` / `Minimum` / `Maximum` / `Variance` | statistics over the interval |
| `NumPts` | raw points behind the average (sensors sample 0.1 Hz → 180 per 30 min) |
| `ExpUncert` | expanded uncertainty, ~95% half-width |
| `StdErMean` | standard error of the mean |
| `finalQF` | **0 = passed, 1 = failed** |
| `AlphaQM` / `BetaQM` / `PassQM` | percent failing / unevaluable / passing |

### Step 5 — Find the real depths

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" --inventory
```

Prints the plot x level grid and, importantly, the **actual depths in cm** read
from `sensor_positions`.

### Step 6 — Check coverage, per plot

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" \
    --ver 502 503 --hor all --year 2024 --no-plot
```

Prints count, mean, min, max per plot and level. Compare counts against the
theoretical maximum: **17,520 for a normal year, 17,568 for a leap year**
(365 or 366 days x 48 half-hours).

### Step 7 — Extract and inspect

```bash
python neon_soil.py --root "$NEON_ROOT/NOGP/DP1.00041.001" \
    --hor 004 --ver 502 503 --year 2024 --gap-report \
    --out-csv out/nogp_2024.csv --out-png out/nogp_2024.png
```

`--gap-report` shows how missing intervals are distributed: how many separate
gaps, how many are isolated single slots, and the start time and duration of
the longest. Add `--resample D` for daily means on the plot (the CSV stays at
native cadence either way).

---

## 4. Gotchas

These cost real time. Read them.

**File counts are not data coverage.** Every plot/level at STER shows 84 of 84
monthly files for 2018–2024 — looks perfect. But plot 001 at 16 cm lost **more
than half** of 2024: NEON publishes a monthly file whether or not the sensor
was alive. Always check *row* counts after QA/QC, never file counts.

**VER is an ordinal, not a depth.** Level 502 is 6 cm at STER plots 001 and
003, but 5 cm at plot 002 and 7 cm at plots 004 and 005. Depths are set by each
plot's soil horizons. Always read `sensor_positions`; never assume a ladder.
NOGP will have its own depths.

**Gaps are absent, not NaN.** Stacked tables contain only the rows NEON
published. Plotting a datetime-indexed series draws a straight line across a
two-week outage and it looks like real data. Use `neon_soil.regular_grid()` to
reindex onto a complete grid before plotting, resampling, or windowing.

**Moisture depths are wrong in `sensor_positions`.** NEON says so, for
`DP1.00094.001` specifically, and ships `swc_depthsV3.csv` with the actual
installation depths. Check whether it appears in your stacked output. Our
loader reads `zOffset`, which is correct for temperature and wrong for
moisture.

**Precipitation sums, it does not average.** When resampling precipitation to
daily, use `.sum()`, not `.mean()`. Note it has no `Mean`/`NumPts` columns —
that is the tell.

**Tipping buckets undercatch frozen precipitation.** Roughly Nov–Mar the gauge
underreports and mistimes snow. At STER 2024, 24% of intervals were flagged and
the unflagged sum (445 mm) is a *floor*, not the annual total. Do not treat
winter precipitation as reliable model input.

**All timestamps are UTC.** Sterling and Mandan are UTC−6/−7. If you build
hour-of-day features straight from `startDateTime`, your diurnal maximum lands
near hour 21 instead of hour 14. Store UTC; derive local time as a feature.

**pandas 3.x: resample aliases are lowercase.** `6h` works, `6H` raises
`ValueError: Invalid frequency`. `D` and `W` are unaffected.

**`nu.list_available_dates()` crashes on some products** with
`TypeError: object of type 'NoneType' has no len()` (it is an
airborne-oriented function with a missing null check). Use
`neon_pull.py --dates`, which routes around it.

**`check_size=False`.** `load_by_product` defaults to prompting interactively
about download size, which hangs batch jobs. The script forces it off.

---

## 5. Site contrast: STER vs NOGP

These two are a deliberate contrast, not a replicate pair.

|  | STER | NOGP |
|---|---|---|
| Domain | D10 Central Plains | D09 Northern Plains |
| Location | North Sterling, CO | ~10 km W of Bismarck, ND |
| Ecosystem | Shortgrass steppe / cropland | Northern mixed-grass prairie / cropland |
| Soils | — | Mollisols (Typic Argiustolls), thick dark mollic horizons |
| Mean annual temp | — | ~5.9 °C |
| Mean annual precip | ~445 mm (2024 measured) | ~455 mm |
| Land history | — | USDA-ARS research lab since 1912 |

NOGP is colder with a longer and harder freeze season, which means more winter
precipitation falls as snow and the frozen-soil problems above bite harder
there. Its Mollisols carry much more organic matter than STER's soils, so
expect a different microbial regime. That contrast is the scientific point.

**Confirm NOGP's own numbers with `--dates` and `--inventory` before relying on
anything in that table.** The STER precipitation figure is measured from our
own 2024 pull; the NOGP figures are from NEON's site description page.

---

## 6. Suggested first task

1. Run `--dates` on NOGP for all four Tier 1 products. Note which exist and
   whether 2024 is fully released.
2. Pull soil temperature for NOGP, 2024, 30-minute.
3. Run `--inventory` and write down NOGP's actual depth ladder.
4. Run the per-plot coverage check and pick the healthiest plot.
5. Extract that plot at the two shallowest levels with `--gap-report` and plot
   the year.
6. Compare the figure against STER's. The seasonal amplitude, the winter
   behavior, and the diurnal damping with depth should all differ, and those
   differences are physically interpretable.

The output of step 3 and step 4 is what we need before any modeling
conversation.

---

## Appendix A — Getting your own NEON API token

**You need your own token. Do not use someone else's.** NEON states plainly
that a token is unique to your account and should not be shared. Beyond that
there are practical reasons: rate limits are tracked per token, so two people
sharing one contend for the same budget, and NEON uses tokens to understand
who is using which data — which is visibility our collaboration wants, not
visibility to avoid.

Tokens exist partly to deter bots that scrape data autonomously, which have
become more common and degrade performance for real users.

### Step 1 — Create a NEON user account

Go to the NEON Data Portal and register for a free account:

- Data Portal: https://data.neonscience.org
- Account info: https://www.neonscience.org/data/about-data/data-portal-user-accounts

Use your university email address.

### Step 2 — Generate the token

1. Sign in at https://data.neonscience.org
2. Go to your **My Account** profile page
3. Scroll to the **bottom** of that page
4. Click the **GET API TOKEN** button
5. After a moment the token appears; click **Copy**

The token is a very long string. Do not retype it — copy it.

### Step 3 — Store it

**On the cluster** (what we use), add a line to `~/.bashrc`:

```bash
export NEON_TOKEN='paste-the-long-string-here'
```

Then `source ~/.bashrc` or open a new shell.

On a shared cluster, check that your home directory and `.bashrc` are not
world-readable:

```bash
chmod 600 ~/.bashrc
ls -ld ~
```

**For code you will share** (a GitHub repo, a paper supplement), never paste
the token into a script. Two options NEON recommends:

- Put `NEON_TOKEN = "..."` in a separate `neon_token_source.py` that is not
  committed, and `import neon_token_source` at the top of your scripts.
- Use `python-dotenv`: `pip install python-dotenv`, put `NEON_TOKEN=...` in a
  `.env` file, add `.env` to `.gitignore`, then `dotenv.load_dotenv()` and
  `os.environ.get("NEON_TOKEN")`.

Our scripts read `os.environ["NEON_TOKEN"]`, so the `.bashrc` approach works
with no code changes.

### Step 4 — Verify it works

```bash
curl -s -D - -o /dev/null -H "X-API-Token: $NEON_TOKEN" \
  https://data.neonscience.org/api/v0/products/DP1.00094.001 | grep -i ratelimit
```

| Response | Meaning |
|---|---|
| `x-ratelimit-limit: 2000` | Token is working |
| `x-ratelimit-limit: 200` | Token is **not** being seen — anonymous rate |
| nothing printed | `$NEON_TOKEN` is empty, or the shell was not re-sourced |

With a token you get a burst of 2000 requests at 8/sec; without one, 200 at
2/sec. A missing token slows you down rather than blocking you, which is why
the failure is easy to miss. Check it once, up front.

### Reference

NEON's own tutorial, which has screenshots of the button:
https://www.neonscience.org/resources/learning-hub/tutorials/neon-api-tokens-tutorial

There is also a shorter quick-start version:
https://www.neonscience.org/resources/learning-hub/tutorials/api-token-setup
