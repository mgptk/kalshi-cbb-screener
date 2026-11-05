"""Men's college basketball: Kalshi moneylines against the sportsbooks and BPI.

A sibling of the `cfb` package, not a copy of it. Everything genuinely neutral
— devig maths, the Kalshi market-data client, team-name normalisation, the
Selenium board scraper and its blank-reason taxonomy, the price formatting —
is imported from `cfb` and used as is. Only what differs by sport lives here:
ESPN's basketball feed (dates, not weeks), BPI's stat names, the two books'
basketball board URLs, and a moneylines-only section.
"""
