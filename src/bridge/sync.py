"""
IntelliHybrid — Bridge Sync Engine
Bidirectional sync: on-prem database ↔ AWS DynamoDB.
Supports full sync, incremental (CDC-style), push-only, and pull-only modes.
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.aws.dynamodb import DynamoDBManager
from src.core.config_loader import HybridConfig
from src.onprem.database import OnPremDatabase, create_database_connector

logger = logging.getLogger(__name__)


def _serialize_for_dynamo(obj: Any) -> Any:
    """Convert Python types to DynamoDB-safe types."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_for_dynamo(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_for_dynamo(i) for i in obj]
    return obj


def _row_fingerprint(row: Dict) -> str:
    """SHA-256 fingerprint of a row — used for change detection."""
    serialized = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


class SyncEngine:
    """
    Orchestrates bidirectional data synchronization between
    on-premise databases and AWS DynamoDB.
    """

    MODES = {"full", "incremental", "push", "pull", "bidirectional"}

    def __init__(self, config: HybridConfig):
        self.config = config
        self.dynamo = DynamoDBManager(config)
        self.db: Optional[OnPremDatabase] = None
        self._seen_fingerprints: Dict[str, str] = {}  # row_id → fingerprint

    def initialize(self):
        """Connect to on-prem database and provision DynamoDB tables."""
        logger.info("Initializing IntelliHybrid SyncEngine...")
        self.db = create_database_connector(self.config.onprem.database)
        logger.info("✅ On-prem database connected.")

        results = self.dynamo.provision_all_tables()
        for table, status in results.items():
            logger.info(f"  Table '{table}': {status}")
        logger.info("✅ DynamoDB tables provisioned.")

    def run_once(self, mode: str = "bidirectional") -> Dict:
        """Execute one sync cycle. Returns sync summary."""
        if mode not in self.MODES:
            raise ValueError(f"Invalid mode '{mode}'. Choose from: {self.MODES}")

        summary = {
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pushed": 0,
            "pulled": 0,
            "errors": [],
        }

        if mode in ("push", "bidirectional", "full"):
            pushed = self._sync_onprem_to_dynamo()
            summary["pushed"] = pushed

        if mode in ("pull", "bidirectional"):
            pulled = self._sync_dynamo_to_onprem()
            summary["pulled"] = pulled

        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        logger.info(f"Sync complete: pushed={summary['pushed']}, pulled={summary['pulled']}")
        return summary

    def run_continuous(self, mode: str = "bidirectional", interval_seconds: int = 60):
        """Run sync in a continuous loop."""
        logger.info(f"Starting continuous sync — mode={mode}, interval={interval_seconds}s")
        while True:
            try:
                summary = self.run_once(mode)
                logger.info(f"[{summary['started_at']}] pushed={summary['pushed']} pulled={summary['pulled']}")
            except KeyboardInterrupt:
                logger.info("Sync stopped by user.")
                break
            except Exception as e:
                logger.error(f"Sync cycle error: {e}", exc_info=True)
            time.sleep(interval_seconds)

    # ------------------------------------------------------------------ #
    #  On-Prem → DynamoDB
    # ------------------------------------------------------------------ #

    def _sync_onprem_to_dynamo(self) -> int:
        """Push all configured on-prem tables to DynamoDB."""
        total_written = 0
        for table_cfg in self.config.dynamodb.tables:
            pk_name = table_cfg.partition_key["name"]
            # Heuristic: use the DynamoDB table name as SQL table name
            sql_table = table_cfg.name.replace("-", "_")
            try:
                rows = self.db.execute_query(f"SELECT * FROM {sql_table}")
                if not rows:
                    logger.debug(f"No rows in '{sql_table}' — skipping push.")
                    continue

                items_to_write = []
                for row in rows:
                    fp = _row_fingerprint(row)
                    row_id = str(row.get(pk_name, ""))
                    if self._seen_fingerprints.get(row_id) == fp:
                        continue  # unchanged — skip
                    self._seen_fingerprints[row_id] = fp
                    items_to_write.append(_serialize_for_dynamo(dict(row)))

                if items_to_write:
                    written = self.dynamo.batch_write(table_cfg.name, items_to_write)
                    total_written += written
                    logger.info(f"  Pushed {written} changed rows from '{sql_table}' → '{table_cfg.name}'")

            except Exception as e:
                logger.error(f"Push failed for table '{table_cfg.name}': {e}", exc_info=True)

        return total_written

    # ------------------------------------------------------------------ #
    #  DynamoDB → On-Prem
    # ------------------------------------------------------------------ #

    def _sync_dynamo_to_onprem(self) -> int:
        """Pull DynamoDB items back to on-prem database (upsert)."""
        total_pulled = 0
        db_type = self.config.onprem.database.type

        for table_cfg in self.config.dynamodb.tables:
            sql_table = table_cfg.name.replace("-", "_")
            try:
                items = self.dynamo.scan_table(table_cfg.name)
                if not items:
                    continue

                upserted = 0
                for item in items:
                    try:
                        upserted += self._upsert_to_onprem(sql_table, item, db_type)
                    except Exception as e:
                        logger.warning(f"  Upsert failed for item in '{sql_table}': {e}")

                total_pulled += upserted
                if upserted:
                    logger.info(f"  Pulled {upserted} items from '{table_cfg.name}' → '{sql_table}'")

            except Exception as e:
                logger.error(f"Pull failed for table '{table_cfg.name}': {e}", exc_info=True)

        return total_pulled

    def _upsert_to_onprem(self, table: str, item: Dict, db_type: str) -> int:
        """Generate and execute an upsert SQL statement."""
        cols = list(item.keys())
        vals = [str(item[c]) for c in cols]

        if db_type == "mysql":
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(f"`{c}`" for c in cols)
            updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols)
            sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
        elif db_type == "postgres":
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(f'"{c}"' for c in cols)
            pk = cols[0]
            updates = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in cols if c != pk)
            sql = f'INSERT INTO "{table}" ({col_names}) VALUES ({placeholders}) ON CONFLICT ("{pk}") DO UPDATE SET {updates}'
        elif db_type == "mssql":
            col_names = ", ".join(f"[{c}]" for c in cols)
            placeholders = ", ".join(["?"] * len(cols))
            sql = f"IF NOT EXISTS (SELECT 1 FROM [{table}] WHERE [{cols[0]}]=?) INSERT INTO [{table}] ({col_names}) VALUES ({placeholders})"
            vals = [vals[0]] + vals  # add pk val twice for EXISTS check
        else:
            # Oracle MERGE
            pk = cols[0]
            updates = ", ".join(f"t.{c}=s.{c}" for c in cols if c != pk)
            col_names = ", ".join(cols)
            s_cols = ", ".join(f":{i+1} {c}" for i, c in enumerate(cols))
            sql = (
                f"MERGE INTO {table} t USING (SELECT {s_cols} FROM DUAL) s "
                f"ON (t.{pk}=s.{pk}) "
                f"WHEN MATCHED THEN UPDATE SET {updates} "
                f"WHEN NOT MATCHED THEN INSERT ({col_names}) VALUES ({', '.join('s.'+c for c in cols)})"
            )

        return self.db.execute_write(sql, tuple(vals))

    def health_check(self) -> Dict:
        """Return health status of all connections."""
        db_health = self.db.health_check() if self.db else {"status": "not_initialized"}
        return {
            "onprem_database": db_health,
            "aws_region": self.config.aws.region,
            "dynamodb_tables": self.dynamo.list_tables(),
        }
