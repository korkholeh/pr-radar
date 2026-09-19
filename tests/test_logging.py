import logging

import pytest

from config.logging_filters import SecretMaskingFilter
from config.security import mask_secrets

_FAKE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _fake_token_body(length: int) -> str:
    """A deterministic, obviously-synthetic body assembled at runtime rather than a literal, so
    the source file never contains a contiguous token-shaped substring for a secret scanner to
    flag (round 1 review NIT: these tests were held back from every commit for exactly that
    reason)."""
    return "".join(_FAKE_ALPHABET[i % len(_FAKE_ALPHABET)] for i in range(length))


TOKEN_SAMPLES = [
    "ghp_" + _fake_token_body(36),
    "gho_" + _fake_token_body(36),
    "ghu_" + _fake_token_body(36),
    "ghs_" + _fake_token_body(36),
    "ghr_" + _fake_token_body(36),
    "github_pat_" + _fake_token_body(74),
]


@pytest.mark.parametrize("token", TOKEN_SAMPLES)
def test_mask_secrets_masks_each_token_shape_and_keeps_last_four(token):
    masked = mask_secrets(f"using token {token} to sync")
    assert token not in masked
    assert masked.endswith(token[-4:] + " to sync")
    assert "*" in masked


def test_mask_secrets_leaves_normal_text_untouched():
    text = "a perfectly normal log line with no secrets"
    assert mask_secrets(text) == text


def _make_record(msg, args=None, exc_info=None):
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


@pytest.mark.parametrize("token", TOKEN_SAMPLES)
def test_filter_masks_record_msg(token):
    record = _make_record(f"token is {token}")
    assert SecretMaskingFilter().filter(record) is True
    assert token not in record.msg
    assert record.msg.endswith(token[-4:])


@pytest.mark.parametrize("token", TOKEN_SAMPLES)
def test_filter_masks_percent_style_args(token):
    record = _make_record("token is %s", args=(token,))
    SecretMaskingFilter().filter(record)
    assert token not in record.args[0]
    assert record.args[0].endswith(token[-4:])


@pytest.mark.parametrize("token", TOKEN_SAMPLES)
def test_filter_masks_exception_traceback(token):
    try:
        raise ValueError(f"failed with {token}")
    except ValueError:
        import sys

        record = _make_record("sync failed", exc_info=sys.exc_info())
    SecretMaskingFilter().filter(record)
    assert token not in record.exc_text
    assert record.exc_text.endswith(token[-4:])
