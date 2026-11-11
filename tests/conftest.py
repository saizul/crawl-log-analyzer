"""Shared fixtures and helpers for the test suite.

Nothing in here touches the network: DNS lookups go through ``FakeResolver``.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crawl_log_analyzer.models import LogEntry
from crawl_log_analyzer.verify import ResolverError

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GOOGLEBOT_MOBILE = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
BINGBOT = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
GPTBOT = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2)"
AHREFS = "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)"
BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

BASE_TIME = datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc)


def make_entry(
    url: str = "/",
    *,
    ip: str = "192.0.2.10",
    status: int = 200,
    user_agent: str = GOOGLEBOT,
    day: int = 0,
    hour: int = 10,
    minute: int = 0,
    bytes_sent: int = 1000,
    request_time: float | None = None,
    method: str = "GET",
) -> LogEntry:
    """Build a log entry ``day`` days after 2026-03-02 at ``hour:minute`` UTC."""
    timestamp = BASE_TIME.replace(hour=hour, minute=minute) + timedelta(days=day)
    return LogEntry(
        ip=ip,
        timestamp=timestamp,
        method=method,
        url=url,
        status=status,
        bytes_sent=bytes_sent,
        protocol="HTTP/1.1",
        user_agent=user_agent,
        request_time=request_time,
    )


def combined_line(
    url: str = "/",
    *,
    ip: str = "192.0.2.10",
    status: int = 200,
    user_agent: str = GOOGLEBOT,
    day: int = 2,
    hour: int = 10,
    size: int = 1000,
    request_time: float | None = None,
) -> str:
    """Format one access log line in the combined format (March 2026)."""
    line = (
        f'{ip} - - [{day:02d}/Mar/2026:{hour:02d}:15:30 +0000] "GET {url} HTTP/1.1" '
        f'{status} {size} "-" "{user_agent}"'
    )
    if request_time is not None:
        line += f" {request_time:.3f}"
    return line


class FakeResolver:
    """In-memory resolver that records every lookup."""

    def __init__(
        self,
        reverse: dict[str, list[str]] | None = None,
        forward: dict[str, list[str]] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        self.reverse_map = reverse or {}
        self.forward_map = forward or {}
        self.failing = failing or set()
        self.reverse_calls: list[str] = []
        self.forward_calls: list[str] = []

    def reverse(self, ip: str) -> list[str]:
        self.reverse_calls.append(ip)
        if ip in self.failing:
            raise ResolverError(f"lookup failed for {ip}")
        return list(self.reverse_map.get(ip, []))

    def forward(self, hostname: str) -> list[str]:
        self.forward_calls.append(hostname)
        if hostname in self.failing:
            raise ResolverError(f"lookup failed for {hostname}")
        return list(self.forward_map.get(hostname, []))


@pytest.fixture
def google_resolver() -> FakeResolver:
    """Resolver that knows one genuine Googlebot address and one impostor."""
    return FakeResolver(
        reverse={
            "192.0.2.10": ["crawl-192-0-2-10.googlebot.com"],
            "203.0.113.200": ["host-200.isp.example"],
            "203.0.113.201": ["crawl-203-0-113-201.googlebot.com"],
        },
        forward={
            "crawl-192-0-2-10.googlebot.com": ["192.0.2.10"],
            "crawl-203-0-113-201.googlebot.com": ["198.51.100.99"],
        },
    )


@pytest.fixture(scope="session")
def generator_module():
    """The sample log generator, loaded from ``examples/`` without installing it."""
    path = EXAMPLES / "generate_sample_logs.py"
    spec = importlib.util.spec_from_file_location("generate_sample_logs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_log() -> str:
    return str(EXAMPLES / "sample-access.log")


@pytest.fixture
def write_log(tmp_path):
    """Write lines to a log file under ``tmp_path`` and return its path."""

    def _write(lines: list[str], name: str = "access.log") -> str:
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    return _write
