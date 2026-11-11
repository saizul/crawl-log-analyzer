"""User-agent classification for crawlers.

The signature table is ordered: the first signature whose pattern matches
wins, so specific variants (``Googlebot-Image``) are listed before generic
ones (``Googlebot``).  Classification only looks at the user-agent string and
is therefore trivially spoofable - see :mod:`crawl_log_analyzer.verify` for
confirming that a hit really came from the operator it claims.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "DEFAULT_SIGNATURES",
    "BotClassifier",
    "BotMatch",
    "BotSignature",
]

SEARCH = "search"
AI = "ai"
SEO = "seo"
SOCIAL = "social"
MONITORING = "monitoring"
OTHER = "other"

CATEGORIES: tuple[str, ...] = (SEARCH, AI, SEO, SOCIAL, MONITORING, OTHER)

CATEGORY_LABELS: dict[str, str] = {
    SEARCH: "Search engine",
    AI: "AI crawler",
    SEO: "SEO tool",
    SOCIAL: "Social preview",
    MONITORING: "Monitoring",
    OTHER: "Other bot",
}


@dataclass(frozen=True)
class BotMatch:
    """Result of classifying a user agent."""

    name: str
    family: str
    category: str
    dns_suffixes: tuple[str, ...] = ()

    @property
    def verifiable_by_dns(self) -> bool:
        return bool(self.dns_suffixes)


@dataclass(frozen=True)
class BotSignature:
    """A named user-agent pattern.

    ``pattern`` must be found in the user agent; when ``requires`` is set it
    must be found as well (used to tell smartphone and desktop variants
    apart).  ``dns_suffixes`` are the host name suffixes the operator
    documents for reverse-DNS verification.
    """

    name: str
    family: str
    category: str
    pattern: re.Pattern[str]
    requires: re.Pattern[str] | None = None
    dns_suffixes: tuple[str, ...] = ()

    def matches(self, user_agent: str) -> bool:
        if self.pattern.search(user_agent) is None:
            return False
        return self.requires is None or self.requires.search(user_agent) is not None

    def to_match(self) -> BotMatch:
        return BotMatch(self.name, self.family, self.category, self.dns_suffixes)


def _sig(
    name: str,
    family: str,
    category: str,
    pattern: str,
    *,
    requires: str | None = None,
    dns: Sequence[str] = (),
) -> BotSignature:
    return BotSignature(
        name=name,
        family=family,
        category=category,
        pattern=re.compile(pattern, re.IGNORECASE),
        requires=re.compile(requires, re.IGNORECASE) if requires else None,
        dns_suffixes=tuple(dns),
    )


_GOOGLE_DNS = (".googlebot.com", ".google.com", ".googleusercontent.com")
_BING_DNS = (".search.msn.com",)
_YANDEX_DNS = (".yandex.ru", ".yandex.net", ".yandex.com")
_BAIDU_DNS = (".baidu.com", ".baidu.jp")
_APPLE_DNS = (".applebot.apple.com",)
_YAHOO_DNS = (".crawl.yahoo.net",)
_MOBILE = r"\b(?:Android|Mobile|iPhone)\b"

DEFAULT_SIGNATURES: tuple[BotSignature, ...] = (
    # --- Google -----------------------------------------------------------
    _sig("AdsBot Mobile", "Google", SEARCH, r"AdsBot-Google-Mobile", dns=_GOOGLE_DNS),
    _sig("AdsBot", "Google", SEARCH, r"AdsBot-Google", dns=_GOOGLE_DNS),
    _sig("Google AdSense", "Google", SEARCH, r"Mediapartners-Google", dns=_GOOGLE_DNS),
    _sig("Googlebot Image", "Google", SEARCH, r"Googlebot-Image", dns=_GOOGLE_DNS),
    _sig("Googlebot Video", "Google", SEARCH, r"Googlebot-Video", dns=_GOOGLE_DNS),
    _sig("Googlebot News", "Google", SEARCH, r"Googlebot-News", dns=_GOOGLE_DNS),
    _sig("Google StoreBot", "Google", SEARCH, r"Storebot-Google", dns=_GOOGLE_DNS),
    _sig("Google InspectionTool", "Google", SEARCH, r"Google-InspectionTool", dns=_GOOGLE_DNS),
    _sig("Google CloudVertexBot", "Google", AI, r"Google-CloudVertexBot", dns=_GOOGLE_DNS),
    _sig("GoogleOther", "Google", SEARCH, r"GoogleOther", dns=_GOOGLE_DNS),
    _sig("Google APIs", "Google", OTHER, r"APIs-Google", dns=_GOOGLE_DNS),
    _sig("Google FeedFetcher", "Google", OTHER, r"FeedFetcher-Google", dns=_GOOGLE_DNS),
    _sig("Google Read Aloud", "Google", OTHER, r"Google-Read-Aloud", dns=_GOOGLE_DNS),
    _sig("Google Site Verifier", "Google", OTHER, r"Google-Site-Verification", dns=_GOOGLE_DNS),
    _sig(
        "Googlebot Smartphone",
        "Google",
        SEARCH,
        r"Googlebot",
        requires=_MOBILE,
        dns=_GOOGLE_DNS,
    ),
    _sig("Googlebot Desktop", "Google", SEARCH, r"Googlebot", dns=_GOOGLE_DNS),
    # --- Microsoft Bing ---------------------------------------------------
    _sig("BingPreview", "Bing", SEARCH, r"BingPreview", dns=_BING_DNS),
    _sig("AdIdxBot", "Bing", SEARCH, r"adidxbot", dns=_BING_DNS),
    _sig("Bingbot", "Bing", SEARCH, r"bingbot", dns=_BING_DNS),
    _sig("MSNBot", "Bing", SEARCH, r"msnbot", dns=_BING_DNS),
    # --- Yandex -----------------------------------------------------------
    _sig("YandexImages", "Yandex", SEARCH, r"YandexImages", dns=_YANDEX_DNS),
    _sig("YandexMobileBot", "Yandex", SEARCH, r"YandexMobileBot", dns=_YANDEX_DNS),
    _sig("YandexBot", "Yandex", SEARCH, r"YandexBot", dns=_YANDEX_DNS),
    _sig("Yandex (other)", "Yandex", SEARCH, r"Yandex[A-Za-z]+/", dns=_YANDEX_DNS),
    # --- Baidu ------------------------------------------------------------
    _sig("Baiduspider Image", "Baidu", SEARCH, r"Baiduspider-image", dns=_BAIDU_DNS),
    _sig("Baiduspider", "Baidu", SEARCH, r"Baiduspider", dns=_BAIDU_DNS),
    # --- Other search engines --------------------------------------------
    _sig("DuckAssistBot", "DuckDuckGo", AI, r"DuckAssistBot"),
    _sig("DuckDuckBot", "DuckDuckGo", SEARCH, r"DuckDuckBot|DuckDuckGo-Favicons-Bot"),
    _sig("Applebot", "Apple", SEARCH, r"Applebot", dns=_APPLE_DNS),
    _sig("Yahoo Slurp", "Yahoo", SEARCH, r"Yahoo! Slurp", dns=_YAHOO_DNS),
    _sig("Sogou Spider", "Sogou", SEARCH, r"Sogou\b.*spider"),
    _sig("SeznamBot", "Seznam", SEARCH, r"SeznamBot", dns=(".seznam.cz",)),
    _sig("Naver Yeti", "Naver", SEARCH, r"\bYeti/", dns=(".naver.com",)),
    _sig("PetalBot", "Petal", SEARCH, r"PetalBot", dns=(".petalsearch.com",)),
    _sig("Qwantbot", "Qwant", SEARCH, r"Qwantbot|Qwantify"),
    _sig("MojeekBot", "Mojeek", SEARCH, r"MojeekBot", dns=(".mojeek.com",)),
    _sig("Bravebot", "Brave", SEARCH, r"Bravebot"),
    # --- AI crawlers and fetchers ----------------------------------------
    _sig("GPTBot", "OpenAI", AI, r"GPTBot"),
    _sig("ChatGPT-User", "OpenAI", AI, r"ChatGPT-User"),
    _sig("OAI-SearchBot", "OpenAI", AI, r"OAI-SearchBot"),
    _sig("CCBot", "Common Crawl", AI, r"CCBot"),
    _sig("PerplexityBot", "Perplexity", AI, r"PerplexityBot"),
    _sig("Perplexity-User", "Perplexity", AI, r"Perplexity-User"),
    _sig("Bytespider", "ByteDance", AI, r"Bytespider"),
    _sig("Amazonbot", "Amazon", AI, r"Amazonbot"),
    _sig("Meta-ExternalAgent", "Meta", AI, r"meta-externalagent"),
    _sig("Meta-ExternalFetcher", "Meta", AI, r"meta-externalfetcher"),
    _sig("cohere-ai", "Cohere", AI, r"cohere-(?:ai|training-data-crawler)"),
    _sig("Diffbot", "Diffbot", AI, r"Diffbot"),
    _sig("YouBot", "You.com", AI, r"YouBot"),
    _sig("MistralAI-User", "Mistral", AI, r"MistralAI-User"),
    _sig("AI2Bot", "Allen Institute", AI, r"AI2Bot"),
    _sig("Timpibot", "Timpi", AI, r"Timpibot"),
    _sig("ImagesiftBot", "Imagesift", AI, r"ImagesiftBot"),
    _sig("Omgili", "Webz.io", AI, r"omgili(?:bot)?"),
    # --- SEO tools --------------------------------------------------------
    _sig("AhrefsSiteAudit", "Ahrefs", SEO, r"AhrefsSiteAudit"),
    _sig("AhrefsBot", "Ahrefs", SEO, r"AhrefsBot"),
    _sig("SiteAuditBot", "Semrush", SEO, r"SiteAuditBot"),
    _sig("SemrushBot", "Semrush", SEO, r"Semrush(?:Bot)?"),
    _sig("MJ12bot", "Majestic", SEO, r"MJ12bot"),
    _sig("DotBot", "Moz", SEO, r"DotBot"),
    _sig("Rogerbot", "Moz", SEO, r"rogerbot"),
    _sig("Screaming Frog", "Screaming Frog", SEO, r"Screaming Frog SEO Spider"),
    _sig("Sitebulb", "Sitebulb", SEO, r"Sitebulb"),
    _sig("DataForSeoBot", "DataForSEO", SEO, r"DataForSeoBot"),
    _sig("Serpstatbot", "Serpstat", SEO, r"serpstatbot"),
    _sig("BLEXBot", "WebMeUp", SEO, r"BLEXBot"),
    _sig("SEOkicks", "SEOkicks", SEO, r"SEOkicks"),
    # --- Social / link previews ------------------------------------------
    _sig("Facebook", "Meta", SOCIAL, r"facebookexternalhit|facebookcatalog|Facebot"),
    _sig("Twitterbot", "X", SOCIAL, r"Twitterbot"),
    _sig("LinkedInBot", "LinkedIn", SOCIAL, r"LinkedInBot"),
    _sig("Pinterestbot", "Pinterest", SOCIAL, r"Pinterestbot|Pinterest/\d"),
    _sig("Slackbot", "Slack", SOCIAL, r"Slackbot|Slack-ImgProxy"),
    _sig("Discordbot", "Discord", SOCIAL, r"Discordbot"),
    _sig("TelegramBot", "Telegram", SOCIAL, r"TelegramBot"),
    _sig("WhatsApp", "Meta", SOCIAL, r"WhatsApp/"),
    # --- Monitoring -------------------------------------------------------
    _sig("UptimeRobot", "UptimeRobot", MONITORING, r"UptimeRobot"),
    _sig("Pingdom", "Pingdom", MONITORING, r"Pingdom"),
    _sig("StatusCake", "StatusCake", MONITORING, r"StatusCake"),
    _sig("Site24x7", "Site24x7", MONITORING, r"Site24x7"),
    _sig("GTmetrix", "GTmetrix", MONITORING, r"GTmetrix"),
    _sig("Lighthouse", "Google", MONITORING, r"Chrome-Lighthouse"),
    # --- Generic fallbacks: HTTP libraries and self-declared bots ---------
    _sig(
        "HTTP library",
        "Generic",
        OTHER,
        r"^(?:python-requests|python-urllib|curl|wget|Go-http-client|okhttp|Java|libwww-perl"
        r"|Apache-HttpClient|axios|node-fetch|aiohttp|httpx|Scrapy|PycURL|Ruby|PHP)\b",
    ),
    _sig(
        "Unknown bot",
        "Generic",
        OTHER,
        r"\w+bot/|\bbot\b|bot[;)]|crawl|spider|slurp|scrape|fetcher|HeadlessChrome|PhantomJS"
        r"|archiver",
    ),
)

_MAX_CACHE = 50_000


class BotClassifier:
    """Classify user agents against an ordered signature table.

    Results are memoised per distinct user-agent string; real logs contain
    few distinct agents compared to the number of lines.
    """

    def __init__(self, signatures: Iterable[BotSignature] | None = None) -> None:
        self.signatures: tuple[BotSignature, ...] = (
            tuple(signatures) if signatures is not None else DEFAULT_SIGNATURES
        )
        self._cache: dict[str, BotMatch | None] = {}

    def with_extra(self, extra: Iterable[BotSignature]) -> BotClassifier:
        """Return a classifier where ``extra`` signatures take precedence."""
        return BotClassifier((*extra, *self.signatures))

    def classify(self, user_agent: str) -> BotMatch | None:
        """Return the matching bot, or ``None`` for browsers and empty agents."""
        try:
            return self._cache[user_agent]
        except KeyError:
            pass
        result: BotMatch | None = None
        if user_agent and user_agent != "-":
            for signature in self.signatures:
                if signature.matches(user_agent):
                    result = signature.to_match()
                    break
        if len(self._cache) >= _MAX_CACHE:
            self._cache.clear()
        self._cache[user_agent] = result
        return result

    def families(self) -> list[str]:
        """Distinct family names, in table order."""
        seen: dict[str, None] = {}
        for signature in self.signatures:
            seen.setdefault(signature.family, None)
        return list(seen)

    def dns_suffixes_for(self, family: str) -> tuple[str, ...]:
        """Union of documented reverse-DNS suffixes for a family."""
        suffixes: dict[str, None] = {}
        for signature in self.signatures:
            if signature.family.lower() == family.lower():
                for suffix in signature.dns_suffixes:
                    suffixes.setdefault(suffix, None)
        return tuple(suffixes)
