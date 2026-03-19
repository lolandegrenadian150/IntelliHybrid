"""
IntelliHybrid — Configuration Loader
Loads and validates config.yaml, resolves environment variable references.
"""

import os
import re
import yaml
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively replace ${VAR_NAME} placeholders with environment variable values."""
    if isinstance(value, str):
        def replacer(match):
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                raise EnvironmentError(
                    f"Required environment variable '{var_name}' is not set. "
                    f"Please export it before running IntelliHybrid."
                )
            return env_val
        return ENV_VAR_PATTERN.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


@dataclass
class AWSConfig:
    region: str
    account_id: str
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str] = None
    role_arn: Optional[str] = None


@dataclass
class DatabaseConfig:
    type: str          # mysql | postgres | oracle | mssql
    host: str
    port: int
    name: str
    username: str
    password: str
    ssl: bool = True
    connection_pool_size: int = 5
    connection_timeout: int = 30


@dataclass
class VPNConfig:
    type: str          # site-to-site | openvpn | direct-connect
    customer_gateway_ip: str
    bgp_asn: int = 65000
    tunnel_inside_cidr: Optional[str] = None
    pre_shared_key: Optional[str] = None
    config_file: Optional[str] = None   # for openvpn .ovpn path


@dataclass
class OnPremConfig:
    database: DatabaseConfig
    vpn: VPNConfig
    subnet_cidr: str = "192.168.0.0/16"


@dataclass
class DynamoTableConfig:
    name: str
    partition_key: Dict[str, str]
    sort_key: Optional[Dict[str, str]]
    billing_mode: str = "PAY_PER_REQUEST"
    encryption: bool = True
    point_in_time_recovery: bool = True
    ttl_attribute: Optional[str] = None
    gsi: list = field(default_factory=list)
    lsi: list = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class DynamoDBConfig:
    tables: list[DynamoTableConfig]


@dataclass
class HybridConfig:
    aws: AWSConfig
    onprem: OnPremConfig
    dynamodb: DynamoDBConfig
    log_level: str = "INFO"
    sync_interval_seconds: int = 60


class ConfigLoader:
    """Load, validate, and resolve IntelliHybrid configuration."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Copy config/config.template.yaml → config/config.yaml and fill in your values."
            )

    def load(self) -> HybridConfig:
        """Parse YAML, resolve env vars, and return a typed HybridConfig object."""
        logger.info(f"Loading configuration from {self.config_path}")
        with open(self.config_path, "r") as f:
            raw = yaml.safe_load(f)

        resolved = _resolve_env_vars(raw)
        config = self._parse(resolved)
        self._validate(config)
        logger.info("Configuration loaded and validated successfully.")
        return config

    def _parse(self, data: Dict) -> HybridConfig:
        aws_data = data["aws"]
        aws = AWSConfig(
            region=aws_data["region"],
            account_id=str(aws_data["account_id"]),
            access_key_id=aws_data["access_key_id"],
            secret_access_key=aws_data["secret_access_key"],
            session_token=aws_data.get("session_token"),
            role_arn=aws_data.get("role_arn"),
        )

        db_data = data["onprem"]["database"]
        database = DatabaseConfig(
            type=db_data["type"].lower(),
            host=db_data["host"],
            port=int(db_data["port"]),
            name=db_data["name"],
            username=db_data["username"],
            password=db_data["password"],
            ssl=db_data.get("ssl", True),
            connection_pool_size=db_data.get("connection_pool_size", 5),
            connection_timeout=db_data.get("connection_timeout", 30),
        )

        vpn_data = data["onprem"]["vpn"]
        vpn = VPNConfig(
            type=vpn_data["type"].lower(),
            customer_gateway_ip=vpn_data["customer_gateway_ip"],
            bgp_asn=vpn_data.get("bgp_asn", 65000),
            tunnel_inside_cidr=vpn_data.get("tunnel_inside_cidr"),
            pre_shared_key=vpn_data.get("pre_shared_key"),
            config_file=vpn_data.get("config_file"),
        )

        onprem = OnPremConfig(
            database=database,
            vpn=vpn,
            subnet_cidr=data["onprem"].get("subnet_cidr", "192.168.0.0/16"),
        )

        tables = []
        for t in data.get("dynamodb", {}).get("tables", []):
            tables.append(DynamoTableConfig(
                name=t["name"],
                partition_key=t["partition_key"],
                sort_key=t.get("sort_key"),
                billing_mode=t.get("billing_mode", "PAY_PER_REQUEST"),
                encryption=t.get("encryption", True),
                point_in_time_recovery=t.get("point_in_time_recovery", True),
                ttl_attribute=t.get("ttl_attribute"),
                gsi=t.get("gsi", []),
                lsi=t.get("lsi", []),
                tags=t.get("tags", {}),
            ))

        return HybridConfig(
            aws=aws,
            onprem=onprem,
            dynamodb=DynamoDBConfig(tables=tables),
            log_level=data.get("log_level", "INFO"),
            sync_interval_seconds=data.get("sync_interval_seconds", 60),
        )

    def _validate(self, config: HybridConfig):
        valid_db_types = {"mysql", "postgres", "oracle", "mssql"}
        if config.onprem.database.type not in valid_db_types:
            raise ValueError(
                f"Invalid database type '{config.onprem.database.type}'. "
                f"Supported: {valid_db_types}"
            )

        valid_vpn_types = {"site-to-site", "openvpn", "direct-connect"}
        if config.onprem.vpn.type not in valid_vpn_types:
            raise ValueError(
                f"Invalid VPN type '{config.onprem.vpn.type}'. "
                f"Supported: {valid_vpn_types}"
            )

        valid_key_types = {"S", "N", "B"}
        for table in config.dynamodb.tables:
            pk_type = table.partition_key.get("type", "")
            if pk_type not in valid_key_types:
                raise ValueError(
                    f"Table '{table.name}': partition_key type must be S, N, or B. Got: '{pk_type}'"
                )
            if table.sort_key:
                sk_type = table.sort_key.get("type", "")
                if sk_type not in valid_key_types:
                    raise ValueError(
                        f"Table '{table.name}': sort_key type must be S, N, or B. Got: '{sk_type}'"
                    )

        logger.debug("All configuration validations passed.")
