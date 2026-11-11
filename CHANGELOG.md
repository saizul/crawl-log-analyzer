# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-11

First public release.

### Added

- Parsers for Nginx/Apache combined, combined with request time, common (CLF)
  and JSON lines logs, with per-file format detection.
- Custom formats from an Nginx `log_format` or Apache `LogFormat` string.
- Streaming reader for plain, gzip, bzip2 and xz files, directories, globs and
  standard input, with malformed-line accounting.
- Bot identification with built-in signatures grouped by family and category,
  extensible from a config file.
- Bot verification by published IP range files and by reverse + forward DNS
  with a per-address cache; fake hits are reported separately.
- `analyze` reports: crawl volume per bot and per day, status codes per bot,
  top URLs and directories, crawl budget waste, response time percentiles,
  crawl frequency, stale URLs, orphan detection against a sitemap or URL list,
  weekday by hour heatmap, and crawl spikes and drops.
- `compare` subcommand for two periods or two sets of logs.
- `bots` and `detect-format` helper subcommands.
- Filters by date range, bot, category, status code, path prefix and regex.
- Output as terminal tables, JSON, CSV, Markdown and a self-contained HTML
  report with inline SVG charts.
- TOML configuration file for bot signatures, waste rules, defaults and input
  format.
- Synthetic sample log generator, sample log, sitemap and IP range file in
  `examples/`.

[Unreleased]: https://github.com/saizul/crawl-log-analyzer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/crawl-log-analyzer/releases/tag/v0.1.0
