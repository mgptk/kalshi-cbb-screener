# NCAAM Sharp Line — college basketball moneylines

Kalshi's men's college-basketball moneylines against the sportsbooks and ESPN's
BPI. One section, by design — no poll bands, no win totals.

```bash
.venv/Scripts/streamlit run streamlit_app.py
```

Serves <http://localhost:8501>. Terminal view:

```bash
.venv/Scripts/python -m cbb.cli --days 2 --limit 20
```

## What it shows

| Column | Meaning |
| --- | --- |
| **Tip-off** | ESPN's published start, in your own time zone; `approx` marks the rare game ESPN does not carry |
| **Team** | the school, with its KenPom rank in front (1–365; a dash means no rank matched) |
| **Kalshi ask** | what it costs to buy Yes on that team right now |
| **DraftKings / BetMGM** | each book's own posted American price, vig included |
| **Composite** | devigged, equal-weighted across the books carrying the game |
| **Edge** | **book composite − Kalshi ask**, per team; positive (green) means Kalshi is priced below the books, so that side is the cheap one to buy |
| **BPI** | ESPN's matchup-predictor win probability |
| **BPI−K** | BPI − Kalshi ask, the same comparison against the model |
| **Vol** | both contracts' Kalshi volume |

Edge is measured against the **ask** rather than the midpoint because the ask is
the price you can actually take; an edge against a midpoint is an edge against a
price nobody is showing.

**One row per game, two columns per measure.** Every measure a team has —
Kalshi ask, each book, the composite, Edge, BPI, BPI−K, the KenPom rank —
appears twice, once per side, so clicking a column header sorts the whole board
on the side you mean. That is why the old four-way sort dropdown is gone: the
HTML board gave a game three rows, so a sort had to move whole blocks and
needed to be told which of the two numbers to use. Here the column *is* the
side.

