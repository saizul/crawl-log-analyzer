"""TOML configuration: custom bot patterns, waste rules and defaults.

Example::

    [[bots]]
    name = "InternalCrawler"
    pattern = "acme-crawler/\\\\d"
    category = "monitoring"

    [waste]
    extra_facet_params = ["fabric", "fit"]

    [[waste.rules]]
    name = "calendar-trap"
    pattern = "^/events/\\\\d{4}/\\\\d{2}/"

Every key is validated; unknown keys are rejected so that typos do not pass
silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .bots import CATEGORIES, OTHER, BotClassifier, BotSignature
from .parsers import (
    FORMAT_ALIASES,
    JsonLineParser,
    LineParser,
    LogFormatError,
    compile_log_format,
    get_parser,
)
from .waste import (
    DEFAULT_FACET_PARAMS,
    DEFAULT_SEARCH_PARAMS,
    DEFAULT_STATIC_EXTENSIONS,
    DEFAULT_TRACKING_PARAMS,
    WasteRules,
)

__all__ = ["Config", "ConfigError", "load_config", "parse_config"]


class ConfigError(ValueError):
    """Raised for an unreadable or invalid configuration file."""


@dataclass
class Config:
    """Validated configuration.  Unset values are ``None`` so the command
    line can tell "not configured" from an explicit default."""

    bots: list[BotSignature] = field(default_factory=list)
    waste_rules: WasteRules = field(default_factory=WasteRules)
    top: int | None = None
    directory_depth: int | None = None
    anomaly_threshold: float | None = None
    log_format_name: str | None = None
    log_format: str | None = None
    json_fields: dict[str, str] = field(default_factory=dict)
    source: str | None = None

    def classifier(self) -> BotClassifier:
        """A classifier in which the configured bots take precedence."""
        base = BotClassifier()
        return base.with_extra(self.bots) if self.bots else base

    def parser(self) -> LineParser | None:
        """The configured line parser, or ``None`` for auto-detection."""
        if self.log_format:
            return compile_log_format(self.log_format)
        if self.json_fields:
            return JsonLineParser(self.json_fields)
        if self.log_format_name:
            return get_parser(self.log_format_name)
        return None


def _load_toml(path: str) -> dict[str, Any]:
    try:
        import tomllib as toml_reader
    except ModuleNotFoundError:  # Python 3.10
        try:
            import tomli as toml_reader  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "reading TOML config files on Python 3.10 requires the 'tomli' package "
                "(pip install tomli)"
            ) from exc
    try:
        with open(path, "rb") as handle:
            return toml_reader.load(handle)
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror or exc}") from exc
    except toml_reader.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(
            f"unknown key(s) in {where}: {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table")
    return value


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    return value


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{where} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _regex(value: Any, where: str) -> re.Pattern[str]:
    text = _string(value, where)
    try:
        return re.compile(text, re.IGNORECASE)
    except re.error as exc:
        raise ConfigError(f"{where} is not a valid regular expression: {exc}") from exc


def _integer(value: Any, where: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be an integer")
    if value < minimum:
        raise ConfigError(f"{where} must be at least {minimum}")
    return value


def _parse_bots(raw: Any) -> list[BotSignature]:
    if not isinstance(raw, list):
        raise ConfigError("[[bots]] must be an array of tables")
    signatures = []
    for index, item in enumerate(raw, start=1):
        where = f"bots[{index}]"
        table = _table(item, where)
        _check_keys(
            table, {"name", "pattern", "family", "category", "requires", "dns_suffixes"}, where
        )
        if "name" not in table or "pattern" not in table:
            raise ConfigError(f"{where} needs both 'name' and 'pattern'")
        name = _string(table["name"], f"{where}.name")
        category = str(table.get("category", OTHER)).lower()
        if category not in CATEGORIES:
            raise ConfigError(
                f"{where}.category must be one of {', '.join(CATEGORIES)} (got {category!r})"
            )
        suffixes = _string_list(table.get("dns_suffixes", []), f"{where}.dns_suffixes")
        signatures.append(
            BotSignature(
                name=name,
                family=_string(table.get("family", name), f"{where}.family"),
                category=category,
                pattern=_regex(table["pattern"], f"{where}.pattern"),
                requires=(
                    _regex(table["requires"], f"{where}.requires") if "requires" in table else None
                ),
                dns_suffixes=tuple(
                    suffix if suffix.startswith(".") else f".{suffix}" for suffix in suffixes
                ),
            )
        )
    return signatures


_PARAM_SETS = {
    "tracking_params": DEFAULT_TRACKING_PARAMS,
    "facet_params": DEFAULT_FACET_PARAMS,
    "search_params": DEFAULT_SEARCH_PARAMS,
}


def _parse_waste(raw: Any) -> WasteRules:
    table = _table(raw, "[waste]")
    allowed = {"ignore_params", "static_extensions", "flag_static_assets", "rules"}
    for key in _PARAM_SETS:
        allowed.update({key, f"extra_{key}"})
    _check_keys(table, allowed, "[waste]")

    values: dict[str, Any] = {}
    for key, default in _PARAM_SETS.items():
        if key in table and f"extra_{key}" in table:
            raise ConfigError(f"[waste] sets both {key} and extra_{key}; use only one")
        if key in table:
            names = _string_list(table[key], f"waste.{key}")
            values[key] = frozenset(name.lower() for name in names)
        elif f"extra_{key}" in table:
            names = _string_list(table[f"extra_{key}"], f"waste.extra_{key}")
            values[key] = default | frozenset(name.lower() for name in names)
    if "ignore_params" in table:
        names = _string_list(table["ignore_params"], "waste.ignore_params")
        values["ignore_params"] = frozenset(name.lower() for name in names)
    if "static_extensions" in table:
        names = _string_list(table["static_extensions"], "waste.static_extensions")
        values["static_extensions"] = frozenset(
            name.lower() if name.startswith(".") else f".{name.lower()}" for name in names
        )
    else:
        values["static_extensions"] = DEFAULT_STATIC_EXTENSIONS
    if "flag_static_assets" in table:
        if not isinstance(table["flag_static_assets"], bool):
            raise ConfigError("waste.flag_static_assets must be true or false")
        values["flag_static_assets"] = table["flag_static_assets"]

    rules: list[tuple[str, re.Pattern[str]]] = []
    raw_rules = table.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ConfigError("[[waste.rules]] must be an array of tables")
    seen: set[str] = set()
    for index, item in enumerate(raw_rules, start=1):
        where = f"waste.rules[{index}]"
        rule = _table(item, where)
        _check_keys(rule, {"name", "pattern"}, where)
        if "name" not in rule or "pattern" not in rule:
            raise ConfigError(f"{where} needs both 'name' and 'pattern'")
        name = _string(rule["name"], f"{where}.name").strip()
        if name in seen:
            raise ConfigError(f"{where}.name {name!r} is used more than once")
        seen.add(name)
        rules.append((name, _regex(rule["pattern"], f"{where}.pattern")))
    return WasteRules(custom_rules=rules, **values)


def parse_config(raw: dict[str, Any], source: str | None = None) -> Config:
    """Validate an already-decoded configuration mapping."""
    _check_keys(raw, {"bots", "waste", "analysis", "input"}, "the config file")
    config = Config(source=source)
    if "bots" in raw:
        config.bots = _parse_bots(raw["bots"])
    if "waste" in raw:
        config.waste_rules = _parse_waste(raw["waste"])

    analysis = _table(raw.get("analysis", {}), "[analysis]")
    _check_keys(analysis, {"top", "directory_depth", "anomaly_threshold"}, "[analysis]")
    if "top" in analysis:
        config.top = _integer(analysis["top"], "analysis.top")
    if "directory_depth" in analysis:
        config.directory_depth = _integer(analysis["directory_depth"], "analysis.directory_depth")
    if "anomaly_threshold" in analysis:
        threshold = analysis["anomaly_threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ConfigError("analysis.anomaly_threshold must be a number")
        if threshold <= 0:
            raise ConfigError("analysis.anomaly_threshold must be positive")
        config.anomaly_threshold = float(threshold)

    input_table = _table(raw.get("input", {}), "[input]")
    _check_keys(input_table, {"format", "log_format", "json_fields"}, "[input]")
    if "format" in input_table and "log_format" in input_table:
        raise ConfigError("[input] sets both format and log_format; use only one")
    try:
        if "format" in input_table:
            config.log_format_name = _string(input_table["format"], "input.format")
            get_parser(config.log_format_name)
        if "log_format" in input_table:
            config.log_format = _string(input_table["log_format"], "input.log_format")
            compile_log_format(config.log_format)
        if "json_fields" in input_table:
            fields = _table(input_table["json_fields"], "[input.json_fields]")
            if not all(isinstance(value, str) for value in fields.values()):
                raise ConfigError("[input.json_fields] values must be strings")
            JsonLineParser(fields)
            config.json_fields = dict(fields)
    except LogFormatError as exc:
        raise ConfigError(f"[input]: {exc}") from exc
    named = config.log_format_name
    not_json = named is not None and FORMAT_ALIASES.get(named.lower()) != "json"
    if config.json_fields and (config.log_format or not_json):
        raise ConfigError("[input.json_fields] only applies to the json format")
    return config


def load_config(path: str) -> Config:
    """Read and validate a TOML configuration file."""
    raw = _load_toml(path)
    try:
        return parse_config(raw, source=path)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None
