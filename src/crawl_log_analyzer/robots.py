"""robots.txt parsing and matching (RFC 9309).

The analysis uses this to find bot hits on URLs that the site's robots.txt
disallows for that bot.  Matching follows RFC 9309 and the behaviour the
major search engines document:

* A crawler obeys the group whose ``user-agent`` token matches it most
  specifically (the longest matching token); groups that name the same token
  are merged; the ``*`` group applies only when no other group matches.
* Within a group the rule with the longest matching path wins; when an
  ``allow`` and a ``disallow`` rule match with the same length, ``allow`` wins.
* ``*`` matches any sequence of characters and a trailing ``$`` anchors the
  pattern to the end of the URL.  Paths are compared after percent-encoding
  normalisation, and ``/robots.txt`` itself is always allowed.

Access logs record the full user agent string, not the product token a
crawler uses to pick its group, so a group token matches when it appears in
the user agent as a whole word (``googlebot`` matches ``Googlebot/2.1`` and
``Googlebot-Image/1.0``, but not ``NotGooglebotX``).

Nothing is fetched from the network: the file is read from disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "MAX_ROBOTS_BYTES",
    "RobotsDecision",
    "RobotsError",
    "RobotsGroup",
    "RobotsRule",
    "RobotsTxt",
    "load_robots_txt",
    "normalize_robots_path",
]

#: Parsers must read at least 500 KiB (RFC 9309, section 2.5); content after
#: that limit is ignored, as Google does.
MAX_ROBOTS_BYTES = 500 * 1024

#: Decision cache size limits; the caches are cleared when they fill up.
_MAX_AGENT_CACHE = 10_000
_MAX_DECISION_CACHE = 200_000

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PERCENT = re.compile(r"%([0-9A-Fa-f]{2})")
_AGENT_CHARS = re.compile(r"[a-z0-9_-]+")
_KNOWN_DIRECTIVES = frozenset({"sitemap", "crawl-delay", "host", "clean-param", "noindex"})


class RobotsError(ValueError):
    """Raised when a robots.txt file cannot be read."""


def _encode_char(char: str) -> str:
    return "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))


def normalize_robots_path(value: str) -> str:
    """Normalise a URL path or rule pattern for comparison.

    Percent-escapes of unreserved characters are decoded, other escapes are
    upper-cased, and characters outside printable ASCII are percent-encoded
    as UTF-8, so ``/caf%C3%A9``, ``/caf%c3%a9`` and ``/café`` compare equal.
    """

    def _escape(match: re.Match[str]) -> str:
        char = chr(int(match.group(1), 16))
        return char if char in _UNRESERVED else f"%{match.group(1).upper()}"

    value = _PERCENT.sub(_escape, value)
    if value.isascii() and value.isprintable() and " " not in value:
        return value
    return "".join(
        char if char.isascii() and char.isprintable() and char != " " else _encode_char(char)
        for char in value
    )


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    pattern = re.sub(r"\*+", "*", pattern)
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.compile(regex + (r"\Z" if anchored else ""), re.DOTALL)


@dataclass(frozen=True)
class RobotsRule:
    """One ``allow`` or ``disallow`` line."""

    allow: bool
    #: The pattern as written in the file.
    path: str
    #: 1-based line number in the file.
    line: int
    _normalized: str = field(default="", repr=False, compare=False)
    _regex: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def create(cls, allow: bool, path: str, line: int) -> RobotsRule:
        normalized = normalize_robots_path(path)
        return cls(allow, path, line, normalized, _compile_pattern(normalized))

    @property
    def directive(self) -> str:
        return "Allow" if self.allow else "Disallow"

    @property
    def specificity(self) -> int:
        """Precedence of the rule: the length of its (normalised) pattern."""
        return len(self._normalized)

    def matches(self, path: str) -> bool:
        """Whether the rule applies to an already normalised ``path``."""
        assert self._regex is not None
        return self._regex.match(path) is not None

    def describe(self) -> str:
        return f"{self.directive}: {self.path}"


@dataclass(frozen=True)
class RobotsGroup:
    """Every rule that applies to one user-agent token (merged across groups)."""

    #: Lower-cased user-agent token, or ``*``.
    agent: str
    rules: tuple[RobotsRule, ...]


@dataclass(frozen=True)
class RobotsDecision:
    """Outcome of checking one URL for one user agent."""

    allowed: bool
    #: The group that applied, or ``None`` when no group matched the agent.
    group: RobotsGroup | None = None
    #: The deciding rule, or ``None`` when no rule matched (implicit allow).
    rule: RobotsRule | None = None


_ALLOW_ALL = RobotsDecision(True)


class RobotsTxt:
    """A parsed robots.txt file."""

    def __init__(
        self,
        groups: dict[str, RobotsGroup],
        *,
        source: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.groups = groups
        self.source = source
        self.warnings = list(warnings or [])
        self._matchers = sorted(
            (
                (agent, re.compile(rf"(?<![a-z0-9]){re.escape(agent)}(?![a-z0-9])"))
                for agent in groups
                if agent != "*"
            ),
            key=lambda item: (-len(item[0]), item[0]),
        )
        self._agent_cache: dict[str, RobotsGroup | None] = {}
        self._decision_cache: dict[tuple[str, str], RobotsDecision] = {}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str, source: str | None = None) -> RobotsTxt:
        """Parse robots.txt content.

        Unknown or malformed lines are skipped with a warning; they never make
        parsing fail, because crawlers are equally lenient.
        """
        warnings: list[str] = []
        rules_by_agent: dict[str, list[RobotsRule]] = {}
        current: list[str] = []
        in_rules = False
        warned_orphan = False

        for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            key, separator, value = line.partition(":")
            if not separator:
                warnings.append(f"line {number}: no ':' separator, ignored")
                continue
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if in_rules:
                    current = []
                    in_rules = False
                agent = value.lower()
                if agent != "*":
                    match = _AGENT_CHARS.match(agent)
                    agent = match.group(0) if match else ""
                if not agent:
                    warnings.append(f"line {number}: empty or invalid user-agent, ignored")
                    continue
                current.append(agent)
                rules_by_agent.setdefault(agent, [])
            elif key in ("allow", "disallow"):
                in_rules = True
                if not current:
                    if not warned_orphan:
                        warnings.append(f"line {number}: rule before any user-agent line, ignored")
                        warned_orphan = True
                    continue
                if not value:
                    continue  # "Disallow:" with no path allows everything
                rule = RobotsRule.create(key == "allow", value, number)
                for agent in current:
                    rules_by_agent[agent].append(rule)
            elif key in _KNOWN_DIRECTIVES:
                continue
            else:
                warnings.append(f"line {number}: unknown directive {key!r}, ignored")

        groups = {
            agent: RobotsGroup(agent, tuple(rules)) for agent, rules in rules_by_agent.items()
        }
        return cls(groups, source=source, warnings=warnings)

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    @property
    def rule_count(self) -> int:
        return len({id(rule) for group in self.groups.values() for rule in group.rules})

    def group_for(self, user_agent: str) -> RobotsGroup | None:
        """The group a crawler with this user agent string obeys."""
        if user_agent in self._agent_cache:
            return self._agent_cache[user_agent]
        lowered = user_agent.lower()
        group: RobotsGroup | None = None
        for agent, pattern in self._matchers:
            if pattern.search(lowered):
                group = self.groups[agent]
                break
        else:
            group = self.groups.get("*")
        if len(self._agent_cache) >= _MAX_AGENT_CACHE:
            self._agent_cache.clear()
        self._agent_cache[user_agent] = group
        return group

    def decide(self, user_agent: str, url: str) -> RobotsDecision:
        """Check whether ``url`` (path plus optional query) is allowed."""
        group = self.group_for(user_agent)
        if group is None or not group.rules:
            return RobotsDecision(True, group) if group is not None else _ALLOW_ALL
        url = url.split("#", 1)[0] or "/"
        key = (group.agent, url)
        cached = self._decision_cache.get(key)
        if cached is not None:
            return cached
        decision = self._evaluate(group, url)
        if len(self._decision_cache) >= _MAX_DECISION_CACHE:
            self._decision_cache.clear()
        self._decision_cache[key] = decision
        return decision

    def is_allowed(self, user_agent: str, url: str) -> bool:
        return self.decide(user_agent, url).allowed

    @staticmethod
    def _evaluate(group: RobotsGroup, url: str) -> RobotsDecision:
        if url.split("?", 1)[0] == "/robots.txt":
            return RobotsDecision(True, group)
        path = normalize_robots_path(url)
        best: RobotsRule | None = None
        for rule in group.rules:
            if not rule.matches(path):
                continue
            if (
                best is None
                or rule.specificity > best.specificity
                or (rule.specificity == best.specificity and rule.allow and not best.allow)
            ):
                best = rule
        if best is None:
            return RobotsDecision(True, group)
        return RobotsDecision(best.allow, group, best)


def load_robots_txt(path: str) -> RobotsTxt:
    """Read and parse a robots.txt file from disk.

    Raises :class:`RobotsError` for files that are clearly not robots.txt
    (binary content) and :class:`OSError` when the file cannot be read.
    """
    with open(path, "rb") as handle:
        data = handle.read(MAX_ROBOTS_BYTES + 1)
    warnings: list[str] = []
    if len(data) > MAX_ROBOTS_BYTES:
        data = data[:MAX_ROBOTS_BYTES]
        warnings.append(
            f"{path} is larger than {MAX_ROBOTS_BYTES // 1024} KiB; the rest is ignored"
        )
    if b"\x00" in data:
        raise RobotsError("file contains NUL bytes; is it really a robots.txt file?")
    robots = RobotsTxt.parse(data.decode("utf-8", errors="replace"), source=path)
    robots.warnings[:0] = warnings
    if not robots.groups:
        robots.warnings.append(f"{path} contains no user-agent groups; nothing is disallowed")
    return robots
