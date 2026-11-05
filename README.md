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
| **KP / TRk** | the school's rank, 1–365, from KenPom or Barttorvik's T-Rank — the header says which |
| **Fav / Dog** | the two schools; the rank is its own column so both sort as they look |
| **Kalshi ask** | what it costs to buy Yes on that team right now |
| **Book columns** | each book's own posted American price, vig included — two when scraping, eight from The Odds API |
| **Composite** | devigged, equal-weighted across every book carrying the game |
| **Bks** | how many books went into that composite — one book and eight are very different numbers |
| **Edge** | **book composite − Kalshi ask**, per team; positive (green) means Kalshi is priced below the books, so that side is the cheap one to buy |
| **BPI** | ESPN's matchup-predictor win probability |
| **BPI−K** | BPI − Kalshi ask, the same comparison against the model |
| **Vol** | both contracts' Kalshi volume |

Edge is measured against the **ask** rather than the midpoint because the ask is
the price you can actually take; an edge against a midpoint is an edge against a
price nobody is showing.

**One row per game, two columns per measure.** Every measure a team has —
Kalshi ask, each book, the composite, Edge, BPI, BPI−K, the rank —
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
its reason for a blank, the rating lines, the BPI margin and Kalshi's mid-sum.
The per-game warnings that used to be tags — `approx time`, `neutral`,
`no ESPN match`, `no moneyline offered`, an unreadable book — are collected
in the **Flags** column.

## Where each number comes from

Four feeds, and two of them cannot be fetched the easy way. This is the whole
reason the project has the shape it does, so it is worth stating plainly:

| Feed | Plain HTTP | Needs a browser |
| --- | --- | --- |
| Kalshi prices | yes | |
| ESPN schedule and BPI | yes | |
| The books, direct | **no** — 403 to any non-browser client | yes, or The Odds API |
| KenPom ranks | **no** — Cloudflare challenge | yes, or T-Rank instead |

Those 403s were measured from a residential address, on the same machine whose
browser scrape succeeds, so the block is on the client's fingerprint rather
than the IP — which is also why a cloud VM would not have helped. DraftKings
does embed a `window.__INITIAL_STATE__` in its board HTML, but its odds
containers come down empty (`"eventGroups":{}`, `"outcomes":[]`); the prices
arrive by XHR after the page boots.

So `cbb/refresh.py` picks a source per feed, in one place:

- **Books.** No key: scrape DraftKings' and BetMGM's boards in a real
  browser. With an Odds API key: **every book the US region carries**, which
  on a measured slate was eight — DraftKings, BetMGM, FanDuel, Bovada,
  BetRivers, BetOnline.ag, BetUS, LowVig.ag. One request covers all of them
  for one credit (`markets=h2h` x `regions=us`), so the free tier's 500 a
  month is about sixteen refreshes a day; filtering to two books would have
  cost exactly the same. The prices arrive as American odds, which is what
  `build_quote` already devigs, so the composite and Edge are unchanged —
  the composite is simply averaged over more books.

  Which books exist is only known once the request has been made, so the
  columns are discovered rather than configured, ordered by how much of the
  slate each one priced. Coverage is uneven — the big five price most
  games and three price a handful — so the **Bks** column says how many
  books each composite is actually averaging.
- **Ranks.** `--ratings auto` takes KenPom where a browser can clear its
  challenge and Barttorvik's T-Rank everywhere else. Both are tempo-free
  ratings of the same kind; the numbers differ and the column header says
  which you are looking at. On a 102-game slate each matched all 204 schools.

### Running it without a browser

```bash
.venv/Scripts/python -m cbb.cli --no-browser --odds-api-key KEY
```

That is exactly what a hosted copy does.

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
  refresh.py     picks a source per feed for this host
  kenpom.py      KenPom's ratings table, read in a browser
  torvik.py      T-Rank's ratings JSON, read over plain HTTP
  ratings.py     what the board needs from either of them
  oddsapi.py     the books over HTTPS, for hosts with no browser
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
   odds_api_key = "your-key-here"
   ```

   The key is yours to get, free, from <https://the-odds-api.com>. Without it
   everything but the book columns still works.

### What a hosted copy does

`browser_scrapes = false` tells it not to attempt the scrapes, and it reads the
substitutes instead: The Odds API for the books, T-Rank for the ranks. With
both secrets set, a hosted copy shows the same columns as a local one.

| | Local, scraping | With an Odds API key | Hosted, no key |
| --- | --- | --- | --- |
| Tip-off, teams, Kalshi, volume | yes | yes | yes |
| ESPN BPI and BPI−K | yes | yes | yes |
| Ranks | KenPom | either | T-Rank |
| Books in the composite | 2 | **8** | none |
| Composite and Edge | yes | yes | **no** |

On a measured 43-game slate the key beat scraping on its own ground: 43 games
with a composite against 38, from eight books instead of two, in seconds
instead of a minute, and with no browser at all.

Without a key the board still runs; it just has no book columns, and says so
in a line at the top rather than leaving you to work it out.

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
- Each ratings source brought its own spellings: KenPom alone writes CSUN and
  Cal Baptist, and Barttorvik keeps UMKC, Nebraska Omaha, Illinois Chicago,
  Loyola MD and McNeese St. where Kalshi uses the current brand. All are in the
  alias table, and with them both sources matched 204 of 204 schools on a
  102-game window. One alias could never have fired: its key was
  `university at albany`, but `normalize()` strips "university" as noise before
  the lookup, so the key had to be `at albany`.
- The Odds API is the odd one out: it attaches mascots ("Saint Louis
  Billikens") and sometimes abbreviates ("Loyola (Chi) Ramblers"). `cbb/oddsapi.py` also registers each game under its names with
  trailing words removed, and **drops any pairing two games would both claim**
  rather than guessing — losing a price is a dash, matching the wrong game is a
  wrong number. Sides are compared school-to-school for the same reason: string
  equality on "Wofford Terriers" versus "Wofford" quietly swapped the two
  prices, which a test now pins. Each shortened name is also run back through
  `normalize()`, because the alias table is applied at its end: without that,
  "SMU Mustangs" stops at `smu` while our own feed says `southern methodist`,
  and the two can never meet — that one cost four games their prices on a
  live slate before it was caught.

With those fixed, all 43 of our games matched an Odds API game and all 86
schools matched a rating.

The four that still don't match are games ESPN's D-I scoreboard doesn't carry —
mostly non-D-I exhibition opponents. They render with a "no ESPN match" tag and
no BPI, rather than disappearing.
