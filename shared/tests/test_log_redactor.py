# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import io
import logging

import pytest

from livepatch_common.log_redactor import _REDACTED, _redact, setup_log_redaction

SECRET = "s3cr3t"


@pytest.mark.parametrize(
    "message, expected",
    [
        ('password="my s3cr3t value" next=1', f'password="{_REDACTED}" next=1'),
        ("token='my s3cr3t value' next=1", f"token='{_REDACTED}' next=1"),
        ('password="abc\\"s3cr3t" next=1', f'password="{_REDACTED}" next=1'),
        ("token='it\\'s-s3cr3t' next=1", f"token='{_REDACTED}' next=1"),
        ('password="abc\\\\" next=s3cr3t', f'password="{_REDACTED}" next=s3cr3t'),
        ('password="s3cr3t', f"password={_REDACTED}"),
        ("password=s3cr3t other", f"password={_REDACTED} other"),
        ('password=""', f'password="{_REDACTED}"'),
    ],
)
def test_kv_redaction(message, expected):
    assert _redact(message) == expected


def test_env_var_redaction_is_shared():
    assert SECRET not in _redact(f"LP_CONTRACTS_PASSWORD={SECRET} LP_OTHER_VAR=1")


@pytest.fixture
def child_logger():
    logger = logging.getLogger("livepatch_common.tests.child")
    logger.propagate = False
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    setup_log_redaction()
    yield logger, stream
    logger.removeHandler(handler)
    logger.propagate = True


def test_child_logger_with_own_handler_is_redacted(child_logger):
    logger, stream = child_logger
    logger.warning("dsn postgresql://user:%s@host/db token=%s", SECRET, SECRET)
    assert SECRET not in stream.getvalue()


def test_child_logger_exception_is_redacted(child_logger):
    logger, stream = child_logger
    try:
        raise RuntimeError(f"password={SECRET}")
    except RuntimeError:
        logger.exception("failed")
    assert SECRET not in stream.getvalue()
    assert "RuntimeError" in stream.getvalue()


def test_percent_style_args_are_preserved(child_logger):
    logger, stream = child_logger
    logger.warning("unit %s is %s", "livepatch/0", "ready")
    assert "unit livepatch/0 is ready" in stream.getvalue()
