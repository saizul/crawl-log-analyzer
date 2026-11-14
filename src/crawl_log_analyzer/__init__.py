"""crawl-log-analyzer: see how search engine bots really crawl a site.

The public library surface mirrors the pipeline the CLI runs::

    from crawl_log_analyzer import ParseStats, analyze, iter_entries

    stats = ParseStats()
    report = analyze(iter_entries(["access.log.gz"], stats=stats), parse_stats=stats)
    print(report.data["totals"]["bot_requests"])
"""

from ._version import __version__
from .analysis import AnalysisOptions, Analyzer, CrawlReport, analyze
from .bots import BotClassifier, BotMatch, BotSignature
from .compare import Comparison, compare_reports
from .config import Config, ConfigError, load_config
from .filters import EntryFilter
from .models import LogEntry
from .parsers import (
    JsonLineParser,
    LineParser,
    LogFormatError,
    RegexLineParser,
    compile_log_format,
    detect_format,
    get_parser,
)
from .reader import ParseStats, expand_paths, iter_entries
from .robots import RobotsTxt, load_robots_txt
from .sitemap import load_url_list
from .verify import (
    BotVerifier,
    DnsVerifier,
    IpRangeSet,
    VerificationStatus,
    load_ip_ranges,
)
from .waste import WasteRules

__all__ = [
    "AnalysisOptions",
    "Analyzer",
    "BotClassifier",
    "BotMatch",
    "BotSignature",
    "BotVerifier",
    "Comparison",
    "Config",
    "ConfigError",
    "CrawlReport",
    "DnsVerifier",
    "EntryFilter",
    "IpRangeSet",
    "JsonLineParser",
    "LineParser",
    "LogEntry",
    "LogFormatError",
    "ParseStats",
    "RegexLineParser",
    "RobotsTxt",
    "VerificationStatus",
    "WasteRules",
    "__version__",
    "analyze",
    "compare_reports",
    "compile_log_format",
    "detect_format",
    "expand_paths",
    "get_parser",
    "iter_entries",
    "load_config",
    "load_ip_ranges",
    "load_robots_txt",
    "load_url_list",
]
