# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trusted CA certificate handling shared by the livepatch charms."""

import logging
from base64 import b64decode
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

TRUSTED_CA_FILENAME = "/usr/local/share/ca-certificates/trusted-contracts.ca.crt"


def decode_contracts_ca(encoded: Optional[str]) -> Tuple[Optional[str], bool]:
    """Decode the base64-encoded contracts CA configuration option.

    A malformed value is logged and ignored rather than blocking the charm,
    which preserves the behaviour of earlier charm revisions. Whitespace is
    ignored so line-wrapped ``base64`` output is accepted, but any other
    non-alphabet character makes the value malformed.

    Args:
        encoded: the raw base64 configuration value, if set.

    Returns:
        The decoded PEM text and whether a configured value was undecodable.
    """
    if not encoded:
        return None, False
    try:
        return b64decode("".join(encoded.split()), validate=True).decode("utf8"), False
    except Exception:  # noqa: B902 - any decoding failure is treated the same
        logger.error("failed to parse base64 value of the contracts CA config option")
        return None, True