#!/usr/bin/env python3
"""Generate a synthetic access log for trying out crawl-log-analyzer.

Everything is made up.  Client addresses come only from the three IPv4
blocks reserved for documentation (RFC 5737), so no line can ever point at a
real machine:

* ``192.0.2.0/24``    - "genuine" Googlebot (matches googlebot-ranges.example.json)
* ``198.51.100.0/24`` - every other crawler
* ``203.0.113.0/24``  - human visitors (1-199) and fake Googlebots (200-250)

The output is deterministic for a given ``--seed``.

Usage::

    python examples/generate_sample_logs.py --days 30 --output access.log
    python examples/generate_sample_logs.py --days 30 --migration-day 15 --gzip -o access.log.gz
    python examples/generate_sample_logs.py --sitemap sitemap.xml
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

BASE_URL = "https://www.example.com"

GOOGLEBOT_SMARTPHONE = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
GOOGLEBOT_DESKTOP = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
GOOGLEBOT_IMAGE = "Googlebot-Image/1.0"

#: (user agent, relative weight, last-octet range in 198.51.100.0/24)
OTHER_BOTS: tuple[tuple[str, int, tuple[int, int]], ...] = (
    (
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        22,
        (1, 30),
    ),
    ("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)", 6, (31, 40)),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like "
        "Gecko) Version/17.4 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
        5,
        (41, 50),
    ),
    ("DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)", 3, (51, 55)),
    (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; "
        "+https://openai.com/gptbot",
        12,
        (60, 75),
    ),
    ("CCBot/2.0 (https://commoncrawl.org/faq/)", 5, (76, 85)),
    (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; "
        "+https://perplexity.ai/perplexitybot)",
        4,
        (86, 95),
    ),
    ("Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)", 14, (100, 130)),
    ("Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)", 9, (131, 160)),
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", 3, (161, 170)),
    ("Mozilla/5.0 (compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)", 4, (171, 175)),
)

BROWSERS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like "
    "Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
)

CATEGORIES = ("shoes", "jackets", "bags", "hats", "watches", "socks")
COLORS = ("black", "blue", "red", "green", "white")
SORTS = ("price_asc", "price_desc", "newest", "rating")
SEARCH_TERMS = ("boots", "rain+jacket", "gift", "sale", "leather+bag")
ASSETS = (
    "/static/css/main.4f2a.css",
    "/static/js/app.91bc.js",
    "/static/js/vendor.77aa.js",
    "/static/img/logo.svg",
    "/static/fonts/inter.woff2",
    "/favicon.ico",
)


def product_paths(prefix: str = "/products") -> list[str]:
    """All product URLs under ``prefix`` (the section the migration moves)."""
    return [
        f"{prefix}/{category}/item-{number:03d}"
        for category in CATEGORIES
        for number in range(1, 21)
    ]


def blog_paths() -> list[str]:
    return [f"/blog/post-{number:02d}" for number in range(1, 41)]


def core_paths() -> list[str]:
    paths = ["/", "/about", "/contact", "/blog/", "/products/"]
    paths += [f"/products/{category}/" for category in CATEGORIES]
    return paths


def sitemap_paths(migrated: bool = False) -> list[str]:
    """Paths the example sitemap lists.

    Deliberately imperfect: it lists a few pages no bot ever requests
    (``/landing/...``) and omits the last ten blog posts, so both orphan
    reports have something to show.
    """
    prefix = "/shop" if migrated else "/products"
    paths = ["/", "/about", "/contact", "/blog/"]
    paths += blog_paths()[:30]
    paths += product_paths(prefix)
    paths += [f"/landing/campaign-{number}" for number in range(1, 6)]
    return paths


def sitemap_xml(migrated: bool = False) -> str:
    """The example sitemap as an XML string."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    lines += [f"  <url><loc>{BASE_URL}{path}</loc></url>" for path in sitemap_paths(migrated)]
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


@dataclass
class Hit:
    """One synthetic request."""

    ip: str
    timestamp: datetime
    url: str
    status: int
    size: int
    referer: str
    user_agent: str
    request_time: float

    def clf_time(self) -> str:
        return self.timestamp.strftime("%d/%b/%Y:%H:%M:%S +0000")

    def combined(self, timed: bool = False) -> str:
        line = (
            f'{self.ip} - - [{self.clf_time()}] "GET {self.url} HTTP/1.1" {self.status} '
            f'{self.size} "{self.referer}" "{self.user_agent}"'
        )
        return f"{line} {self.request_time:.3f}" if timed else line

    def json(self) -> str:
        return json.dumps(
            {
                "time": self.timestamp.isoformat(),
                "remote_addr": self.ip,
                "method": "GET",
                "uri": self.url,
                "protocol": "HTTP/1.1",
                "status": self.status,
                "body_bytes_sent": self.size,
                "http_referer": self.referer,
                "http_user_agent": self.user_agent,
                "request_time": round(self.request_time, 3),
            }
        )


