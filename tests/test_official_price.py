import pytest

from housing.errors import DataSourceError
from housing.official_price import OfficialPrice, get_official_price
from tests.test_building import BONGCHEON


def test_missing_vworld_key_raises(monkeypatch):
    monkeypatch.delenv("VWORLD_API_KEY", raising=False)
    with pytest.raises(DataSourceError, match="직접 입력") as e:
        get_official_price(BONGCHEON, "", "201")
    assert "VWORLD_API_KEY" not in str(e.value) and ".env" not in str(e.value)  # 사용자에게 보이는 문구


def test_manual_price_shape():
    p = OfficialPrice(price_won=160_000_000, year=2026, source="사용자 입력")
    assert p.price_won == 160_000_000
