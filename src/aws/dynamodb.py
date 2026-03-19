"""
IntelliHybrid — DynamoDB Manager
Auto-provisions DynamoDB tables based on config-defined PK/SK schemas.
Supports GSIs, LSIs, encryption, TTL, and PITR.
"""

import logging
import time
from typing import Dict, List, Optional, Any

import boto3
from botocore.exceptions import ClientError

from src.core.config_loader import HybridConfig, DynamoTableConfig

logger = logging.getLogger(__name__)


class DynamoDBManager:
    """
    Manages DynamoDB table lifecycle: create, update, describe, delete.
    All table schemas are driven entirely by config — no code changes needed.
    """

    def __init__(self, config: HybridConfig):
        self.config = config
        self.client = boto3.client(
            "dynamodb",
            region_name=config.aws.region,
            aws_access_key_id=config.aws.access_key_id,
            aws_secret_access_key=config.aws.secret_access_key,
            aws_session_token=config.aws.session_token,
        )
        self.resource = boto3.resource(
            "dynamodb",
            region_name=config.aws.region,
            aws_access_key_id=config.aws.access_key_id,
            aws_secret_access_key=config.aws.secret_access_key,
            aws_session_token=config.aws.session_token,
        )

    @classmethod
    def from_config(cls, config: HybridConfig) -> "DynamoDBManager":
        return cls(config)

    # ------------------------------------------------------------------ #
    #  Table Provisioning
    # ------------------------------------------------------------------ #

    def provision_all_tables(self) -> Dict[str, str]:
        """
        Create or update all tables defined in config.yaml.
        Returns a dict of {table_name: status}.
        """
        results = {}
        for table_cfg in self.config.dynamodb.tables:
            try:
                if self._table_exists(table_cfg.name):
                    logger.info(f"Table '{table_cfg.name}' already exists — skipping creation.")
                    results[table_cfg.name] = "EXISTS"
                else:
                    self.create_table(table_cfg)
                    results[table_cfg.name] = "CREATED"
            except Exception as e:
                logger.error(f"Failed to provision table '{table_cfg.name}': {e}")
                results[table_cfg.name] = f"ERROR: {e}"
        return results

    def create_table(self, table_cfg: DynamoTableConfig) -> Dict:
        """Create a single DynamoDB table from a DynamoTableConfig object."""
        logger.info(f"Creating DynamoDB table: '{table_cfg.name}'")

        key_schema, attribute_definitions = self._build_key_schema(
            table_cfg.partition_key, table_cfg.sort_key
        )

        # Collect all attribute definitions (PK, SK, GSI keys, LSI keys)
        all_attrs: Dict[str, str] = {}
        for attr in attribute_definitions:
            all_attrs[attr["AttributeName"]] = attr["AttributeType"]

        gsi_configs = []
        for gsi in table_cfg.gsi:
            gsi_key_schema, gsi_attrs = self._build_key_schema(
                gsi["partition_key"], gsi.get("sort_key")
            )
            for a in gsi_attrs:
                all_attrs[a["AttributeName"]] = a["AttributeType"]
            gsi_configs.append({
                "IndexName": gsi["name"],
                "KeySchema": gsi_key_schema,
                "Projection": gsi.get("projection", {"ProjectionType": "ALL"}),
            })

        lsi_configs = []
        for lsi in table_cfg.lsi:
            lsi_key_schema, lsi_attrs = self._build_key_schema(
                table_cfg.partition_key, lsi["sort_key"]
            )
            for a in lsi_attrs:
                all_attrs[a["AttributeName"]] = a["AttributeType"]
            lsi_configs.append({
                "IndexName": lsi["name"],
                "KeySchema": lsi_key_schema,
                "Projection": lsi.get("projection", {"ProjectionType": "ALL"}),
            })

        params: Dict[str, Any] = {
            "TableName": table_cfg.name,
            "KeySchema": key_schema,
            "AttributeDefinitions": [
                {"AttributeName": k, "AttributeType": v} for k, v in all_attrs.items()
            ],
            "BillingMode": table_cfg.billing_mode,
        }

        if gsi_configs:
            params["GlobalSecondaryIndexes"] = gsi_configs
        if lsi_configs:
            params["LocalSecondaryIndexes"] = lsi_configs

        if table_cfg.encryption:
            params["SSESpecification"] = {
                "Enabled": True,
                "SSEType": "KMS",
            }

        # Tags
        default_tags = {
            "ManagedBy": "IntelliHybrid",
            "Project": "HybridConnector",
        }
        merged_tags = {**default_tags, **table_cfg.tags}
        params["Tags"] = [{"Key": k, "Value": v} for k, v in merged_tags.items()]

        response = self.client.create_table(**params)

        # Wait for ACTIVE
        self._wait_for_table_active(table_cfg.name)

        # Enable Point-In-Time Recovery
        if table_cfg.point_in_time_recovery:
            self._enable_pitr(table_cfg.name)

        # Enable TTL
        if table_cfg.ttl_attribute:
            self._enable_ttl(table_cfg.name, table_cfg.ttl_attribute)

        logger.info(f"✅ Table '{table_cfg.name}' is ACTIVE and ready.")
        return response

    def delete_table(self, table_name: str) -> bool:
        """Delete a DynamoDB table. Returns True on success."""
        try:
            self.client.delete_table(TableName=table_name)
            logger.info(f"Table '{table_name}' deletion initiated.")
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.warning(f"Table '{table_name}' does not exist.")
                return False
            raise

    def list_tables(self) -> List[str]:
        """List all DynamoDB tables in the account/region."""
        tables = []
        paginator = self.client.get_paginator("list_tables")
        for page in paginator.paginate():
            tables.extend(page["TableNames"])
        return tables

    def describe_table(self, table_name: str) -> Dict:
        """Return full table description."""
        response = self.client.describe_table(TableName=table_name)
        return response["Table"]

    # ------------------------------------------------------------------ #
    #  Data Operations (used by Bridge/Sync layer)
    # ------------------------------------------------------------------ #

    def put_item(self, table_name: str, item: Dict) -> Dict:
        table = self.resource.Table(table_name)
        return table.put_item(Item=item)

    def get_item(self, table_name: str, key: Dict) -> Optional[Dict]:
        table = self.resource.Table(table_name)
        response = table.get_item(Key=key)
        return response.get("Item")

    def batch_write(self, table_name: str, items: List[Dict]) -> int:
        """Batch write up to 25 items at a time. Returns count written."""
        table = self.resource.Table(table_name)
        written = 0
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
                written += 1
        logger.debug(f"Batch wrote {written} items to '{table_name}'")
        return written

    def scan_table(self, table_name: str, filter_expression=None) -> List[Dict]:
        """Full table scan — use sparingly in production."""
        table = self.resource.Table(table_name)
        kwargs = {}
        if filter_expression:
            kwargs["FilterExpression"] = filter_expression
        items = []
        while True:
            response = table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _table_exists(self, table_name: str) -> bool:
        try:
            self.client.describe_table(TableName=table_name)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return False
            raise

    def _build_key_schema(
        self,
        partition_key: Dict[str, str],
        sort_key: Optional[Dict[str, str]],
    ):
        key_schema = [{"AttributeName": partition_key["name"], "KeyType": "HASH"}]
        attributes = [{"AttributeName": partition_key["name"], "AttributeType": partition_key["type"]}]
        if sort_key:
            key_schema.append({"AttributeName": sort_key["name"], "KeyType": "RANGE"})
            attributes.append({"AttributeName": sort_key["name"], "AttributeType": sort_key["type"]})
        return key_schema, attributes

    def _wait_for_table_active(self, table_name: str, timeout: int = 120):
        logger.info(f"Waiting for table '{table_name}' to become ACTIVE...")
        start = time.time()
        while True:
            desc = self.describe_table(table_name)
            status = desc["TableStatus"]
            if status == "ACTIVE":
                return
            if time.time() - start > timeout:
                raise TimeoutError(f"Table '{table_name}' did not become ACTIVE within {timeout}s.")
            logger.debug(f"Table status: {status}. Retrying in 5s...")
            time.sleep(5)

    def _enable_pitr(self, table_name: str):
        self.client.update_continuous_backups(
            TableName=table_name,
            PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
        )
        logger.debug(f"PITR enabled for '{table_name}'")

    def _enable_ttl(self, table_name: str, ttl_attribute: str):
        self.client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl_attribute},
        )
        logger.debug(f"TTL enabled on '{ttl_attribute}' for '{table_name}'")
