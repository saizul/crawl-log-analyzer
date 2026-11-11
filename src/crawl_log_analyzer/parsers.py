"""Access-log line parsers.

Three families of parser are provided:

* :class:`RegexLineParser` - built from an Nginx ``log_format`` string or an
  Apache ``LogFormat`` string by :func:`compile_log_format`.  The predefined
  ``combined``, ``combined-timed`` and ``common`` formats are compiled the
  same way, so a custom format is never a second-class citizen.
* :class:`JsonLineParser` - one JSON object per line, with tolerant field
  name aliases and optional dotted-path overrides.
* :func:`detect_format` - picks the best predefined parser for a sample of
  lines.

Every parser exposes ``parse(line) -> LogEntry | None``; ``None`` means the
line is malformed and should be counted, not raised.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import LogEntry

__all__ = [
    "APACHE_COMBINED",
    "APACHE_COMMON",
    "FORMAT_ALIASES",
    "NGINX_COMBINED",
    "NGINX_COMBINED_TIMED",
    "JsonLineParser",
    "LineParser",
    "LogFormatError",
    "RegexLineParser",
    "compile_log_format",
    "detect_format",
    "get_parser",
    "parse_clf_time",
    "parse_iso_time",
    "parse_request_line",
    "parse_timestamp",
]

NGINX_COMBINED = (
    '$remote_addr - $remote_user [$time_local] "$request" '
    '$status $body_bytes_sent "$http_referer" "$http_user_agent"'
)
NGINX_COMBINED_TIMED = NGINX_COMBINED + " $request_time"
APACHE_COMMON = '%h %l %u %t "%r" %>s %b'
APACHE_COMBINED = '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"'

#: Accepted ``--format`` names mapped to their canonical parser name.
FORMAT_ALIASES: dict[str, str] = {
    "combined": "combined",
    "nginx": "combined",
    "nginx-combined": "combined",
    "apache-combined": "combined",
    "combined-timed": "combined-timed",
    "nginx-timed": "combined-timed",
    "common": "common",
    "apache-common": "common",
    "clf": "common",
    "json": "json",
    "jsonl": "json",
    "ndjson": "json",
}


class LogFormatError(ValueError):
    """Raised when a log format string cannot be compiled or is unusable."""


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------

_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

_TZ_CACHE: dict[str, timezone] = {"": timezone.utc, "Z": timezone.utc, "+0000": timezone.utc}

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d{1,9}))?\s*(Z|z|[+-]\d{2}:?\d{2}|[+-]\d{2})?$"
)
_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _parse_offset(text: str) -> timezone:
    """Turn ``+0530``, ``-08:00``, ``+01`` or ``Z`` into a ``timezone``."""
    cached = _TZ_CACHE.get(text)
    if cached is not None:
        return cached
    raw = text.replace(":", "")
    if raw in ("Z", "z"):
        tz = timezone.utc
    else:
        if len(raw) not in (3, 5) or raw[0] not in "+-" or not raw[1:].isdigit():
            raise ValueError(f"invalid UTC offset: {text!r}")
        hours = int(raw[1:3])
        minutes = int(raw[3:5]) if len(raw) == 5 else 0
        if hours > 23 or minutes > 59:
            raise ValueError(f"invalid UTC offset: {text!r}")
        delta = timedelta(hours=hours, minutes=minutes)
        tz = timezone(-delta if raw[0] == "-" else delta)
    if len(_TZ_CACHE) < 256:
        _TZ_CACHE[text] = tz
    return tz


def parse_clf_time(value: str) -> datetime:
    """Parse a Common Log Format timestamp such as ``10/Oct/2026:13:55:36 +0000``.

    A missing UTC offset is interpreted as UTC.  Raises ``ValueError`` on
    anything that is not a valid timestamp.
    """
    value = value.strip()
    if len(value) < 20 or value[2] != "/" or value[6] != "/" or value[11] != ":":
        raise ValueError(f"not a CLF timestamp: {value!r}")
    try:
        month = _MONTHS[value[3:6]]
    except KeyError:
        raise ValueError(f"unknown month in timestamp: {value!r}") from None
    tz = _parse_offset(value[20:].strip())
    return datetime(
        int(value[7:11]),
        month,
        int(value[0:2]),
        int(value[12:14]),
        int(value[15:17]),
        int(value[18:20]),
        tzinfo=tz,
    )


def parse_iso_time(value: str) -> datetime:
    """Parse an ISO 8601 timestamp (``2026-10-10T13:55:36+00:00`` and friends).

    Accepts a space instead of ``T``, fractional seconds of any precision, and
    ``Z``/``+hh:mm``/``+hhmm``/``+hh`` offsets.  A missing offset means UTC.
    """
    match = _ISO_RE.match(value.strip())
    if match is None:
        raise ValueError(f"not an ISO 8601 timestamp: {value!r}")
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    micro = int((fraction or "0").ljust(6, "0")[:6])
    tz = _parse_offset(offset) if offset else timezone.utc
    return datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tzinfo=tz
    )


def _from_epoch(number: float) -> datetime:
    # Heuristic unit detection: seconds, milliseconds or microseconds.
    if number > 1e14:
        number /= 1_000_000
    elif number > 1e11:
        number /= 1_000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError) as exc:
        raise ValueError(f"epoch timestamp out of range: {number!r}") from exc


def parse_timestamp(value: Any) -> datetime:
    """Parse a timestamp in any supported representation.

    Supported: CLF (``$time_local``), ISO 8601 (``$time_iso8601``), and Unix
    epoch numbers in seconds, milliseconds or microseconds (``$msec``).
    """
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if not isinstance(value, str):
        raise ValueError(f"unsupported timestamp type: {type(value).__name__}")
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if len(text) > 11 and text[2] == "/" and text[11] == ":":
        return parse_clf_time(text)
    if _NUMBER_RE.match(text):
        return _from_epoch(float(text))
    return parse_iso_time(text)


# --------------------------------------------------------------------------
# Field conversion
# --------------------------------------------------------------------------

_METHOD_RE = re.compile(r"^[A-Z][A-Z-]{1,15}$")
_ABSOLUTE_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*")


def parse_request_line(request: str) -> tuple[str, str, str] | None:
    """Split ``"GET /path HTTP/1.1"`` into ``(method, url, protocol)``.

    Returns ``None`` for request lines that are not HTTP (TLS handshakes sent
    to a plain-text port, empty requests logged as ``-`` and so on).  URLs that
    contain raw spaces are kept intact.
    """
    parts = request.strip().split(" ")
    if len(parts) < 2:
        return None
    method = parts[0]
    if not _METHOD_RE.match(method):
        return None
    if len(parts) == 2:
        return method, parts[1], ""
    if parts[-1].upper().startswith("HTTP/"):
        return method, " ".join(parts[1:-1]), parts[-1]
    return method, " ".join(parts[1:]), ""


def _clean_url(url: str) -> str:
    """Reduce absolute-form request targets to origin-form (path + query)."""
    if url.startswith("/"):
        return url
    match = _ABSOLUTE_URL_RE.match(url)
    if match is not None:
        rest = url[match.end() :]
        if not rest:
            return "/"
        return rest if rest.startswith("/") else "/" + rest
    return url


def _dash_to_empty(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text == "-" else text


def _to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "-" or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    if isinstance(value, (int, float)):
        return int(value)
    return int(float(value)) if "." in value else int(value)


def _to_seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = str(value).strip()
    if not text or text == "-":
        return None
    # $upstream_response_time may hold several values: "0.010, 0.250".
    if "," in text or " " in text or ":" in text:
        total = 0.0
        seen = False
        for piece in re.split(r"[,:\s]+", text):
            if not piece or piece == "-":
                continue
            total += float(piece)
            seen = True
        return total if seen else None
    number = float(text)
    return number if number >= 0 else None


def entry_from_fields(fields: Mapping[str, Any]) -> LogEntry | None:
    """Build a :class:`LogEntry` from canonical field names.

    Canonical names: ``ip``, ``time``, ``request`` (or ``method`` + ``url``
    [+ ``query``] [+ ``protocol``]), ``status``, ``bytes``, ``referer``,
    ``user_agent``, ``request_time`` (seconds), ``request_time_us``
    (microseconds), ``request_time_ms`` (milliseconds) and ``host``.

    Returns ``None`` when a mandatory field is missing or unparseable.
    """
    try:
        raw_time = fields.get("time")
        if raw_time is None or raw_time == "":
            return None
        timestamp = parse_timestamp(raw_time)

        request = fields.get("request")
        url = fields.get("url")
        method = fields.get("method")
        protocol = _dash_to_empty(fields.get("protocol"))
        if url:
            url = str(url)
            query = _dash_to_empty(fields.get("query"))
            if query and "?" not in url:
                url = f"{url}?{query.lstrip('?')}"
            method = _dash_to_empty(method).upper() or "GET"
        elif request:
            parsed = parse_request_line(str(request))
            if parsed is None:
                return None
            method, url, protocol = parsed
        else:
            return None

        raw_status = fields.get("status")
        if raw_status is None or raw_status == "" or raw_status == "-":
            return None
        status = _to_int(raw_status)
        if not 100 <= status <= 599:
            return None

        request_time = _to_seconds(fields.get("request_time"))
        if request_time is None and fields.get("request_time_us") not in (None, "", "-"):
            micro = _to_seconds(fields.get("request_time_us"))
            request_time = None if micro is None else micro / 1_000_000
        if request_time is None and fields.get("request_time_ms") not in (None, "", "-"):
            milli = _to_seconds(fields.get("request_time_ms"))
            request_time = None if milli is None else milli / 1_000

        return LogEntry(
            ip=_dash_to_empty(fields.get("ip")) or "-",
            timestamp=timestamp,
            method=str(method),
            url=_clean_url(str(url)),
            status=status,
            bytes_sent=max(_to_int(fields.get("bytes")), 0),
            protocol=protocol,
            referer=_dash_to_empty(fields.get("referer")),
            user_agent=_dash_to_empty(fields.get("user_agent")),
            request_time=request_time,
            host=_dash_to_empty(fields.get("host")),
        )
    except (ValueError, TypeError, OverflowError):
        return None


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------


class LineParser:
    """Interface implemented by all line parsers."""

    name: str = "unknown"

    def parse(self, line: str) -> LogEntry | None:  # pragma: no cover - interface
        raise NotImplementedError


class RegexLineParser(LineParser):
    """Parse lines with a compiled regex whose groups use canonical names."""

    def __init__(self, name: str, pattern: re.Pattern[str], source: str = "") -> None:
        self.name = name
        self.pattern = pattern
        self.source = source

    def parse(self, line: str) -> LogEntry | None:
        match = self.pattern.match(line.rstrip("\r\n"))
        if match is None:
            return None
        return entry_from_fields(match.groupdict())

    def __repr__(self) -> str:
        return f"RegexLineParser(name={self.name!r}, pattern={self.pattern.pattern!r})"


#: JSON key aliases for each canonical field, in priority order.
JSON_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "ip": ("remote_addr", "ip", "client_ip", "clientip", "remote_ip", "client", "remote_host"),
    "time": ("time_iso8601", "time_local", "timestamp", "@timestamp", "time", "ts", "msec"),
    "request": ("request", "request_line"),
    "method": ("request_method", "method", "verb", "http_method"),
    "url": ("request_uri", "uri", "url", "path", "request_path"),
    "query": ("args", "query_string", "query"),
    "protocol": ("server_protocol", "protocol", "http_version"),
    "status": ("status", "status_code", "response_code", "response", "http_status"),
    "bytes": ("body_bytes_sent", "bytes_sent", "bytes", "size", "response_size"),
    "referer": ("http_referer", "referer", "referrer"),
    "user_agent": ("http_user_agent", "user_agent", "useragent", "agent", "ua"),
    "request_time": ("request_time", "response_time", "duration", "upstream_response_time"),
    "host": ("host", "http_host", "server_name", "vhost"),
}


def _dig(obj: Any, dotted: str) -> Any:
    """Fetch ``a.b.c`` from nested dicts; a literal dotted key wins."""
    if isinstance(obj, dict) and dotted in obj:
        return obj[dotted]
    current = obj
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class JsonLineParser(LineParser):
    """Parse JSON-lines access logs.

    ``field_map`` maps canonical field names to the JSON key (dotted paths
    allowed) that holds them, overriding the built-in aliases - for example
    ``{"ip": "client.address", "request_time_ms": "took_ms"}``.
    """

    name = "json"

    def __init__(self, field_map: Mapping[str, str] | None = None) -> None:
        self.field_map = dict(field_map or {})
        allowed = set(JSON_FIELD_ALIASES) | {"request_time_us", "request_time_ms"}
        unknown = sorted(set(self.field_map) - allowed)
        if unknown:
            raise LogFormatError(
                f"unknown JSON field mapping(s): {', '.join(unknown)}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )

    def parse(self, line: str) -> LogEntry | None:
        text = line.strip()
        if not text.startswith("{"):
            return None
        try:
            obj = json.loads(text)
        except ValueError:
            return None
        if not isinstance(obj, dict):
            return None
        fields: dict[str, Any] = {}
        for canonical, aliases in JSON_FIELD_ALIASES.items():
            if canonical in self.field_map:
                continue
            for alias in aliases:
                value = obj.get(alias)
                if value is not None and value != "":
                    fields[canonical] = value
                    break
        for canonical, key in self.field_map.items():
            value = _dig(obj, key)
            if value is not None:
                fields[canonical] = value
        # A full request line takes precedence only when no separate URL exists.
        if "url" in fields and "request" in fields:
            del fields["request"]
        return entry_from_fields(fields)


# --------------------------------------------------------------------------
# log_format compilation
# --------------------------------------------------------------------------

_QUOTED = r'(?:[^"\\]|\\.)*'
_BRACKETED = r"[^\]]*"
_BARE = r"\S+"
_CLF_TIME = r"\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?: [+-]\d{4})?"

#: Nginx variable -> canonical field name.
NGINX_VARIABLES: dict[str, str] = {
    "remote_addr": "ip",
    "realip_remote_addr": "ip",
    "time_local": "time",
    "time_iso8601": "time",
    "msec": "time",
    "request": "request",
    "request_method": "method",
    "request_uri": "url",
    "uri": "url",
    "args": "query",
    "query_string": "query",
    "server_protocol": "protocol",
    "status": "status",
    "body_bytes_sent": "bytes",
    "bytes_sent": "bytes",
    "http_referer": "referer",
    "http_user_agent": "user_agent",
    "request_time": "request_time",
    "upstream_response_time": "request_time",
    "host": "host",
    "http_host": "host",
    "server_name": "host",
}

#: Apache directive letter -> canonical field name.
APACHE_DIRECTIVES: dict[str, str] = {
    "h": "ip",
    "a": "ip",
    "t": "time",
    "r": "request",
    "m": "method",
    "U": "url",
    "q": "query",
    "H": "protocol",
    "s": "status",
    "b": "bytes",
    "B": "bytes",
    "O": "bytes",
    "D": "request_time_us",
    "T": "request_time",
    "v": "host",
    "V": "host",
}

#: Apache ``%{Name}i`` request headers -> canonical field name.
APACHE_HEADERS: dict[str, str] = {
    "referer": "referer",
    "user-agent": "user_agent",
    "host": "host",
}

_NGINX_TOKEN_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_APACHE_TOKEN_RE = re.compile(r"%(?:!?\d+(?:,\d+)*)?[<>]?(?:\{([^}]*)\})?([a-zA-Z%])")

_REQUIRED_FIELDS = ("time", "status")
#: Fields that may contain spaces when they are the unquoted last variable.
_FREE_TEXT_FIELDS = frozenset({None, "user_agent", "referer", "request", "url"})


def _value_pattern(canonical: str | None, before: str, after: str, is_last: bool) -> str:
    """Choose the regex for a variable from its surrounding literal text."""
    prev_char = before[-1:] if before else ""
    next_char = after[:1] if after else ""
    if prev_char == '"' and next_char == '"':
        return _QUOTED
    if prev_char == "[" and next_char == "]":
        return _BRACKETED
    if canonical == "status":
        return r"\d{3}|-"
    if canonical == "time":
        return rf"{_CLF_TIME}|\S+"
    if is_last and not after and canonical in _FREE_TEXT_FIELDS:
        return r".*"
    if next_char and not next_char.isspace():
        # Stop at the delimiter that follows, e.g. "$host:$server_port".
        return rf"[^\s{re.escape(next_char)}]+"
    return _BARE


def _detect_style(fmt: str) -> str:
    if _NGINX_TOKEN_RE.search(fmt):
        return "nginx"
    if "%" in fmt:
        return "apache"
    raise LogFormatError(
        "log format contains no variables; expected Nginx ($remote_addr ...) "
        "or Apache (%h %l %u ...) syntax"
    )


def _tokenize(fmt: str, style: str) -> list[tuple[str, str | None]]:
    """Split a format string into ``("lit", text)`` and ``("var", field)`` tokens.

    Variables that the analyser has no use for are returned with ``None`` as
    their canonical field name, so they are matched but not captured.
    """
    tokens: list[tuple[str, str | None]] = []
    pos = 0
    token_re = _NGINX_TOKEN_RE if style == "nginx" else _APACHE_TOKEN_RE
    for match in token_re.finditer(fmt):
        if match.start() > pos:
            tokens.append(("lit", fmt[pos : match.start()]))
        pos = match.end()
        if style == "nginx":
            name = match.group(1) or match.group(2)
            tokens.append(("var", NGINX_VARIABLES.get(name)))
            continue
        argument, letter = match.group(1), match.group(2)
        if letter == "%":
            tokens.append(("lit", "%"))
        elif letter == "i" and argument is not None:
            tokens.append(("var", APACHE_HEADERS.get(argument.lower())))
        elif letter == "t" and argument is None:
            # Apache's %t writes its own surrounding brackets.
            tokens.append(("lit", "["))
            tokens.append(("var", "time"))
            tokens.append(("lit", "]"))
        elif argument is not None and letter in ("t", "T"):
            # %{format}t / %{unit}T: custom representations are matched, not captured,
            # except the two request-time units that map cleanly.
            unit = {"us": "request_time_us", "ms": "request_time_ms", "s": "request_time"}
            tokens.append(("var", unit.get(argument) if letter == "T" else None))
        elif argument is not None:
            tokens.append(("var", None))
        else:
            tokens.append(("var", APACHE_DIRECTIVES.get(letter)))
    if pos < len(fmt):
        tokens.append(("lit", fmt[pos:]))
    # Merge adjacent literals produced by %% and %t expansion.
    merged: list[tuple[str, str | None]] = []
    for kind, value in tokens:
        if kind == "lit" and merged and merged[-1][0] == "lit":
            merged[-1] = ("lit", (merged[-1][1] or "") + (value or ""))
        else:
            merged.append((kind, value))
    return merged


def compile_log_format(
    fmt: str,
    *,
    style: str | None = None,
    name: str = "custom",
    allow_trailing: bool = False,
) -> RegexLineParser:
    """Compile an Nginx ``log_format`` or Apache ``LogFormat`` string.

    ``style`` is ``"nginx"``, ``"apache"`` or ``None`` to auto-detect.  With
    ``allow_trailing`` the resulting regex tolerates extra fields appended
    after the described ones, which is how the predefined formats cope with
    the many "combined plus something" variants in the wild.

    Raises :class:`LogFormatError` if the format lacks the fields every
    analysis needs: a timestamp, a status and either ``$request`` or a URL.
    """
    fmt = fmt.strip()
    if not fmt:
        raise LogFormatError("log format is empty")
    style = style or _detect_style(fmt)
    if style not in ("nginx", "apache"):
        raise LogFormatError(f"unknown log format style: {style!r}")

    tokens = _tokenize(fmt, style)
    captured: set[str] = set()
    parts: list[str] = ["^"]
    for index, (kind, value) in enumerate(tokens):
        if kind == "lit":
            parts.append(re.escape(value or ""))
            continue
        before = tokens[index - 1][1] if index > 0 and tokens[index - 1][0] == "lit" else ""
        has_next = index + 1 < len(tokens)
        after = tokens[index + 1][1] if has_next and tokens[index + 1][0] == "lit" else ""
        if has_next and tokens[index + 1][0] == "var":
            raise LogFormatError(
                "two adjacent variables without a separator cannot be parsed reliably"
            )
        is_last = index == len(tokens) - 1 and not allow_trailing
        pattern = _value_pattern(value, before or "", after or "", is_last)
        if value is None or value in captured:
            parts.append(f"(?:{pattern})")
        else:
            captured.add(value)
            parts.append(f"(?P<{value}>{pattern})")
    parts.append(r"(?:\s.*)?$" if allow_trailing else "$")

    missing = [field for field in _REQUIRED_FIELDS if field not in captured]
    if "request" not in captured and "url" not in captured:
        missing.append("request")
    if missing:
        raise LogFormatError(
            "log format is missing required field(s): "
            + ", ".join(missing)
            + " (need a timestamp, a status and a request line or URL)"
        )
    try:
        regex = re.compile("".join(parts))
    except re.error as exc:  # pragma: no cover - defensive
        raise LogFormatError(f"could not compile log format: {exc}") from exc
    return RegexLineParser(name, regex, source=fmt)


_BUILTIN_CACHE: dict[str, LineParser] = {}


def get_parser(name: str) -> LineParser:
    """Return the predefined parser for a ``--format`` name or alias."""
    canonical = FORMAT_ALIASES.get(name.lower())
    if canonical is None:
        known = ", ".join(sorted(FORMAT_ALIASES))
        raise LogFormatError(f"unknown log format {name!r}; known formats: {known}")
    parser = _BUILTIN_CACHE.get(canonical)
    if parser is None:
        if canonical == "json":
            parser = JsonLineParser()
        elif canonical == "combined":
            parser = compile_log_format(NGINX_COMBINED, name="combined", allow_trailing=True)
        elif canonical == "combined-timed":
            parser = compile_log_format(NGINX_COMBINED_TIMED, name="combined-timed")
        else:
            parser = compile_log_format(APACHE_COMMON, name="common", allow_trailing=True)
        _BUILTIN_CACHE[canonical] = parser
    return parser


#: Candidate order for auto-detection: most specific first, ties keep order.
_DETECTION_ORDER = ("json", "combined-timed", "combined", "common")


def detect_format(lines: Iterable[str]) -> LineParser | None:
    """Pick the predefined parser that understands most of ``lines``.

    Returns ``None`` when no parser handles at least half of the non-blank
    sample lines.
    """
    sample = [line for line in lines if line.strip()]
    if not sample:
        return None
    best: LineParser | None = None
    best_score = 0
    for name in _DETECTION_ORDER:
        parser = get_parser(name)
        score = sum(1 for line in sample if parser.parse(line) is not None)
        if score > best_score:
            best, best_score = parser, score
    if best is None or best_score * 2 < len(sample):
        return None
    return best
