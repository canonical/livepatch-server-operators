# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific wiring for the shared log redaction module.

The k8s charm runs as a 12-factor application, so sensitive values appear in
``LP_*`` environment-variable assignments (see map_config_to_env_vars() and
get_env_vars() in charm.py). Those are registered here on top of the generic
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

_SENSITIVE_ENV_VAR_PATTERN = re.compile(
    r"(?P<varname>"
    r"LP_CONTRACTS_PASSWORD"
    r"|LP_PATCH_SYNC_TOKEN"
    r"|LP_PATCH_STORAGE_S3_SECRET_KEY"
    r"|LP_PATCH_STORAGE_S3_ACCESS_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_API_KEY"
    r"|LP_PATCH_STORAGE_SWIFT_USERNAME"
    r"|LP_PATCH_STORAGE_AZURE_ACCOUNT_KEY"
    r"|LP_PATCH_STORAGE_AZURE_CONNECTION_STRING"
    r"|LP_PATCH_STORAGE_AZURE_CLIENT_SECRET"
    r"|LP_PATCH_STORAGE_GCS_CREDENTIALS_JSON"
    r"|LP_PATCH_STORAGE_IBM_ACCESS_KEY"
    r"|LP_PATCH_STORAGE_IBM_SECRET_KEY"
    r"|LP_PATCH_STORAGE_IBM_API_KEY"
    r"|LP_AUTH_BASIC_USERS"
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

register_sensitive_pattern(
    _SENSITIVE_ENV_VAR_PATTERN,
    lambda m: f"{m.group('varname')}{m.group('sep')}{_REDACTED}",
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