"""Smoke test untuk config.py."""

import pytest

import config


def test_parse_id_list_valid():
    assert config._parse_id_list(" 123, 456 ,,") == frozenset({123, 456})


def test_parse_id_list_empty():
    assert config._parse_id_list("") == frozenset()


def test_parse_id_list_invalid():
    with pytest.raises(ValueError):
        config._parse_id_list("123,abc")


def test_constants():
    assert config.TIMEZONE_NAME == "Asia/Jakarta"
    assert config.WEEK_START == 0
    assert config.BUDGET_WARNING_PERCENT < config.BUDGET_EXCEEDED_PERCENT
