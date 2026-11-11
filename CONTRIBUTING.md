# Contributing

Thanks for taking the time to improve crawl-log-analyzer. Bug reports, new bot
signatures, log format fixes and documentation corrections are all welcome.

## Reporting a bug

Open an issue with:

- the command you ran and the full output or traceback
- your Python version (`python --version`) and operating system
- a few log lines that reproduce the problem, **anonymised**: replace client
  addresses with documentation addresses (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`, `2001:db8::/32`) and remove anything that identifies your
  visitors or your site if it is private

`crawl-log-analyzer detect-format yourfile.log` output helps with parsing
problems.

## Development setup

You need Python 3.10 or newer.

```console
$ git clone https://github.com/saizul/crawl-log-analyzer.git
$ cd crawl-log-analyzer
$ python -m venv .venv
$ . .venv/bin/activate
$ python -m pip install -e ".[dev]"
```

Then, before every commit:

```console
$ ruff check .
$ pytest
```

Both must pass; CI runs the same two commands on Python 3.10 to 3.13.

## Guidelines

- **No runtime dependencies.** The package uses the standard library only.
  Development tools go in the `dev` extra.
- **Tests never touch the network.** DNS verification takes an injectable
  resolver; use a fake one in tests (see `tests/test_verify.py`).
- **Synthetic data only.** Test fixtures and examples must use the
  documentation address ranges listed above and `example.com` style host
  names. Never commit real access logs.
- **Every change comes with a test** that fails without it.
- Keep lines within 100 characters and let `ruff check .` decide style
  questions.
- Support every Python version in the CI matrix. Avoid syntax or library
  features newer than 3.10.
- Add a line to the `Unreleased` section of `CHANGELOG.md` for anything a user
  would notice.

## Project layout

```text
src/crawl_log_analyzer/
    parsers.py     log line parsers and log_format compilation
    reader.py      file discovery, decompression, streaming
    bots.py        bot signatures and classification
    verify.py      IP range and DNS verification
    filters.py     entry filters
    waste.py       crawl budget waste rules
    sitemap.py     sitemap and URL list loading
    stats.py       percentiles, histograms, anomaly detection
    analysis.py    the analyzer and the report data
    compare.py     period comparison
    sections.py    report data to renderable sections
    output/        table, JSON, CSV, Markdown, HTML and SVG renderers
    config.py      TOML configuration
    cli.py         command-line interface
examples/          sample log generator and sample data
tests/             pytest suite
```

## Adding a bot signature

Signatures live in `src/crawl_log_analyzer/bots.py`. A new entry needs:

- a name, family and category
- a case-insensitive pattern that matches the documented user agent token and
  nothing else (more specific signatures go before general ones)
- DNS suffixes only if the operator documents them publicly; link the
  documentation in the pull request
- a test in `tests/test_bots.py` with a real-world user agent string

## Adding a waste rule or output section

New analyses should put their numbers into the report data in `analysis.py`
and their presentation into `sections.py`. Renderers work from sections, so a
new section appears in every output format without touching them.

## Pull requests

Keep pull requests focused on one change, describe what it does and why, and
mention anything you could not test. By contributing you agree that your work
is released under the MIT license of this project.
