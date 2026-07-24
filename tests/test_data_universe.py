from dyadtetrade.data.universe import instrument_from_xtdata, is_unlaunched_placeholder


def test_qmt_zero_open_date_contract_is_an_unlaunched_placeholder() -> None:
    detail = {
        "InstrumentName": "认购款",
        "OpenDate": "0",
        "CreateDate": "20260701",
        "ExpireDate": 99999999,
        "FloatVolume": 0.0,
        "TotalVolume": 0.0,
    }

    assert is_unlaunched_placeholder(detail)
    instrument = instrument_from_xtdata("158000.SZ", detail, is_current=True, instrument_type="etf")
    assert instrument.list_date == 20260701
    assert instrument.is_active is False


def test_listed_etf_with_real_float_is_not_a_placeholder() -> None:
    detail = {
        "InstrumentName": "沪深300ETF",
        "OpenDate": "20120528",
        "CreateDate": "0",
        "ExpireDate": 99999999,
        "FloatVolume": 19_701_487_700.0,
        "TotalVolume": 19_701_487_700.0,
    }

    assert not is_unlaunched_placeholder(detail)
    instrument = instrument_from_xtdata("510300.SH", detail, is_current=True, instrument_type="etf")
    assert instrument.list_date == 20120528
    assert instrument.is_active is True


def test_cash_index_without_share_capital_is_still_active() -> None:
    detail = {
        "InstrumentName": "上证指数",
        "OpenDate": "0",
        "CreateDate": "0",
        "ExpireDate": 99999999,
        "FloatVolume": 0.0,
        "TotalVolume": 0.0,
    }

    assert is_unlaunched_placeholder(detail)
    instrument = instrument_from_xtdata(
        "000001.SH", detail, is_current=True, instrument_type="index"
    )
    assert instrument.is_active is True
