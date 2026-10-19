# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Log redaction module, shared across the livepatch-server charms.

Provides a logging Filter and Formatter that scrub sensitive information
(passwords, tokens, API keys, connection strings) from log records before
they reach any handler.

The generic patterns (URIs with credentials, Authorization headers,
key=value pairs with secret-sounding keys and sensitive ``LP_*``
environment-variable assignments) are always active. Charms append their own
sensitive assignment patterns via :func:`register_sensitive_pattern`: the
machine charm redacts ``lp.*`` snap-config assignments.

If you add a new sensitive config option, add the corresponding ``LP_*``
env-var name to ``_SENSITIVE_ENV_VAR_PATTERN`` (and, for the machine charm,
the ``lp.*`` snap-config key to its charm-specific pattern).

To activate redaction, call ``setup_log_redaction()`` once early in the
charm's ``__init__`` method, after ``super().__init__()``.
"""

import logging
import re
from typing import Callable, Optional, Pattern

_REDACTED = "***REDACTED***"

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# URIs with embedded credentials: scheme://user:password@host/...
# Replaces everything after the scheme (userinfo + host + path).
_URI_PATTERN = re.compile(
    r"(?P<scheme>\w[\w+\-.]*://)"  # e.g. postgresql://
    r"[^:@\s]+:[^@\s]+"  # user:password  (redacted)
    r"@\S*",  # @host/db...    (redacted)
    re.IGNORECASE,
)

# HTTP Authorization header values: Bearer/Basic <credential>
_AUTH_HEADER_PATTERN = re.compile(
    r"(?P<scheme>(?:Bearer|Basic)\s+)\S+",
    re.IGNORECASE,
)

# Generic key=value / key: value pairs where the key name implies a secret.
# Uses \b word boundaries to avoid false positives (e.g. "token_count=5").
_KV_PATTERN = re.compile(
    r"(?P<key>\b(?:"
    r"password|passwd|secret|token|"
    r"api[_\-]?key|api[_\-]?secret|"
    r"access[_\-]?key|private[_\-]?key|auth[_\-]?key|"
    r"credentials|host"
    r")\b)"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?:"
    # Backslash-escaped characters (including the quote itself) are consumed
    # as part of the value so an escaped quote cannot end the match early.
    r"(?P<q>['\"])(?P<qv>(?:\\.|(?!(?P=q))[^\\])*)(?P=q)"
    r"|"
    # An unterminated opening quote must not stop the unquoted value matching.
    r"['\"]?(?P<uv>[^\s'\"]+)"
    r")",
    re.IGNORECASE,
)

# Sensitive LP_* env-var assignments, e.g. the k8s charm's Pebble environment
# (map_config_to_env_vars()/get_env_vars()) or snap config rendered as env vars.
_SENSITIVE_ENV_VAR_PATTERN = re.compile(
    r"(?P<varname>"
    r"LP_CONTRACTS_PASSWORD"
    r"|LP_PATCH_SYNC_TOKEN"
    r"|LP_PATCH_STORAGE_S3_SECRET_KEY"
    r"|LP_PATCH_STORAGE_S3_ACCESS_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_API_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_TEMP_URL_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_USERNAME"
    r"|LP_PATCH_STORAGE_AZURE_ACCOUNT_KEY"
    r"|LP_PATCH_STORAGE_AZURE_CONNECTION_STRING"
    r"|LP_PATCH_STORAGE_AZURE_CLIENT_SECRET"
    r"|LP_PATCH_STORAGE_GCS_CREDENTIALS_JSON"
    r"|LP_PATCH_STORAGE_IBM_ACCESS_KEY"
    r"|LP_PATCH_STORAGE_IBM_SECRET_KEY"
    r"|LP_PATCH_STORAGE_IBM_API_KEY"
    r"|LP_AUTH_BASIC_USERS"
    r"|LP_APP_SECRET_KEY"
    r"|LP_INFLUX_TOKEN"
    r"|LP_PATCH_STORAGE_POSTGRES_CONNECTION_STRING"
    r"|LP_DATABASE_CONNECTION_STRING"
    r")"
    r"(?P<sep>=)"
    # Non-greedy, spanning newlines (DOTALL): stop only before a run of trailing
    # whitespace at end of string, or right before what looks like a genuine
    # subsequent KEY=value assignment (env var names always contain an
    # underscore, unlike base64/PEM content lines, which can otherwise be
    # mistaken for one), so multi-line secrets (PEM keys, pretty-printed JSON)
    # are fully redacted without leaking a suffix or absorbing trailing whitespace.
    r"(?P<value>.+?)(?=\s+[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+=|\s*$)",
    re.IGNORECASE | re.DOTALL,
)


def _kv_replace(m: re.Match) -> str:
    key = m.group("key")
    sep = m.group("sep")
    q = m.group("q")
    if q:
        return f"{key}{sep}{q}{_REDACTED}{q}"
    return f"{key}{sep}{_REDACTED}"


_PATTERNS = [
    (
        _URI_PATTERN,
        lambda m: f"{m.group('scheme')}{_REDACTED}",
    ),
    (
        _AUTH_HEADER_PATTERN,
        lambda m: f"{m.group('scheme')}{_REDACTED}",
    ),
    (
        _KV_PATTERN,
        _kv_replace,
    ),
    (
        _SENSITIVE_ENV_VAR_PATTERN,
        lambda m: f"{m.group('varname')}{m.group('sep')}{_REDACTED}",
    ),
]


def register_sensitive_pattern(
    pattern: Pattern[str],
    replacement: Callable[[re.Match], str],
) -> None:
    """Register a charm-specific sensitive (pattern, replacement) pair."""
    _PATTERNS.append((pattern, replacement))


def _redact(msg: str) -> str:
    """Apply all redaction patterns to *msg* and return the sanitised result."""
    for pattern, replacement in _PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg


def _redact_if_str(value: object) -> object:
    """Redact *value* if it is a string; return it unchanged otherwise."""
    return _redact(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class RedactingFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log records in-place.

    Handles both pre formatted log messages (record.msg) and structured log
    arguments. When ``record.args`` is present the filter avoids redacting
    ``record.msg`` itself so ``%s``/``%(key)s`` placeholders are preserved
    for ``LogRecord.getMessage()``; the final rendered message is instead
    redacted via the formatter / LogRecord factory.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitise *record* in-place; never suppresses the record."""
        if not record.args:
            record.msg = _redact(str(record.msg))
        if isinstance(record.args, dict):
            record.args = {k: _redact_if_str(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_redact_if_str(arg) for arg in record.args)
        if record.exc_info and record.exc_info[0] is not None:
            try:
                record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
            except Exception:
                pass
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
        return True


_orig_log_record_factory = None


def _install_redacting_log_record_factory() -> None:
    global _orig_log_record_factory
    if _orig_log_record_factory is not None:
        return
    _orig_log_record_factory = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = _orig_log_record_factory(*args, **kwargs)
        orig_getMessage = record.getMessage

        def _redacted_getMessage():
            return _redact(orig_getMessage())

        record.getMessage = _redacted_getMessage  # type: ignore[method-assign]
        if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
            try:
                record.exc_text = _redact(logging.Formatter().formatException(record.exc_info))
            except Exception:
                pass
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
        return record

    logging.setLogRecordFactory(_factory)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


class RedactingFormatter(logging.Formatter):
    """Formatter that delegates to a wrapped formatter then redacts the output.

    Acts as a last line of defence: catches sensitive data not intercepted by
    the filter — for example, values embedded in exception tracebacks or
    messages from loggers that bypass the root-logger filter chain.
    """

    def __init__(self, wrapped: Optional[logging.Formatter] = None) -> None:
        """Init function. If *wrapped* is None, uses a default logging.Formatter."""
        super().__init__()
        self._wrapped = wrapped if wrapped is not None else logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        """Override format to redact the formatted log message."""
        return _redact(self._wrapped.format(record))

    def formatException(self, ei) -> str:  # noqa: N802
        """Override formatException to redact the formatted exception."""
        return _redact(self._wrapped.formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802
        """Override formatStack to redact the formatted stack info."""
        return _redact(self._wrapped.formatStack(stack_info))


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


def setup_log_redaction() -> None:
    """Attach redaction to the root logger.

    Must be called after ops has initialised the root logger — i.e. after
    ``super().__init__()`` inside the charm's ``__init__`` method.

    Effects:
    - A ``RedactingFilter`` is registered on the root logger, redacting records
      logged directly on it. Logger filters are not applied to records
      propagated from child loggers, so this alone does not cover them.
    - Each existing handler on the root logger has its formatter wrapped with
      ``RedactingFormatter`` as a safety net.
    - A redacting :func:`logging.getLogRecordFactory` is installed so every
      subsequent :class:`logging.LogRecord` has its rendered message,
      exception and stack redacted at creation. This is what covers records
      from child loggers, including those with their own handlers or with
      ``propagate=False``.
    """
    root = logging.getLogger()

    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())

    for handler in root.handlers:
        if not isinstance(handler.formatter, RedactingFormatter):
            handler.setFormatter(RedactingFormatter(handler.formatter))

    _install_redacting_log_record_factory()
