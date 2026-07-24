# Integration contract

Data path: `XtData -> raw -> clean -> trusted -> bt_ready -> Backtrader feed`.

Required feed fields: datetime, symbol, OHLCV, amount, suspended, adjust mode, and optional limits/corporate-action metadata. Required broker outputs: client order id, status, submitted/fill timestamps, requested/filled quantity, price, fee, reject reason, and cash/position snapshots.