class Generator:
    """Deterministic traffic model for a small shop with a blog."""

    def __init__(
        self,
        days: int = 14,
        hits_per_day: int = 400,
        seed: int = 7,
        start: date = date(2026, 3, 2),
        migration_day: int = 0,
    ) -> None:
        if days < 1 or hits_per_day < 1:
            raise ValueError("days and hits_per_day must be positive")
        self.days = days
        self.hits_per_day = hits_per_day
        self.start = start
        self.migration_day = migration_day
        self.rng = random.Random(seed)
        #: Day index (0-based) on which Googlebot crawls four times as much.
        self.spike_day = min(9, days - 1) if days >= 8 else -1
        #: Day index on which the origin answers many requests with 503.
        self.outage_day = min(11, days - 1) if days >= 12 else -1

    # -- URL choice --------------------------------------------------------

    def _migrated(self, day_index: int) -> bool:
        return bool(self.migration_day) and day_index >= self.migration_day

    def _page(self, day_index: int) -> tuple[str, int]:
        """Pick a URL a crawler requests and the status the site answers with."""
        rng = self.rng
        migrated = self._migrated(day_index)
        roll = rng.random()
        if roll < 0.08:
            return rng.choice(core_paths()[:4]), 200
        if roll < 0.30:
            # Popular posts are crawled far more often than the long tail.
            index = min(int(rng.expovariate(1 / 6)), 39)
            return blog_paths()[index], 200
        if roll < 0.62:
            old = rng.choice(product_paths("/products"))
            if not migrated:
                return old, 200
            # After the migration bots still request old URLs for a while.
            if rng.random() < 0.35:
                return old, 301
            return old.replace("/products", "/shop", 1), 200
        if roll < 0.74:
            category = rng.choice(CATEGORIES)
            prefix = "/shop" if migrated else "/products"
            query = f"color={rng.choice(COLORS)}&sort={rng.choice(SORTS)}"
            if rng.random() < 0.4:
                query += f"&page={rng.randint(2, 9)}"
            return f"{prefix}/{category}/?{query}", 200
        if roll < 0.79:
            return f"/search?q={rng.choice(SEARCH_TERMS)}", 200
        if roll < 0.84:
            page = rng.choice(blog_paths()[:10])
            return f"{page}?utm_source=newsletter&utm_medium=email", 200
        if roll < 0.89:
            return f"/old-catalog/item-{rng.randint(1, 60)}.html", 404
        if roll < 0.91:
            return f"/discontinued/model-{rng.randint(1, 8)}", 410
        if roll < 0.95:
            return f"/blog/post-{rng.randint(1, 10):02d}/", 301
        if roll < 0.97:
            return rng.choice(("/About", "/about/", "/Contact", "/blog")), 200
        return rng.choice(ASSETS), 200

    def _human_page(self) -> tuple[str, int]:
        rng = self.rng
        roll = rng.random()
        if roll < 0.35:
            return rng.choice(ASSETS), 200
        if roll < 0.6:
            return rng.choice(blog_paths()[:15]), 200
        if roll < 0.9:
            return rng.choice(product_paths()), 200
        return "/", 200

    # -- request assembly --------------------------------------------------

    def _size(self, url: str, status: int) -> int:
        if status in (301, 302, 304):
            return 0 if status == 304 else 178
        if status >= 400:
            return self.rng.randint(300, 900)
        if url.startswith("/static/") or url == "/favicon.ico":
            return self.rng.randint(2_000, 90_000)
        return self.rng.randint(8_000, 60_000)

    def _hit(self, moment: datetime, day_index: int, kind: str) -> Hit:
        rng = self.rng
        referer = "-"
        if kind == "human":
            url, status = self._human_page()
            ip = f"203.0.113.{rng.randint(1, 199)}"
            agent = rng.choice(BROWSERS)
            referer = rng.choice(("-", f"{BASE_URL}/", "https://search.example/"))
        else:
            url, status = self._page(day_index)
            if kind == "google":
                ip = f"192.0.2.{rng.randint(1, 120)}"
                roll = rng.random()
                if url.startswith("/static/img/"):
                    agent = GOOGLEBOT_IMAGE
                elif roll < 0.8:
                    agent = GOOGLEBOT_SMARTPHONE
                else:
                    agent = GOOGLEBOT_DESKTOP
            elif kind == "fake":
                ip = f"203.0.113.{rng.randint(200, 250)}"
                agent = GOOGLEBOT_DESKTOP
            else:
                agents = [bot[0] for bot in OTHER_BOTS]
                weights = [bot[1] for bot in OTHER_BOTS]
                index = rng.choices(range(len(agents)), weights=weights)[0]
                low, high = OTHER_BOTS[index][2]
                ip = f"198.51.100.{rng.randint(low, high)}"
                agent = agents[index]
        request_time = rng.lognormvariate(-2.2, 0.6)
        in_outage = day_index == self.outage_day and 9 <= moment.hour < 13
        if status == 200 and in_outage and rng.random() < 0.6:
            status, request_time = 503, rng.uniform(5.0, 30.0)
        if status == 200 and rng.random() < 0.004:
            status = 500
        return Hit(
            ip=ip,
            timestamp=moment,
            url=url,
            status=status,
            size=self._size(url, status),
            referer=referer,
            user_agent=agent,
            request_time=request_time,
        )

    def _day_plan(self, day_index: int) -> list[str]:
        """Who makes each request of the day, before timestamps are drawn."""
        rng = self.rng
        weekday = (self.start + timedelta(days=day_index)).weekday()
        volume = self.hits_per_day * (0.85 if weekday >= 5 else 1.0)
        volume *= rng.uniform(0.92, 1.08)
        google = volume * 0.34 * (4.0 if day_index == self.spike_day else 1.0)
        counts = {
            "google": google,
            "other": volume * 0.26,
            "fake": volume * 0.03,
            "human": volume * 0.37,
        }
        plan: list[str] = []
        for kind, amount in counts.items():
            plan.extend([kind] * max(1, round(amount)))
        return plan

    def hits(self) -> Iterator[Hit]:
        """Yield every request in chronological order."""
        rng = self.rng
        for day_index in range(self.days):
            midnight = datetime.combine(
                self.start + timedelta(days=day_index), datetime.min.time(), tzinfo=timezone.utc
            )
            moments = []
            for kind in self._day_plan(day_index):
                if kind == "human":
                    hour = min(23.99, max(0.0, rng.gauss(14, 4)))
                else:
                    # Crawlers favour the night but never stop entirely.
                    hour = rng.uniform(0, 24) if rng.random() < 0.6 else rng.uniform(0, 7)
                moments.append((midnight + timedelta(seconds=int(hour * 3600)), kind))
            moments.sort(key=lambda item: item[0])
            for moment, kind in moments:
                yield self._hit(moment, day_index, kind)

    def lines(self, fmt: str = "combined") -> Iterator[str]:
        """Yield formatted log lines (``combined``, ``combined-timed`` or ``json``)."""
        if fmt not in ("combined", "combined-timed", "json"):
            raise ValueError(f"unknown format: {fmt}")
        for hit in self.hits():
            if fmt == "json":
                yield hit.json()
            else:
                yield hit.combined(timed=fmt == "combined-timed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=14, help="number of days (default 14)")
    parser.add_argument("--hits-per-day", type=int, default=400, help="requests per weekday")
    parser.add_argument("--seed", type=int, default=7, help="random seed (default 7)")
    parser.add_argument("--start", default="2026-03-02", help="first day, YYYY-MM-DD")
    parser.add_argument(
        "--format",
        choices=("combined", "combined-timed", "json"),
        default="combined-timed",
        help="log line format (default: combined with a trailing $request_time)",
    )
    parser.add_argument(
        "--migration-day",
        type=int,
        default=0,
        help="day number (1-based offset) from which /products/ moves to /shop/; 0 = never",
    )
    parser.add_argument("--gzip", action="store_true", help="gzip the log output")
    parser.add_argument("--output", "-o", default="-", help="log file to write (default stdout)")
    parser.add_argument("--sitemap", help="write the example sitemap to this path and exit")
    args = parser.parse_args(argv)

    if args.sitemap:
        with open(args.sitemap, "w", encoding="utf-8") as handle:
            handle.write(sitemap_xml(migrated=bool(args.migration_day)))
        return 0
    try:
        generator = Generator(
            days=args.days,
            hits_per_day=args.hits_per_day,
            seed=args.seed,
            start=date.fromisoformat(args.start),
            migration_day=args.migration_day,
        )
    except ValueError as exc:
        parser.error(str(exc))
    lines = generator.lines(args.format)
    if args.output == "-":
        if args.gzip:
            parser.error("--gzip needs --output FILE")
        try:
            for line in lines:
                sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except BrokenPipeError:  # e.g. piping into `head`
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    elif args.gzip:
        with gzip.open(args.output, "wt", encoding="utf-8") as handle:
            handle.writelines(line + "\n" for line in lines)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.writelines(line + "\n" for line in lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
