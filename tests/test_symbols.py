import pytest

from dyadtetrade.core.symbols import infer_asset_type, is_a_share_symbol, normalize_symbol


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("000001.sz", "000001.SZ"),
        ("430047", "430047.BJ"),
    ],
)
def test_normalize_symbol(raw: str, expected: str) -> None:
    assert normalize_symbol(raw) == expected


def test_a_share_filter_excludes_etf() -> None:
    assert is_a_share_symbol("600519.SH")
    assert not is_a_share_symbol("510300.SH")


@pytest.mark.parametrize(
    ("symbol", "asset_type"),
    [("600519.SH", "stock"), ("510300.SH", "etf"), ("000300.SH", "index")],
)
def test_asset_type_inference(symbol: str, asset_type: str) -> None:
    assert infer_asset_type(symbol) == asset_type
