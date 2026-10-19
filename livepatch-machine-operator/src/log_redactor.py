# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific wiring for the shared log redaction module.

The machine charm drives the livepatch snap, so sensitive values appear as
``lp.*`` snap-config assignments embedded verbatim in ``SnapError`` messages
when a ``snap set`` operation fails (e.g. ``lp.contracts.password=...``,
``lp.patch-sync.token=...``). Those are registered here on top of the generic
redaction patterns provided by ``livepatch_common.log_redactor``.
"""

import re

from livepatch_common.log_redactor import (
    RedactingFilter,
    RedactingFormatter,
    _REDACTED,
    _redact,
    _redact_if_str,
    register_sensitive_pattern,
    setup_log_redaction,
)

_SENSITIVE_SNAP_CONFIG_PATTERN = re.compile(
    r"(?P<key>\blp\.(?:"
    r"auth\.basic\.users"
    r"|database\.connection-string"
    r"|contracts\.password"
    r"|patch-storage\.postgres-connection-string"
    r"|patch-sync\.token"
    r"|influx\.token"
    r"|patch-storage\.swift-api-key"
    r"|patch-storage\.swift-temp-url-key"
    r"|patch-storage\.swift-username"
    r"|patch-storage\.s3-access-key"
    r"|patch-storage\.s3-secret-key"
    r"|patch-storage\.gcs-credentials-json(?:-file)?"
    r"|patch-storage\.azure-account-key"
    r"|patch-storage\.azure-connection-string"
    r"|patch-storage\.azure-client-secret"
    r"|patch-storage\.ibm-api-key"
    r")\b)"
    r"(?P<sep>=)"
    # Non-greedy, spanning newlines (DOTALL): stop only before a run of trailing
    # whitespace at end of string, or right before what looks like a genuine
    # subsequent key=value assignment (keys always contain a dot), so multi-line
    # secrets (PEM keys, pretty-printed JSON) are fully redacted.
    r"(?P<value>.+?)(?=\s+lp\.[\w.\-]+=|\s*$)",
    re.IGNORECASE | re.DOTALL,
)

register_sensitive_pattern(
    _SENSITIVE_SNAP_CONFIG_PATTERN,
    lambda m: f"{m.group('key')}{m.group('sep')}{_REDACTED}",
)

__all__ = [
    "RedactingFilter",
    "RedactingFormatter",
    "_REDACTED",
    "_redact",
    "_redact_if_str",
    "register_sensitive_pattern",
    "setup_log_redaction",
]