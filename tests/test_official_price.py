import pytest

from housing.errors import DataSourceError
from housing.official_price import OfficialPrice, get_official_price
from tests.test_building import BONGCHEON


def test_missing_vworld_key_raises(monkeypatch):
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)
    with pytest.raises(DataSourceError, match="VWORLD_API_KEY"):
        get_official_price(BONGCHEON, "", "201")


def test_manual_price_shape():
    p = OfficialPrice(price_won=160_000_000, year=2026, source="사용자 입력")
    assert p.price_won == 160_000_000
