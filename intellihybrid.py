"""
IntelliHybrid — CLI Entry Point
Usage: intellihybrid <command> [options]
"""

import argparse
import json
import logging
import sys

from src.core.config_loader import ConfigLoader
from src.aws.dynamodb import DynamoDBManager
from src.onprem.vpn import VPNManager
from src.bridge.sync import SyncEngine


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_init(args):
    """intellihybrid init — validate config, establish VPN, provision DynamoDB tables."""
    config = ConfigLoader(args.config).load()
    setup_logging(config.log_level)
    logger = logging.getLogger("intellihybrid.init")

    logger.info("=" * 60)
    logger.info("  IntelliHybrid — Initialization")
    logger.info("=" * 60)

    # Step 1: VPN
    if not args.skip_vpn:
        logger.info("[1/3] Establishing VPN tunnel...")
        vpn = VPNManager(config)
        status = vpn.establish()
        logger.info(f"      ✅ VPN: {status['status']}")
    else:
        logger.info("[1/3] Skipping VPN setup (--skip-vpn flag)")

    # Step 2: DynamoDB
    logger.info("[2/3] Provisioning DynamoDB tables...")
    dynamo = DynamoDBManager(config)
    results = dynamo.provision_all_tables()
    for table, status in results.items():
        logger.info(f"      {status:10} {table}")

    # Step 3: On-prem DB connectivity test
    logger.info("[3/3] Testing on-prem database connectivity...")
    engine = SyncEngine(config)
    engine.db_connector = None  # just health check
    from src.onprem.database import create_database_connector
    try:
        db = create_database_connector(config.onprem.database)
        health = db.health_check()
        logger.info(f"      ✅ Database: {health['status']} ({health['type']} @ {health['host']})")
        db.disconnect()
    except Exception as e:
        logger.error(f"      ❌ Database connection failed: {e}")

    logger.info("")
    logger.info("✅ IntelliHybrid initialized successfully!")
    logger.info("   Run: intellihybrid sync --mode bidirectional")


def cmd_sync(args):
    """intellihybrid sync — start the sync engine."""
    config = ConfigLoader(args.config).load()
    setup_logging(config.log_level)
    logger = logging.getLogger("intellihybrid.sync")

    engine = SyncEngine(config)
    engine.initialize()

    if args.once:
        summary = engine.run_once(mode=args.mode)
        print(json.dumps(summary, indent=2))
    else:
        interval = args.interval or config.sync_interval_seconds
        engine.run_continuous(mode=args.mode, interval_seconds=interval)


def cmd_tables(args):
    """intellihybrid tables — list or describe DynamoDB tables."""
    config = ConfigLoader(args.config).load()
    setup_logging("INFO")
    dynamo = DynamoDBManager(config)

    if args.describe and args.table_name:
        info = dynamo.describe_table(args.table_name)
        print(json.dumps(info, indent=2, default=str))
    else:
        tables = dynamo.list_tables()
        print(f"DynamoDB tables in {config.aws.region}:")
        for t in tables:
            print(f"  - {t}")


def cmd_health(args):
    """intellihybrid health — check connectivity of all components."""
    config = ConfigLoader(args.config).load()
    setup_logging("INFO")
    engine = SyncEngine(config)
    engine.initialize()
    health = engine.health_check()
    print(json.dumps(health, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="intellihybrid",
        description="IntelliHybrid — Intelligent On-Premise ↔ AWS Cloud Connector",
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="Path to config.yaml (default: config/config.yaml)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize: VPN + DynamoDB + DB check")
    p_init.add_argument("--skip-vpn", action="store_true", help="Skip VPN tunnel setup")
    p_init.set_defaults(func=cmd_init)

    # sync
    p_sync = subparsers.add_parser("sync", help="Start data synchronization")
    p_sync.add_argument(
        "--mode", default="bidirectional",
        choices=["bidirectional", "push", "pull", "full", "incremental"],
        help="Sync direction (default: bidirectional)"
    )
    p_sync.add_argument("--interval", type=int, help="Seconds between sync cycles")
    p_sync.add_argument("--once", action="store_true", help="Run one cycle then exit")
    p_sync.set_defaults(func=cmd_sync)

    # tables
    p_tables = subparsers.add_parser("tables", help="List or describe DynamoDB tables")
    p_tables.add_argument("--describe", action="store_true")
    p_tables.add_argument("--table-name", type=str)
    p_tables.set_defaults(func=cmd_tables)

    # health
    p_health = subparsers.add_parser("health", help="Check all component health")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
