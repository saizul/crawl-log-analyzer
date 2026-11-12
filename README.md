# crawl-log-analyzer

[![CI](https://github.com/saizul/crawl-log-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/crawl-log-analyzer/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

See how search engine bots really crawl your site, straight from your Nginx or
Apache access logs.

Crawl stats in webmaster tools are sampled, delayed and limited to one search
engine. Your access log has every request. `crawl-log-analyzer` reads it and
answers the questions a technical SEO audit usually starts with:

- Which bots crawl the site, how much, and is that changing?
- How much of that crawl is wasted on redirects, errors, parameters and filters?
- Which important URLs are never crawled, and which crawled URLs are not in the sitemap?
- Is that "Googlebot" really Googlebot?

It is a command-line tool and a Python library with **no runtime dependencies**
(standard library only), so it runs anywhere Python 3.10+ does, including on
the server that holds the logs.

## Features

- **Log formats**: Nginx/Apache combined, combined with `$request_time`,
  common (CLF), JSON lines, and any custom Nginx `log_format` or Apache
  `LogFormat` string. The format is detected per file when you do not name one.
- **Input**: plain, `.gz`, `.bz2` and `.xz` files, directories, globs and
  standard input. Logs are streamed, never loaded into memory.
- **Bot identification**: about 85 built-in signatures (Googlebot variants,
  Bingbot, YandexBot, Baiduspider, DuckDuckBot, Applebot, GPTBot, CCBot,
  PerplexityBot, AhrefsBot, SemrushBot, social previews, uptime monitors and
  more), grouped by family and category. Add your own in a config file.
- **Bot verification**: reverse + forward DNS, or published IP range files.
  Fake bots are reported separately and kept out of the crawl statistics.
- **Reports**
  - crawl volume per bot and per day
  - status code distribution per bot
  - top crawled URLs and directories
  - crawl budget waste: 5xx, 404, 410, redirects, tracking parameters,
    internal search, faceted navigation, static assets, case and
    trailing-slash duplicates, plus your own regex rules
  - response time percentiles per bot
  - crawl frequency per URL and the URLs crawled longest ago
  - orphan detection against a sitemap or URL list (both directions)
  - weekday by hour heatmap
  - crawl spikes and drops (robust z-score on daily hits)
- **Outputs**: terminal tables, JSON, CSV (one file per table), Markdown and a
  self-contained HTML report with inline SVG charts (no JavaScript libraries,
  no external requests, light and dark mode).
- **Filters**: date range, bot, bot category, status code, path prefix, path
  regex.
- **Compare** two periods, for example before and after a migration.
- **Sample log generator** so you can try everything without real data.

## Installation

The project is not on PyPI yet. Install it from a checkout:

```console
$ git clone https://github.com/saizul/crawl-log-analyzer.git
$ cd crawl-log-analyzer
$ python -m pip install .
```

That installs the `crawl-log-analyzer` command. `python -m crawl_log_analyzer`
works too.

Python 3.10 only: TOML config files need the `tomli` package
(`pip install tomli`), because `tomllib` joined the standard library in 3.11.
Everything else works without it.

## Quick start

The repository ships a synthetic two-week log (`examples/sample-access.log`,
documentation IP addresses only), a matching sitemap and an example IP range
file:

```console
$ crawl-log-analyzer analyze examples/sample-access.log \
    --ip-ranges examples/googlebot-ranges.example.json \
    --sitemap examples/sitemap.example.xml \
    --top 5
```

```text
CRAWL LOG ANALYSIS
Period: 2026-03-02 00:25 to 2026-03-15 20:07 (14 days)
Bot verification: ip-ranges(google)
Sitemap: examples/sitemap.example.xml

Summary
=======
Metric                Value
--------------------  -------------------------
Requests analysed     880
Bot hits              552 (62.7% of requests)
Non-bot requests      302
Distinct bots         14
Unique URLs crawled   228
Bot hits per day      39.43
Bytes served to bots  16.4 MB
Crawl budget waste    217 hits (39.3%)
Fake bot hits         26 (4.5% of checked hits)
Malformed lines       0 (0.0%)

Crawl volume by bot
===================
Hits, distinct URLs and bytes served per crawler.

Bot                   Family  Category       Hits  Share  Unique URLs     Bytes  Avg bytes  Active days  Last seen         Verified
--------------------  ------  -------------  ----  -----  -----------  --------  ---------  -----------  ----------------  --------
Googlebot Smartphone  Google  Search engine   274  49.6%          147    8.0 MB    29.9 KB           14  2026-03-15 13:48       274
Googlebot Desktop     Google  Search engine    66  12.0%           52    2.1 MB    32.6 KB           14  2026-03-15 20:07        66
Bingbot               Bing    Search engine    44   8.0%           39    1.2 MB    28.5 KB           13  2026-03-15 04:51         0
AhrefsBot             Ahrefs  SEO tool         36   6.5%           33  956.9 KB    26.6 KB           13  2026-03-15 18:16         0
GPTBot                OpenAI  AI crawler       34   6.2%           30    1.1 MB    32.8 KB           10  2026-03-15 05:31         0
Showing 5 of 14 rows.
```

The report continues with status classes, top URLs and directories, and the
sections below (shortened here):

```text
Crawl budget waste
==================
Each wasted hit is counted once under its most important reason; 'Matching hits' counts every hit
the reason applies to.

Reason                            Hits  Share of bot hits  Matching hits  Top example
--------------------------------  ----  -----------------  -------------  ----------------------------------------------------
Server errors (5xx)                  5               0.9%              5  /blog/post-02
Not found (404)                     19               3.4%             19  /old-catalog/item-57.html
Gone (410)                          18               3.3%             18  /discontinued/model-2
Redirects (3xx)                     23               4.2%             23  /blog/post-07/
Tracking parameters                 36               6.5%             37  /blog/post-01?utm_source=newsletter&utm_medium=email
Internal search results             24               4.3%             24  /search?q=sale
Faceted navigation / filters        61              11.1%             61  /products/bags/?color=red&sort=price_asc
Static assets                       18               3.3%             18  /static/img/logo.svg
Case / trailing-slash duplicates    13               2.4%             13  /Contact

In sitemap, never crawled
=========================
111 of 159 sitemap URLs were crawled (69.8%).

URL
-------------
/blog/post-16
/blog/post-20
/blog/post-21
/blog/post-24
/blog/post-25
Showing 5 of 48 rows.

Crawl spikes and drops
======================
Series                Date        Kind   Hits  Median  Robust z-score
--------------------  ----------  -----  ----  ------  --------------
All bots              2026-03-11  spike   100   36.00           17.27
Googlebot Smartphone  2026-03-11  spike    65   17.00           16.19
Googlebot Desktop     2026-03-11  spike    19    3.50            6.97

Fake bots
=========
Hits whose user agent claims a bot but whose address failed verification (ip-ranges(google)).

Claimed bot        Hits  Distinct IPs
-----------------  ----  ------------
Googlebot Desktop    26            20
```

## Usage

```text
crawl-log-analyzer analyze LOG [LOG ...] [options]     analyse logs
crawl-log-analyzer compare ... [options]               compare two periods
crawl-log-analyzer bots [--category NAME]              list known bot signatures
crawl-log-analyzer detect-format LOG [LOG ...]         show the detected format
```

Run any subcommand with `--help` for the full option list.

### Reading logs

```console
# Several rotated files, compressed or not; a directory or a quoted glob works too
$ crawl-log-analyzer analyze /var/log/nginx/access.log /var/log/nginx/access.log.*.gz

# From standard input
$ zcat access.log.gz | crawl-log-analyzer analyze -

# Name the format instead of detecting it
$ crawl-log-analyzer analyze access.log --format combined-timed

# A custom Nginx log_format (copy it from nginx.conf) ...
$ crawl-log-analyzer analyze access.log --log-format \
    '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent" $request_time'

# ... or an Apache LogFormat
$ crawl-log-analyzer analyze access.log --log-format '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i" %D'

# What would be detected?
$ crawl-log-analyzer detect-format examples/sample-access.log
File                        Format          Sample lines parsed
--------------------------  --------------  -------------------
examples/sample-access.log  combined-timed  50/50
```

Format names: `combined` (aliases `nginx`, `nginx-combined`,
`apache-combined`), `combined-timed` (`nginx-timed`; combined plus a trailing
request time in seconds), `common` (`clf`, `apache-common`) and `json`
(`jsonl`, `ndjson`).

JSON logs are read one object per line. Common key names are recognised
(`remote_addr`, `time_iso8601`, `request_uri`, `status`, `body_bytes_sent`,
`http_user_agent`, `request_time`, ...); unusual names can be mapped in the
config file.

Lines that cannot be parsed are counted and reported in the summary, never
silently dropped.

### Filters

Filters apply before any statistics are computed.

```console
$ crawl-log-analyzer analyze access.log --from 2026-03-01 --to 2026-03-07
$ crawl-log-analyzer analyze access.log --bot googlebot          # name contains, or family equals
$ crawl-log-analyzer analyze access.log --category search        # search, ai, seo, social, monitoring, other
$ crawl-log-analyzer analyze access.log --status 404 --status 5xx
$ crawl-log-analyzer analyze access.log --status 300-399,410
$ crawl-log-analyzer analyze access.log --path-prefix /blog/
$ crawl-log-analyzer analyze access.log --path-regex '\?(.*&)?page=\d+' --exclude-regex '^/api/'
```

### Outputs

```console
# Terminal tables are the default; --top sets rows per table, --all-sections adds the detailed ones
$ crawl-log-analyzer analyze access.log --top 50 --all-sections

# Only some sections
$ crawl-log-analyzer analyze access.log --section waste --section anomalies

# Write files (can be combined; add -o none to skip the terminal report)
$ crawl-log-analyzer analyze access.log -o none \
    --html report.html --json report.json --markdown report.md --csv-dir csv/

# JSON on standard output for scripting
$ crawl-log-analyzer analyze access.log -o json | jq '.totals.bot_requests'
```

| Output | What you get |
|---|---|
| `table` | Aligned plain-text tables for the terminal |
| `json` | The complete report data (lists keep up to 1,000 rows regardless of `--top`) |
| `markdown` | Tables ready to paste into an issue or a client report |
| `html` | One self-contained file: every table plus inline SVG charts for daily volume, status mix, top directories, waste and the hourly heatmap |
| `--csv-dir` | One CSV file per table, with raw (unformatted) numbers |

Section keys for `--section`: `summary`, `bots`, `daily`, `status_classes`,
`status_codes`, `top_urls`, `top_directories`, `waste`, `waste_urls`,
`duplicates`, `response_times`, `crawl_frequency`, `stale_urls`,
`sitemap_not_crawled`, `crawled_not_in_sitemap`, `heatmap`, `anomalies`,
`fake_bots`, `fake_bot_ips`, `parsing`.

### Orphan detection

Give `--sitemap` an XML sitemap (plain or `.gz`) or a text file with one URL
per line. Nothing is fetched from the network: a sitemap index is followed
only to child sitemaps saved next to it on disk, and missing children produce a
warning.

```console
$ crawl-log-analyzer analyze access.log --sitemap sitemap.xml \
    --section sitemap_not_crawled --section crawled_not_in_sitemap
```

Two lists come out: sitemap URLs no bot requested during the period, and
URLs that bots fetched with a 2xx response, that no waste rule matches, and
that the sitemap does not mention.

### Comparing two periods

Either split one set of logs at a date, or give two sets:

```console
$ crawl-log-analyzer compare access.log* --split-date 2026-03-16
$ crawl-log-analyzer compare --before old/access.log* --after new/access.log* \
    --before-label "Old site" --after-label "New site"
```

The comparison shows per-bot volume change (normalised per day, so periods of
different length compare fairly), status class shift in percentage points,
waste by reason, directories that gained or lost crawl, and URLs that are new
or no longer crawled. To try it on synthetic data with a simulated migration:

```console
$ python examples/generate_sample_logs.py --days 28 --hits-per-day 80 \
    --migration-day 14 --gzip -o migration.log.gz
$ crawl-log-analyzer compare migration.log.gz --split-date 2026-03-16
```

### Configuration file

Optional, TOML, passed with `--config`. It can add bot signatures, tune the
waste rules, set defaults and describe the log format. See
[`examples/config.example.toml`](examples/config.example.toml) for every key.

```toml
[[bots]]
name = "PartnerFeedBot"
pattern = "PartnerFeedBot"
category = "other"
dns_suffixes = [".crawl.partner.example"]

[waste]
extra_facet_params = ["fabric", "fit"]
ignore_params = ["page", "lang"]
flag_static_assets = false

[[waste.rules]]
name = "calendar-trap"
pattern = '^/events/\d{4}/\d{2}/'

[analysis]
top = 25
```

Command-line options override the config file.

## How bot verification works

Anyone can send `Googlebot` in a user agent, and scrapers do. Without
verification, their requests inflate your crawl numbers. Search engines
document two ways to check a crawler, and both are supported.

**Published IP ranges** (`--ip-ranges`). Several operators publish the networks
their crawlers use as a JSON file. Download the current file yourself and pass
it in; the tool never downloads anything.

```console
$ crawl-log-analyzer analyze access.log --ip-ranges googlebot.json --ip-ranges Bing=bingbot.json
```

The bot family is taken from the `FAMILY=` prefix, or guessed from the file
name. Files in the common `{"prefixes": [{"ipv4Prefix": ...}, {"ipv6Prefix":
...}]}` layout are understood, as are plain lists of CIDR strings. A hit that
claims the family and comes from inside a listed network is verified; from
outside, it is fake. This method is offline, fast and deterministic.

**Reverse + forward DNS** (`--verify-dns`). For each distinct address of a bot
that documents its host names:

1. Look up the PTR record of the address.
2. Check that the host name ends in one of the operator's documented suffixes
   (for example `.googlebot.com` or `.search.msn.com`).
3. Resolve that host name forward and check the original address is among the
   results. This step matters: anyone who controls reverse DNS for their own
   address space can make it claim any name.

Results are cached per address, so a log with a million Googlebot hits from a
few hundred addresses needs a few hundred lookups.

Every bot hit ends up in one of four states:

| State | Meaning |
|---|---|
| verified | Passed an IP range or DNS check |
| fake | A check ran and the address failed it |
| unknown | A check exists but the lookup itself failed (timeout, resolver error) |
| unchecked | No verification method is available for this bot, or none was requested |

Fake hits are removed from all crawl statistics and listed in the "Fake bots"
sections with their addresses. Pass `--include-fake` to keep them in. Unknown
and unchecked hits stay in the statistics. If both methods are enabled, IP
ranges are consulted first and DNS is used for families without a range file.

`crawl-log-analyzer bots` shows which built-in bots have documented DNS
suffixes.

## Using it as a library

```python
from crawl_log_analyzer import AnalysisOptions, ParseStats, analyze, iter_entries

stats = ParseStats()
entries = iter_entries(["access.log", "access.log.1.gz"], stats=stats)
report = analyze(entries, options=AnalysisOptions(top=50), parse_stats=stats)

print(report.data["totals"]["bot_requests"])
for row in report.data["bots"]:
    print(row["bot"], row["hits"])
```

`report.data` is the same structure the JSON output writes.

## Sample data

`examples/generate_sample_logs.py` writes realistic-looking synthetic logs:
bots with daily patterns, a crawl spike, wasteful URLs, slow responses, fake
Googlebot traffic and optionally a mid-period migration. It is deterministic
for a given `--seed`.

```console
$ python examples/generate_sample_logs.py --days 30 --hits-per-day 500 -o access.log
$ python examples/generate_sample_logs.py --format json --gzip -o access.json.gz
```

Every address it emits comes from the ranges reserved for documentation
(RFC 5737 and RFC 3849). `examples/googlebot-ranges.example.json` matches the
generator, not the real Googlebot; do not use it on real logs.

## Limitations

- **A log shows requests, not indexing.** A crawled URL may still not be
  indexed, and bots that render JavaScript request more than the HTML.
- **Only what reaches the log is counted.** Requests answered by a CDN or
  cache in front of the server never appear in the origin log. Analyse the CDN
  logs instead (export them as JSON lines or a format you can describe with
  `--log-format`).
- **Client address must be the real one.** Behind a proxy or load balancer,
  log the forwarded address (for example Nginx `real_ip` module); otherwise
  verification sees the proxy and marks every bot fake.
- **DNS verification depends on your resolver** and can be slow on logs with
  many distinct bot addresses. It also reflects DNS today, not on the day of
  the request. IP range files have the same caveat: use a current file.
- **Bot identification is by user agent.** A crawler that pretends to be a
  browser is counted as non-bot traffic. Bots without a verification method
  (most SEO tools) are taken at their word.
- **Orphan detection compares paths exactly** after light normalisation (host
  and fragment removed). It does not follow redirects or canonicals, and it
  knows nothing about internal links, so "orphan" here means "not in the URL
  list you supplied".
- **Static assets count as waste by default**, which is debatable for sites
  that rely on rendering. Turn it off with `flag_static_assets = false`.
- **Waste detection is rule based.** The built-in parameter lists cover common
  platforms; check the "Top example" column and adjust the rules in the config
  file for your site.
- **Response time percentiles are approximate** (within about 2%) because they
  are computed from a fixed-size histogram rather than by keeping every value.
  They need a format that logs the request time.
- **Anomaly detection needs at least 7 days** of data and ignores days that
  differ from the median by fewer than 5 hits. It flags unusual days; it does
  not model weekly seasonality.
- **Timestamps are used as logged.** Days and hours follow the time zone
  offset written in the log, and logs with mixed offsets are not converted.

## Contributing

Bug reports, new bot signatures and format fixes are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE). Copyright (c) 2026 Saizul Amin.

## Docker image

A multi-arch image (linux/amd64, linux/arm64) is published to GitHub Packages:

```bash
docker pull ghcr.io/saizul/crawl-log-analyzer:latest
docker run --rm -v "$PWD:/work" ghcr.io/saizul/crawl-log-analyzer:latest --help
```

Mount the folder with your input files at `/work`; output files are written there too.
