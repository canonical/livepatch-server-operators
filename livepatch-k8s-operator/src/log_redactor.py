# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm-specific wiring for the shared log redaction module.

The k8s charm runs as a 12-factor application, so sensitive values appear in
``LP_*`` environment-variable assignments (see map_config_to_env_vars() and
get_env_vars() in charm.py). Those are covered by the generic redaction
patterns provided by ``livepatch_common.log_redactor``, so no charm-specific
patterns are registered here.
"""

from livepatch_common.log_redactor import (
    RedactingFilter,
    RedactingFormatter,
    _REDACTED,
    _redact,
    _redact_if_str,
    register_sensitive_pattern,
    setup_log_redaction,
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
