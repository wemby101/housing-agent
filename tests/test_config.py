import pytest

from housing.config import get_key
from housing.errors import DataSourceError


def test_get_key_reads_env(monkeypatch):
    monkeypatch.setenv("JUSO_API_KEY", "abc")
    assert get_key("JUSO_API_KEY") == "abc"


def test_get_key_missing_raises(monkeypatch):
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)
    with pytest.raises(DataSourceError, match="VWORLD_API_KEY"):
        get_key("VWORLD_API_KEY")


def test_get_key_empty_counts_as_missing(monkeypatch):
    monkeypatch.setenv("VWORLD_API_KEY", "")
    with pytest.raises(DataSourceError):
        get_key("VWORLD_API_KEY")