Which side is which is the **Pair the two sides as** control:
`Favourite / Underdog` (the default, and how the edges are usually read) or
`Away / Home` (the sportsbook's own orientation). The favourite is whichever
side Kalshi prices higher, decided once per game so every measure means the
same team; the book composite and then BPI stand in when Kalshi has no price.

**Edge and BPI−K stay numeric in both price representations.** They are
differences in percentage points, so American odds do not apply to them —
which means the sorts that matter stay numeric even with the American toggle on.
Only the prices themselves switch, and in American mode they become text.

Hover titles have nowhere to live in a dataframe, so **selecting a row** opens
the detail panel underneath: both two-sided Kalshi quotes, each book's price or
its reason for a blank, the KenPom lines, the BPI margin and Kalshi's mid-sum.
The per-game warnings that used to be tags — `approx time`, `neutral`,
`no ESPN match`, `no moneyline offered`, an unreadable book — are collected
in the **Flags** column.

### KenPom ranks

The ratings table on kenpom.com's front page is public — the paywall is on the
per-team pages, not the ranking — but Cloudflare answers a plain HTTP request
with its interstitial, so `cbb/kenpom.py` tries a request first and otherwise
renders the page in the same Selenium browser the books already need. It does
not try to defeat a challenge: if the browser is shown one too, the board says
so in a note and renders without ranks. A successful read is kept for the rest
of the Eastern day, since the ranks only move overnight, so pressing Refresh
does not pay for another browser launch. `--no-kenpom` skips it entirely.

Schools are matched by the shared normaliser in `core/teams.py`. On a 26-game
slate that resolved 51 of 52 teams; the miss was an NAIA school KenPom does not
rate, which is the honest answer rather than a bad match.

## Layout

```
core/            sport-neutral: devig, Kalshi client, team names, board scraping
  scrapers/
    board.py     the Selenium board scraper and its blank-reason taxonomy
    parsers.py   the DraftKings and BetMGM HTML parsers
cbb/             everything that knows it is basketball
  espn.py        ESPN schedule (dates, not weeks) + the BPI predictor
  books.py       the two books, pointed at their NCAAM boards
  sections.py    the moneyline section
  kenpom.py      the public ratings table, for the ranks
  view.py        section -> one-row-per-game table (no UI import, tested alone)
  streamlit_app.py  the page: layout, caching, the detail panel
streamlit_app.py the entry point `streamlit run` wants at the repository root
.streamlit/
  config.toml    theme, checked in so hosted and local look alike
  secrets.toml.example  the one setting a hosted copy needs
tests/
```

## On Streamlit Community Cloud

The repository is ready to deploy as it stands: `streamlit_app.py` is at the
root where Community Cloud looks for it, `requirements.txt` is the install
list, and `.streamlit/config.toml` carries the theme so a hosted copy looks
like a local one. Nothing here authenticates against anything — Kalshi's
market data, ESPN and KenPom's ratings table are all public — so there are
no secrets to leak into a public repository.

1. Push this directory to GitHub.
2. <https://share.streamlit.io> → **Create app** → point it at the repo and
   branch, main file path `streamlit_app.py`, Python 3.11 or newer.
3. **App settings → Secrets**, paste the one line from
   `.streamlit/secrets.toml.example`:

   ```toml
   browser_scrapes = false
   ```

### What a hosted copy cannot do

**The sportsbook and KenPom scrapes do not work there, and cannot be made to.**
Both need a real browser: the books render their odds client-side, and KenPom
sits behind Cloudflare. A Community Cloud container has no browser, and even
with one, a datacenter IP is exactly what those sites turn away. That is what
`browser_scrapes = false` is for — it stops the app spending a minute
failing three times and makes it say, in one line at the top, which columns
are missing.

| | Local | Hosted |
| --- | --- | --- |
| Tip-off, teams, Kalshi prices, volume | yes | yes |
| ESPN BPI and BPI−K | yes | yes |
| DraftKings / BetMGM, Composite, Edge | yes | **no** |
| KenPom ranks | yes | **no** |

So the hosted board is a Kalshi-versus-BPI screener. If you want the book edges
on your phone as well, the data has to be fetched where a browser exists —
this machine — and published for the phone to read.

### On a phone

The sidebar starts collapsed so the table gets the full width, and the
**Compact** checkbox drops the book prices and volume for a board that fits a
phone screen. A hosted app sleeps when nobody is using it; the first visit
after that wakes it and fetches, which takes a few seconds without the scrapes.

## Why the fetch is cached the way it is

Streamlit re-runs the whole script on every widget interaction, and a refresh
here drives a real browser at two sportsbooks and at KenPom — a minute or
two. So `load()` is wrapped in `st.cache_data` and **only the Refresh button
causes another fetch**: it bumps a `refresh_token` in session state, which is
part of the cache key and nothing else. Changing the pairing, the price
representation or the day window re-reads the cached fetch. Bumping a token
rather than calling `cache_data.clear()` keeps the other day windows that have
already been fetched.

`cbb/kenpom.py` keeps its own day-long cache underneath that, so even a real
refresh does not pay for a second browser launch for the ranks.

## `core` is a copy, not a link

`core` holds this project's own copy of the sport-neutral modules from the
football project next door (`../cfb`): the devig maths, the Kalshi market-data
client, team-name normalisation, the Selenium board scraper, and the two book
HTML parsers. Copied rather than imported across directories so this project
runs standalone — its own venv, its own tests, nothing breaking if the sibling
moves.

The cost is real and worth stating plainly: **these files now exist twice, and
they will drift.** A genuine fix to any of them belongs in both. The accent
folding in `teams.normalize` is the live example — it was found here, chasing
"San José State", and fixed on both sides. If the duplication starts to hurt,
the alternative is to make `core` a small installable package that both projects
depend on instead.

Four things actually differ, and each one is why a parameter on the football code
would not have been enough:

**The Kalshi series is `KXNCAAMBGAME`.** `KXNCAABBGAME` is college *baseball* and
`KXNCAABGAME` is a dormant generic series with no open markets. The rules text
also reads "men's college basketball game", so football's regex matches nothing.

**ESPN basketball has no weeks.** The football calendar returns week windows with
start and end dates; basketball's is a flat list of days, because the sport runs
on a daily schedule. A slate here is a date range, not a week.

**BPI is not FPI.** Same endpoint shape, every stat renamed —
`teampredwinpct` for `gameprojection`, `teampredmov` for `teampredptdiff`.
Reading football's names off a basketball payload returns `None` for everything
without raising, so this fails silently if you assume. BPI does publish
`opponentpredwinpct`, so one team's document fills both sides of a row.

**Kalshi's `occurrence_datetime` is not the tip-off.** It runs exactly three
hours late — measured across a full 16-game slate, where every Kalshi time sat
three hours after ESPN's scheduled start, which is what a *settle* time looks
like. Taken at face value it showed every game three hours late and filed late
tip-offs under the following Eastern day, which asked ESPN for the wrong date
and dropped four West-Coast games from a one-day window. So Kalshi's time is
only an estimate (occurrence minus three hours) used to pick the window, and
ESPN's published start replaces it wherever the game matched; the few ESPN does
not carry are marked `approx` on the board.

**Slate days are Eastern days, but the clock is yours.** A 9pm Eastern tip-off
falls on the next UTC date, and both Kalshi's event ticker (`…26NOV05…`) and
ESPN's `dates=` parameter file it under the Eastern day. Bucketing by UTC splits
one evening across two buckets and asks ESPN for the wrong one — on a two-day
window that cost about 40 games their ESPN match, and so their BPI, without any
error. That is the grouping only. *Displayed* times are local: the server
renders each tip-off in the zone of the machine it runs on, labelled with that
zone (`7:30 PM EST`), and the page then re-renders them in the zone of the
device showing it, so the board read on a phone two zones away shows that
phone's time. Without JavaScript the server's zone stands.

## The window

Kalshi lists this series days ahead — a few hundred games — while the books only
hang lines a day or two out. Showing Kalshi's whole list would bury the
comparable games under rows with nothing to compare against, so the slate is a
date window anchored on Kalshi's own earliest open tip-off, one day wide by
default. Anything beyond it is counted and reported on the page, never silently
dropped; the picker widens the window without re-scraping the books.

## Verified, not assumed

- **Both books use the same markup as football**, so their parsers are imported
  rather than rewritten. DraftKings' basketball board parses to 100 games, and
  its away/home orientation was checked against Kalshi's own per-team contracts
  across 78 games — all 78 agreed.
- **BPI orientation** was checked against the raw per-team ESPN documents for
  several games; away, home and the margin sign all line up.
- BetMGM's NCAAM board needed more than a longer sleep. It renders its shell
  first and fills the games in a beat later, and how long that beat lasts varies
  enough that a fixed wait produced an empty parse on roughly every other run —
  which surfaces as "board rendered but no games were parsed" and reads exactly
  like a dead selector. `BoardScraper.READY_SELECTOR` now polls for the board's
  own markup instead of guessing at a duration; football's two books use it too.

## Team-name matching

145 of 149 games on a typical two-day window match ESPN. Fixing the last of the
misses turned up one general bug and a handful of school-name facts:

- `normalize()` now folds accents before stripping punctuation. "San José State"
  was losing its `é` entirely and normalising to `san jos state`, matching
  nothing — a whole school lost to one character. That fix lives in the shared
  module and helps football too.
- Six schools are spelled differently by the two feeds (Grambling, Central
  Connecticut, UNO, Seattle U, New College, FDU) and are in the shared alias
  table. There is deliberately **no general "drop State" rule**: Illinois and
  Illinois State are different schools, and a blanket rule would merge them.

The four that still don't match are games ESPN's D-I scoreboard doesn't carry —
mostly non-D-I exhibition opponents. They render with a "no ESPN match" tag and
no BPI, rather than disappearing.
