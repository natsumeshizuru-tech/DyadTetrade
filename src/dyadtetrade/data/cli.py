from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dyadtetrade.data.backtest.engine import QmtBacktestEngine
from dyadtetrade.data.backtest.examples import DoubleMovingAverageStrategy
from dyadtetrade.data.compat.xtdata_compat import LocalXtData
from dyadtetrade.data.config import load_data_config
from dyadtetrade.data.downloader import BackfillRunner, summary_dict
from dyadtetrade.data.financial import FinancialBackfillRunner
from dyadtetrade.data.financial import summary_dict as financial_summary_dict
from dyadtetrade.data.jobs.eod_sync import EodSyncJob
from dyadtetrade.data.jobs.validate import validation_payload
from dyadtetrade.data.qmt_dat_import import QmtDatImportRunner
from dyadtetrade.data.qmt_dat_import import summary_dict as dat_summary_dict
from dyadtetrade.data.reference_data import ReferenceDataSyncRunner
from dyadtetrade.data.reference_data import summary_dict as reference_summary_dict
from dyadtetrade.data.sqlite import BarRepository, CatalogRepository
from dyadtetrade.data.sqlite.archive import MarketDataArchive
from dyadtetrade.data.xtdata_gateway import build_xtdata_gateway


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DyadTetrade SQLite market-data platform")
    parser.add_argument("--config", type=Path, default=Path("config/market_data.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("init-db")
    commands.add_parser("eod-sync")
    validate = commands.add_parser("validate")
    validate.add_argument("--full", action="store_true")
    validate.add_argument("--output", type=Path)
    validate.add_argument("--baseline", type=Path)
    validate.add_argument("--changed", default="")
    backfill = commands.add_parser("backfill")
    backfill.add_argument("--start", required=True)
    backfill.add_argument("--end", required=True)
    backfill.add_argument("--periods", default="1d,1m")
    backfill.add_argument("--sector", action="append", default=[])
    backfill.add_argument("--include-delisted", action="store_true")
    backfill.add_argument("--symbols", default="")
    import_dat = commands.add_parser("import-qmt-dat")
    import_dat.add_argument("--data-dir", type=Path)
    import_dat.add_argument("--start", required=True)
    import_dat.add_argument("--end", required=True)
    import_dat.add_argument("--markets", default="SH,SZ,BJ")
    import_dat.add_argument("--instrument-types", default="stock,etf")
    import_dat.add_argument("--symbols", default="")
    import_dat.add_argument("--source", default="qmt_big_local_dat_v1")
    refresh_catalog = commands.add_parser("refresh-catalog")
    refresh_catalog.add_argument("--start", required=True)
    refresh_catalog.add_argument("--end", required=True)
    refresh_catalog.add_argument("--sector", action="append", default=[])
    refresh_catalog.add_argument("--include-delisted", action="store_true")
    financial = commands.add_parser("backfill-financial")
    financial.add_argument("--start", required=True)
    financial.add_argument("--end", required=True)
    financial.add_argument("--symbols", default="")
    financial.add_argument("--resume", action="store_true")
    reference = commands.add_parser("sync-reference-data")
    reference.add_argument("--snapshot-date", required=True)
    reference.add_argument("--resume", action="store_true")
    query = commands.add_parser("query")
    query.add_argument("--symbols", required=True)
    query.add_argument("--period", default="1d")
    query.add_argument("--start", required=True)
    query.add_argument("--end", required=True)
    query.add_argument(
        "--adjust",
        choices=("none", "front", "back", "front_ratio", "back_ratio"),
        default="none",
    )
    query.add_argument("--count", type=int, default=-1)
    backtest = commands.add_parser("backtest-double-ma")
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--period", default="1d")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.add_argument("--fast", type=int, default=5)
    backtest.add_argument("--slow", type=int, default=20)
    backtest.add_argument("--quantity", type=int, default=100)
    archive = commands.add_parser("archive")
    archive_commands = archive.add_subparsers(dest="archive_command", required=True)
    verify = archive_commands.add_parser("verify")
    verify.add_argument("--path", type=Path)
    seal = archive_commands.add_parser("seal")
    seal.add_argument("--path", type=Path, required=True)
    seal.add_argument("--vacuum", action="store_true")
    restore = archive_commands.add_parser("restore")
    restore.add_argument("--path", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_data_config(args.config)
    if args.command == "init-db":
        catalog = CatalogRepository(settings.storage)
        bars = BarRepository(settings.storage)
        print(json.dumps({"catalog": str(catalog.initialize())}, ensure_ascii=False))
        bars.initialize()
        return 0
    if args.command == "doctor":
        gateway = build_xtdata_gateway(settings.qmt)
        payload = gateway.doctor()
        payload["storage_root"] = str(settings.storage.root.resolve())
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "validate":
        payload = validation_payload(
            settings,
            full=bool(args.full),
            baseline=args.baseline,
            changed_names={
                Path(item.strip()).name for item in args.changed.split(",") if item.strip()
            },
        )
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0 if payload["integrity_ok"] and payload["duplicate_primary_keys"] == 0 else 2
    if args.command == "eod-sync":
        eod_summary = EodSyncJob(settings, build_xtdata_gateway(settings.qmt)).run()
        print(json.dumps(asdict(eod_summary), ensure_ascii=False, indent=2))
        return 0 if eod_summary.status in {"completed", "skipped"} else 2
    if args.command == "backfill":
        gateway = build_xtdata_gateway(settings.qmt)
        runner = BackfillRunner(settings, gateway)
        backfill_summary = runner.run(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            periods=_period_args(args.periods),
            sectors=args.sector or settings.universe.sectors,
            include_delisted=bool(args.include_delisted),
            symbols_override=[
                item.strip().upper() for item in args.symbols.split(",") if item.strip()
            ],
        )
        print(json.dumps(summary_dict(backfill_summary), ensure_ascii=False, indent=2))
        return 0 if backfill_summary.failed_items == 0 else 2
    if args.command == "import-qmt-dat":
        data_dir = args.data_dir or settings.qmt.big_data_dir
        if data_dir is None:
            raise ValueError("QMT DAT import requires --data-dir or qmt.big_data_dir")
        dat_summary = QmtDatImportRunner(settings, data_dir, source=str(args.source)).run(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            markets={item.strip().upper() for item in args.markets.split(",") if item.strip()},
            instrument_types=tuple(
                item.strip().lower() for item in args.instrument_types.split(",") if item.strip()
            ),
            symbols_override=[
                item.strip().upper() for item in args.symbols.split(",") if item.strip()
            ],
        )
        print(json.dumps(dat_summary_dict(dat_summary), ensure_ascii=False, indent=2))
        return 0 if dat_summary.failed_items == 0 else 2
    if args.command == "refresh-catalog":
        refresh_summary = BackfillRunner(settings, build_xtdata_gateway(settings.qmt)).run(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            periods=[],
            sectors=args.sector or settings.universe.sectors,
            include_delisted=bool(args.include_delisted),
            job_type="catalog-refresh",
        )
        print(json.dumps(summary_dict(refresh_summary), ensure_ascii=False, indent=2))
        return 0
    if args.command == "backfill-financial":
        if not settings.financial.enabled:
            raise RuntimeError("financial backfill is disabled by configuration")
        financial_summary = FinancialBackfillRunner(
            settings, build_xtdata_gateway(settings.qmt)
        ).run(
            start=_date_arg(args.start),
            end=_date_arg(args.end),
            symbols_override=[
                item.strip().upper() for item in args.symbols.split(",") if item.strip()
            ],
            resume=bool(args.resume),
        )
        print(json.dumps(financial_summary_dict(financial_summary), ensure_ascii=False, indent=2))
        return 0 if financial_summary.failed_items == 0 else 2
    if args.command == "sync-reference-data":
        reference_summary = ReferenceDataSyncRunner(
            settings, build_xtdata_gateway(settings.qmt)
        ).run(snapshot_date=_date_arg(args.snapshot_date), resume=bool(args.resume))
        print(json.dumps(reference_summary_dict(reference_summary), ensure_ascii=False, indent=2))
        return 0 if reference_summary.failed_items == 0 else 2
    if args.command == "query":
        local = LocalXtData(settings)
        frames = local.get_market_data_ex(
            field_list=[],
            stock_list=[item.strip().upper() for item in args.symbols.split(",")],
            period=args.period,
            start_time=args.start,
            end_time=args.end,
            count=args.count,
            dividend_type=args.adjust,
            fill_data=False,
        )
        print(json.dumps(_query_payload(frames), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "backtest-double-ma":
        symbol = str(args.symbol).upper()
        strategy = DoubleMovingAverageStrategy(
            symbol, fast=int(args.fast), slow=int(args.slow), quantity=int(args.quantity)
        )
        backtest_result = QmtBacktestEngine(
            LocalXtData(settings), result_root=settings.backtest.result_root
        ).run(
            strategy,
            symbols=[symbol],
            period=str(args.period),
            start=str(args.start),
            end=str(args.end),
            strategy_name="double_moving_average",
        )
        print(
            json.dumps(
                {
                    "run_id": backtest_result.run_id,
                    "bars_processed": backtest_result.bars_processed,
                    "orders": len(backtest_result.orders),
                    "trades": len(backtest_result.trades),
                    "final_cash": str(backtest_result.final_cash),
                    "final_assets": str(backtest_result.final_assets),
                    "result_database": backtest_result.result_database,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "archive":
        archive_service = MarketDataArchive(settings.storage, settings.archive)
        if args.archive_command == "verify":
            paths = (
                [args.path]
                if args.path is not None
                else sorted(settings.archive.root.rglob("*.db"))
            )
            verified = [asdict(archive_service.verify_file(path)) for path in paths]
            print(json.dumps({"verified": verified}, ensure_ascii=False, indent=2))
            return 0
        if args.archive_command == "seal":
            result = archive_service.seal_minute_shard(args.path, vacuum=bool(args.vacuum))
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0
        if args.archive_command == "restore":
            result = archive_service.restore(args.path, args.target)
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0
        raise AssertionError(f"unhandled archive command: {args.archive_command}")
    raise AssertionError(f"unhandled command: {args.command}")


def _date_arg(value: str) -> int:
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) != 8:
        raise ValueError(f"date must use YYYYMMDD: {value}")
    return int(digits)


def _period_args(value: str) -> list[str]:
    # PowerShell evaluates an unquoted `1d,1m` as the native argument `1,1m`.
    # Preserve the documented command by recognizing that exact decimal-suffix loss.
    values = [item.strip() for item in value.split(",") if item.strip()]
    normalized = ["1d" if item == "1" else item for item in values]
    unsupported = sorted(set(normalized) - {"1d", "1m"})
    if unsupported:
        raise ValueError("unsupported raw periods: " + ", ".join(unsupported))
    return list(dict.fromkeys(normalized))


def _query_payload(frames: dict[str, Any]) -> dict[str, object]:
    result: dict[str, object] = {}
    for symbol, frame in frames.items():
        result[symbol] = {
            "rows": len(frame),
            "columns": list(frame.columns),
            "first": None if frame.empty else str(frame.index[0]),
            "last": None if frame.empty else str(frame.index[-1]),
            "head": frame.head(5).reset_index().to_dict(orient="records"),
        }
    return result


if __name__ == "__main__":
    raise SystemExit(main())
