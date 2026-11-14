"""Command-line interface.

Subcommands::

    crawl-log-analyzer analyze access.log*            # full report
    crawl-log-analyzer compare --before a.log --after b.log
    crawl-log-analyzer bots                           # list known crawlers
    crawl-log-analyzer detect-format access.log       # show the detected format

Exit codes: ``0`` success, ``1`` no log line could be parsed, ``2`` usage,
configuration or input errors.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TextIO

from ._version import __version__
from .analysis import AnalysisOptions, Analyzer, CrawlReport
from .bots import CATEGORIES, CATEGORY_LABELS, BotClassifier
from .compare import compare_reports
from .config import Config, ConfigError, load_config
from .filters import EntryFilter, FilterError, parse_date
from .output import UnknownSectionError, csv_out, json_out, markdown, table
from .output import html as html_out
from .parsers import (
    FORMAT_ALIASES,
    JsonLineParser,
    LineParser,
    LogFormatError,
    compile_log_format,
    detect_format,
    get_parser,
)
from .reader import ParseStats, UnknownFormatError, expand_paths, iter_entries, iter_lines
from .robots import RobotsError, load_robots_txt
from .sections import Document, build_comparison_document, build_report_document
from .sitemap import SitemapError, load_url_list
from .verify import (
    BotVerifier,
    DnsVerifier,
    IpRangeError,
    IpRangeSet,
    Resolver,
    infer_family_from_filename,
    load_ip_ranges,
)

__all__ = ["CliError", "build_parser", "main"]

EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_USAGE = 2

#: Share of malformed lines above which a warning is printed.
MALFORMED_WARNING_SHARE = 0.05


class CliError(Exception):
    """A user-facing error; the message is printed and the exit code is 2."""


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("input")
    group.add_argument(
        "--format",
        "-f",
        dest="format",
        metavar="NAME",
        help="log format: " + ", ".join(sorted(FORMAT_ALIASES)) + " (default: detect per file)",
    )
    group.add_argument(
        "--log-format",
        metavar="STRING",
        help="custom Nginx log_format or Apache LogFormat string, e.g. "
        "'$remote_addr [$time_local] \"$request\" $status $body_bytes_sent'",
    )
    group.add_argument("--config", "-c", metavar="FILE", help="TOML configuration file")

    group = parser.add_argument_group("filters")
    group.add_argument("--from", dest="date_from", metavar="DATE", help="first day (YYYY-MM-DD)")
    group.add_argument("--to", dest="date_to", metavar="DATE", help="last day (YYYY-MM-DD)")
    group.add_argument(
        "--bot",
        action="append",
        default=[],
        metavar="NAME",
        help="only bots whose name contains NAME or whose family equals it (repeatable)",
    )
    group.add_argument(
        "--category",
        action="append",
        default=[],
        choices=CATEGORIES,
        help="only bots of this category (repeatable)",
    )
    group.add_argument(
        "--status",
        action="append",
        default=[],
        metavar="SPEC",
        help="only these statuses: 404, 4xx or 300-399 (repeatable, or comma-separated)",
    )
    group.add_argument("--path-prefix", metavar="PREFIX", help="only URLs starting with PREFIX")
    group.add_argument("--path-regex", metavar="REGEX", help="only URLs matching REGEX")
    group.add_argument("--exclude-regex", metavar="REGEX", help="drop URLs matching REGEX")

    group = parser.add_argument_group("bot verification")
    group.add_argument(
        "--verify-dns",
        action="store_true",
        help="verify bots with reverse + forward DNS lookups (needs network access)",
    )
    group.add_argument(
        "--ip-ranges",
        action="append",
        default=[],
        metavar="[FAMILY=]FILE",
        help="published IP range file for a bot family, e.g. googlebot.json or "
        "Bing=ranges.json (repeatable)",
    )
    group.add_argument(
        "--include-fake",
        action="store_true",
        help="keep hits that failed verification in the analyses",
    )

    group = parser.add_argument_group("analysis")
    group.add_argument("--top", type=_positive_int, metavar="N", help="rows per table (default 20)")
    group.add_argument(
        "--depth",
        type=_positive_int,
        metavar="N",
        help="path segments that make up a directory (default 2)",
    )

    group = parser.add_argument_group("output")
    group.add_argument(
        "--output",
        "-o",
        choices=("table", "json", "markdown", "html", "none"),
        default="table",
        help="what to print on standard output (default: table)",
    )
    group.add_argument("--json", dest="json_path", metavar="FILE", help="also write JSON to FILE")
    group.add_argument("--html", dest="html_path", metavar="FILE", help="also write HTML to FILE")
    group.add_argument(
        "--markdown", dest="markdown_path", metavar="FILE", help="also write Markdown to FILE"
    )
    group.add_argument(
        "--csv-dir", metavar="DIR", help="also write one CSV file per section into DIR"
    )
    group.add_argument(
        "--section",
        action="append",
        default=[],
        metavar="KEY",
        help="limit the output to this section (repeatable), e.g. bots, waste, top_urls",
    )
    group.add_argument(
        "--all-sections",
        action="store_true",
        help="include the detail sections in the terminal table output",
    )
    group.add_argument("--title", help="report title")
    group.add_argument("--quiet", "-q", action="store_true", help="suppress warnings")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="crawl-log-analyzer",
        description="Analyse web server access logs for technical SEO: which bots crawl "
        "what, how often, with which result, and where crawl budget is wasted.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    analyze = commands.add_parser(
        "analyze",
        help="analyse access logs and report on bot activity",
        description="Analyse one or more access logs (plain or compressed, files, "
        "directories or globs; '-' reads standard input).",
    )
    analyze.add_argument("logs", nargs="+", metavar="LOG", help="log file, directory or glob")
    _add_input_options(analyze)
    extra = analyze.add_argument_group("analyze options")
    extra.add_argument(
        "--sitemap",
        metavar="FILE",
        help="sitemap XML (or text file with one URL per line) for orphan detection",
    )
    extra.add_argument(
        "--robots-txt",
        metavar="FILE",
        help="robots.txt file; report bot hits on URLs it disallows",
    )
    extra.add_argument(
        "--anomaly-threshold",
        type=_positive_float,
        metavar="Z",
        help="robust z-score that flags a day as a spike or drop (default 3.5)",
    )
    extra.add_argument(
        "--now",
        metavar="DATE",
        help="reference date for 'days since last crawl' (default: last day in the logs)",
    )

    compare = commands.add_parser(
        "compare",
        help="compare two periods, e.g. before and after a migration",
        description="Compare bot activity between two sets of logs, or split one set "
        "of logs at a date.",
    )
    compare.add_argument("logs", nargs="*", metavar="LOG", help="logs to split with --split-date")
    compare.add_argument("--before", nargs="+", metavar="LOG", help="logs of the first period")
    compare.add_argument("--after", nargs="+", metavar="LOG", help="logs of the second period")
    compare.add_argument(
        "--split-date",
        metavar="DATE",
        help="split LOG arguments at this day: earlier requests are 'before'",
    )
    compare.add_argument("--before-label", default="Before", help="name of the first period")
    compare.add_argument("--after-label", default="After", help="name of the second period")
    _add_input_options(compare)

    bots = commands.add_parser("bots", help="list the crawlers the tool recognises")
    bots.add_argument("--config", "-c", metavar="FILE", help="TOML configuration file")
    bots.add_argument("--category", action="append", default=[], choices=CATEGORIES)

    detect = commands.add_parser("detect-format", help="show the format detected for log files")
    detect.add_argument("logs", nargs="+", metavar="LOG")
    return parser


# --------------------------------------------------------------------------
# Pipeline assembly
# --------------------------------------------------------------------------


@dataclass
class _Context:
    """Everything derived from the arguments that the pipeline needs."""

    config: Config
    parser: LineParser | None
    classifier: BotClassifier
    verifier: BotVerifier | None
    filters: EntryFilter
    top: int
    depth: int
    include_fake: bool = False


def _load_config(path: str | None) -> Config:
    if not path:
        return Config()
    try:
        return load_config(path)
    except ConfigError as exc:
        raise CliError(str(exc)) from exc


def _build_line_parser(args: argparse.Namespace, config: Config) -> LineParser | None:
    if args.format and args.log_format:
        raise CliError("use either --format or --log-format, not both")
    try:
        if args.log_format:
            return compile_log_format(args.log_format)
        if args.format:
            canonical = FORMAT_ALIASES.get(args.format.lower())
            if canonical == "json" and config.json_fields:
                return JsonLineParser(config.json_fields)
            return get_parser(args.format)
        return config.parser()
    except LogFormatError as exc:
        raise CliError(str(exc)) from exc


def _parse_ip_range_argument(argument: str) -> tuple[str, str]:
    """Split ``[FAMILY=]FILE`` into ``(family, path)``."""
    family, separator, path = argument.partition("=")
    if separator and family and not os.path.exists(argument):
        return family.strip(), path
    inferred = infer_family_from_filename(argument)
    if inferred is None:
        raise CliError(
            f"cannot tell which bot family {argument!r} belongs to; "
            "name it explicitly, e.g. --ip-ranges Google=" + argument
        )
    return inferred, argument


def _build_verifier(
    args: argparse.Namespace,
    classifier: BotClassifier,
    resolver: Resolver | None,
) -> BotVerifier | None:
    ranges: dict[str, IpRangeSet] = {}
    known = {family.lower(): family for family in classifier.families()}
    for argument in args.ip_ranges:
        family, path = _parse_ip_range_argument(argument)
        if family.lower() not in known:
            raise CliError(
                f"unknown bot family {family!r} for --ip-ranges; known families: "
                + ", ".join(sorted(known.values()))
            )
        try:
            loaded = load_ip_ranges(path)
        except (IpRangeError, OSError) as exc:
            raise CliError(f"cannot load IP ranges from {path}: {exc}") from exc
        existing = ranges.get(family.lower())
        if existing is None:
            ranges[family.lower()] = loaded
        else:
            for network in loaded.networks():
                existing.add(network)
    dns = DnsVerifier(resolver) if args.verify_dns else None
    if not ranges and dns is None:
        return None
    return BotVerifier(ranges, dns)


def _build_context(args: argparse.Namespace, resolver: Resolver | None) -> _Context:
    config = _load_config(args.config)
    classifier = config.classifier()
    for option, value in (("--from", args.date_from), ("--to", args.date_to)):
        if value:
            _parse_day(value, option)
    try:
        filters = EntryFilter.build(
            date_from=args.date_from,
            date_to=args.date_to,
            statuses=args.status,
            path_prefix=args.path_prefix,
            path_regex=args.path_regex,
            exclude_regex=args.exclude_regex,
            bots=args.bot,
            categories=args.category,
        )
    except FilterError as exc:
        raise CliError(str(exc)) from exc
    return _Context(
        config=config,
        parser=_build_line_parser(args, config),
        classifier=classifier,
        verifier=_build_verifier(args, classifier, resolver),
        filters=filters,
        top=args.top or config.top or 20,
        depth=args.depth or config.directory_depth or 2,
        include_fake=args.include_fake,
    )


def _expand(patterns: Sequence[str]) -> list[str]:
    try:
        return expand_paths(patterns)
    except FileNotFoundError as exc:
        raise CliError(str(exc)) from exc


def _parse_day(text: str, option: str) -> date:
    try:
        return parse_date(text)
    except FilterError as exc:
        raise CliError(f"{option}: {exc}") from exc


def _options(context: _Context, **overrides: object) -> AnalysisOptions:
    return AnalysisOptions(
        top=context.top,
        directory_depth=context.depth,
        waste_rules=context.config.waste_rules,
        filters=context.filters,
        include_fake=context.include_fake,
        **overrides,  # type: ignore[arg-type]
    )


def _warn(args: argparse.Namespace, stderr: TextIO, message: str) -> None:
    if not args.quiet:
        print(f"warning: {message}", file=stderr)


def _report_parse_problems(args: argparse.Namespace, stats: ParseStats, stderr: TextIO) -> None:
    if stats.malformed and stats.malformed_share >= MALFORMED_WARNING_SHARE:
        _warn(
            args,
            stderr,
            f"{stats.malformed:,} of {stats.total_lines:,} lines "
            f"({stats.malformed_share:.1%}) could not be parsed; is the log format right?",
        )
        for path, number, text in stats.malformed_samples[:3]:
            _warn(args, stderr, f"  {path}:{number}: {text[:120]}")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _write_file(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _emit(args: argparse.Namespace, document: Document, stdout: TextIO, stderr: TextIO) -> None:
    keys = args.section or None
    try:
        if args.output == "table":
            stdout.write(table.render_document(document, keys, include_detail=args.all_sections))
        elif args.output == "markdown":
            stdout.write(markdown.render_document(document, keys))
        elif args.output == "json":
            stdout.write(json_out.render_json(document.data, keys))
        elif args.output == "html":
            stdout.write(html_out.render_document(document, keys))

        written: list[str] = []
        if args.json_path:
            _write_file(args.json_path, json_out.render_json(document.data, keys))
            written.append(args.json_path)
        if args.html_path:
            _write_file(args.html_path, html_out.render_document(document, keys))
            written.append(args.html_path)
        if args.markdown_path:
            _write_file(args.markdown_path, markdown.render_document(document, keys))
            written.append(args.markdown_path)
        if args.csv_dir:
            files = csv_out.write_csv_dir(document, args.csv_dir, keys)
            written.append(f"{args.csv_dir} ({len(files)} CSV files)")
    except UnknownSectionError as exc:
        raise CliError(str(exc)) from exc
    except OSError as exc:
        raise CliError(f"cannot write output: {exc}") from exc
    if written and not args.quiet:
        for path in written:
            print(f"wrote {path}", file=stderr)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _run_analysis(
    context: _Context,
    paths: list[str],
    options: AnalysisOptions,
) -> tuple[CrawlReport, ParseStats]:
    stats = ParseStats()
    analyzer = Analyzer(context.classifier, context.verifier, options)
    try:
        analyzer.consume(iter_entries(paths, context.parser, stats))
    except UnknownFormatError as exc:
        raise CliError(str(exc)) from exc
    except OSError as exc:
        raise CliError(f"cannot read log: {exc}") from exc
    return analyzer.report(stats), stats


def _cmd_analyze(
    args: argparse.Namespace, stdout: TextIO, stderr: TextIO, resolver: Resolver | None
) -> int:
    context = _build_context(args, resolver)
    paths = _expand(args.logs)

    overrides: dict[str, object] = {}
    threshold = args.anomaly_threshold or context.config.anomaly_threshold
    if threshold:
        overrides["anomaly_threshold"] = threshold
    if args.now:
        overrides["now"] = _parse_day(args.now, "--now")
    if args.sitemap:
        try:
            urls, warnings = load_url_list(args.sitemap)
        except (SitemapError, OSError) as exc:
            raise CliError(f"cannot load sitemap {args.sitemap}: {exc}") from exc
        for warning in warnings:
            _warn(args, stderr, warning)
        overrides["sitemap_urls"] = urls
        overrides["sitemap_source"] = args.sitemap
    if args.robots_txt:
        try:
            robots = load_robots_txt(args.robots_txt)
        except (RobotsError, OSError) as exc:
            raise CliError(f"cannot load robots.txt {args.robots_txt}: {exc}") from exc
        for warning in robots.warnings:
            _warn(args, stderr, f"robots.txt: {warning}")
        overrides["robots"] = robots

    report, stats = _run_analysis(context, paths, _options(context, **overrides))
    _report_parse_problems(args, stats, stderr)
    if stats.parsed == 0:
        print("error: no log lines could be parsed", file=stderr)
        return EXIT_NO_DATA
    document = build_report_document(report.data, title=args.title or "Crawl log analysis")
    _emit(args, document, stdout, stderr)
    return EXIT_OK


def _cmd_compare(
    args: argparse.Namespace, stdout: TextIO, stderr: TextIO, resolver: Resolver | None
) -> int:
    split_mode = bool(args.split_date)
    if split_mode:
        if args.before or args.after:
            raise CliError("use either --split-date with LOG arguments or --before/--after")
        if not args.logs:
            raise CliError("--split-date needs at least one LOG argument")
    else:
        if args.logs:
            raise CliError("positional LOG arguments need --split-date; or use --before/--after")
        if not args.before or not args.after:
            raise CliError("compare needs --before LOG... and --after LOG... (or --split-date)")

    context = _build_context(args, resolver)
    if split_mode:
        split = _parse_day(args.split_date, "--split-date")
        stats = ParseStats()
        before_analyzer = Analyzer(context.classifier, context.verifier, _options(context))
        after_analyzer = Analyzer(context.classifier, context.verifier, _options(context))
        try:
            for entry in iter_entries(_expand(args.logs), context.parser, stats):
                if entry.timestamp.date() < split:
                    before_analyzer.feed(entry)
                else:
                    after_analyzer.feed(entry)
        except UnknownFormatError as exc:
            raise CliError(str(exc)) from exc
        except OSError as exc:
            raise CliError(f"cannot read log: {exc}") from exc
        before, after = before_analyzer.report(), after_analyzer.report()
        _report_parse_problems(args, stats, stderr)
        parsed = stats.parsed
    else:
        before, before_stats = _run_analysis(context, _expand(args.before), _options(context))
        after, after_stats = _run_analysis(context, _expand(args.after), _options(context))
        _report_parse_problems(args, before_stats, stderr)
        _report_parse_problems(args, after_stats, stderr)
        parsed = before_stats.parsed + after_stats.parsed

    if parsed == 0:
        print("error: no log lines could be parsed", file=stderr)
        return EXIT_NO_DATA
    for label, report in ((args.before_label, before), (args.after_label, after)):
        if report.bot_hits == 0:
            _warn(args, stderr, f"period '{label}' contains no bot hits")
    comparison = compare_reports(
        before, after, top=context.top, labels=(args.before_label, args.after_label)
    )
    document = build_comparison_document(comparison.data, title=args.title or "Crawl comparison")
    _emit(args, document, stdout, stderr)
    return EXIT_OK


def _cmd_bots(args: argparse.Namespace, stdout: TextIO) -> int:
    classifier = _load_config(args.config).classifier()
    rows = []
    for signature in classifier.signatures:
        if args.category and signature.category not in args.category:
            continue
        rows.append(
            [
                signature.name,
                signature.family,
                CATEGORY_LABELS.get(signature.category, signature.category),
                "yes" if signature.dns_suffixes else "no",
            ]
        )
    stdout.write(table.render_table(["Bot", "Family", "Category", "DNS-verifiable"], rows) + "\n")
    stdout.write(f"\n{len(rows)} signatures\n")
    return EXIT_OK


def _cmd_detect(args: argparse.Namespace, stdout: TextIO) -> int:
    status = EXIT_OK
    rows = []
    for path in _expand(args.logs):
        try:
            sample = [
                line for line in itertools.islice(iter_lines(path), 200) if line.strip()
            ][:50]
        except OSError as exc:
            raise CliError(f"cannot read log: {exc}") from exc
        parser = detect_format(sample)
        if parser is None:
            status = EXIT_NO_DATA
            rows.append([path, "unknown", "-"])
            continue
        parsed = sum(1 for line in sample if parser.parse(line) is not None)
        rows.append([path, parser.name, f"{parsed}/{len(sample)}"])
    stdout.write(table.render_table(["File", "Format", "Sample lines parsed"], rows) + "\n")
    return status


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    resolver: Resolver | None = None,
) -> int:
    """Run the CLI and return the process exit code.

    ``stdout``, ``stderr`` and ``resolver`` exist for embedding and testing;
    ``resolver`` replaces the system DNS resolver used by ``--verify-dns``.
    """
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    if args.command is None:
        parser.print_help(stderr)
        return EXIT_USAGE
    try:
        if args.command == "analyze":
            return _cmd_analyze(args, stdout, stderr, resolver)
        if args.command == "compare":
            return _cmd_compare(args, stdout, stderr, resolver)
        if args.command == "bots":
            return _cmd_bots(args, stdout)
        return _cmd_detect(args, stdout)
    except CliError as exc:
        print(f"error: {exc}", file=stderr)
        return EXIT_USAGE
    except BrokenPipeError:  # e.g. piping into `head`
        return EXIT_OK
    except KeyboardInterrupt:
        print("interrupted", file=stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
