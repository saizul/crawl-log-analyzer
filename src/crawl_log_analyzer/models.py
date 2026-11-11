"""Core data model shared by parsers and analyses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class LogEntry:
    """One parsed access-log request.

    ``url`` is the request target exactly as the client sent it (path plus
    optional query string).  Absolute-form targets (``GET http://host/path``)
    are reduced to the path by the parsers so that analyses can treat every
    entry the same way.
    """

    ip: str
    timestamp: datetime
    method: str
    url: str
    status: int
    bytes_sent: int = 0
    protocol: str = ""
    referer: str = ""
    user_agent: str = ""
    request_time: float | None = None
    host: str = ""

    @property
    def path(self) -> str:
        """The URL without query string or fragment."""
        url = self.url
        cut = url.find("?")
        if cut != -1:
            url = url[:cut]
        cut = url.find("#")
        if cut != -1:
            url = url[:cut]
        return url

    @property
    def query(self) -> str:
        """The raw query string (without the leading ``?``), or ``""``."""
        cut = self.url.find("?")
        if cut == -1:
            return ""
        query = self.url[cut + 1 :]
        frag = query.find("#")
        return query if frag == -1 else query[:frag]

    @property
    def status_class(self) -> str:
        """Status class label such as ``"2xx"`` or ``"4xx"``."""
        return f"{self.status // 100}xx"
