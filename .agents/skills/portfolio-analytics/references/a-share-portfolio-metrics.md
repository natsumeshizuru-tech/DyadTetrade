# A-share Portfolio Metrics

- Use the actual exchange trading calendar or an observed-session count; document the annualization factor.
- Compute drawdown from the high-water mark and report both depth and duration.
- Separate gross return, explicit fees, taxes, slippage, impact, and net return.
- Align CSI 300 or another configured benchmark only on comparable trading dates.
- Treat intraday and daily series separately; never mix their annualization conventions.
- Report missing data, stale marks, suspended holdings, and delisted securities as limitations.

Validation requires recomputing total return from first and last equity, reconciling P&L to positions and cash, and reproducing results from the saved inputs.
