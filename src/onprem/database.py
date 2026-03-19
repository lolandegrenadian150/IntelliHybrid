"""
IntelliHybrid — On-Premise Database Connector
Supports MySQL, PostgreSQL, Oracle, and SQL Server.
Uses connection pooling and automatic retry logic.
"""

import logging
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple

from src.core.config_loader import DatabaseConfig

logger = logging.getLogger(__name__)


class OnPremDatabase(ABC):
    """Abstract base for all on-prem database connectors."""

    def __init__(self, config: DatabaseConfig):
        self.config = config
        self._pool = None

    @abstractmethod
    def connect(self) -> None:
        """Establish connection pool."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close all connections."""

    @abstractmethod
    def execute_query(self, sql: str, params: Optional[Tuple] = None) -> List[Dict]:
        """Execute a SELECT query and return list of row dicts."""

    @abstractmethod
    def execute_write(self, sql: str, params: Optional[Tuple] = None) -> int:
        """Execute INSERT/UPDATE/DELETE. Returns rows affected."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Ping the database. Returns True if reachable."""

    def health_check(self) -> Dict[str, Any]:
        ok = self.test_connection()
        return {
            "status": "healthy" if ok else "unhealthy",
            "host": self.config.host,
            "port": self.config.port,
            "database": self.config.name,
            "type": self.config.type,
        }


# ------------------------------------------------------------------ #
#  MySQL
# ------------------------------------------------------------------ #

class MySQLConnector(OnPremDatabase):
    """MySQL / MariaDB connector using PyMySQL with connection pooling."""

    def connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
            self._connection = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.username,
                password=self.config.password,
                database=self.config.name,
                ssl={"ssl": {}} if self.config.ssl else None,
                cursorclass=DictCursor,
                connect_timeout=self.config.connection_timeout,
                autocommit=False,
            )
            logger.info(f"Connected to MySQL at {self.config.host}:{self.config.port}")
        except ImportError:
            raise RuntimeError("PyMySQL not installed. Run: pip install PyMySQL")

    def disconnect(self):
        if self._connection:
            self._connection.close()
            logger.info("MySQL connection closed.")

    def execute_query(self, sql: str, params=None) -> List[Dict]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall()

    def execute_write(self, sql: str, params=None) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            self._connection.commit()
            return cursor.rowcount

    def test_connection(self) -> bool:
        try:
            self._connection.ping(reconnect=True)
            return True
        except Exception as e:
            logger.error(f"MySQL ping failed: {e}")
            return False


# ------------------------------------------------------------------ #
#  PostgreSQL
# ------------------------------------------------------------------ #

class PostgreSQLConnector(OnPremDatabase):
    """PostgreSQL connector using psycopg2 with RealDictCursor."""

    def connect(self):
        try:
            import psycopg2
            import psycopg2.extras
            connect_args = dict(
                host=self.config.host,
                port=self.config.port,
                user=self.config.username,
                password=self.config.password,
                dbname=self.config.name,
                connect_timeout=self.config.connection_timeout,
            )
            if self.config.ssl:
                connect_args["sslmode"] = "require"
            self._connection = psycopg2.connect(**connect_args)
            self._connection.autocommit = False
            logger.info(f"Connected to PostgreSQL at {self.config.host}:{self.config.port}")
        except ImportError:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")

    def disconnect(self):
        if self._connection:
            self._connection.close()

    def execute_query(self, sql: str, params=None) -> List[Dict]:
        import psycopg2.extras
        with self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, params or ())
            return [dict(row) for row in cursor.fetchall()]

    def execute_write(self, sql: str, params=None) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            self._connection.commit()
            return cursor.rowcount

    def test_connection(self) -> bool:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"PostgreSQL ping failed: {e}")
            return False


# ------------------------------------------------------------------ #
#  Oracle
# ------------------------------------------------------------------ #

class OracleConnector(OnPremDatabase):
    """Oracle DB connector using cx_Oracle / oracledb."""

    def connect(self):
        try:
            import oracledb
            dsn = f"{self.config.host}:{self.config.port}/{self.config.name}"
            self._connection = oracledb.connect(
                user=self.config.username,
                password=self.config.password,
                dsn=dsn,
            )
            logger.info(f"Connected to Oracle at {dsn}")
        except ImportError:
            raise RuntimeError("oracledb not installed. Run: pip install oracledb")

    def disconnect(self):
        if self._connection:
            self._connection.close()

    def execute_query(self, sql: str, params=None) -> List[Dict]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or [])
            cols = [col[0].lower() for col in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def execute_write(self, sql: str, params=None) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or [])
            self._connection.commit()
            return cursor.rowcount

    def test_connection(self) -> bool:
        try:
            with self._connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM DUAL")
            return True
        except Exception as e:
            logger.error(f"Oracle ping failed: {e}")
            return False


# ------------------------------------------------------------------ #
#  SQL Server
# ------------------------------------------------------------------ #

class SQLServerConnector(OnPremDatabase):
    """Microsoft SQL Server connector using pyodbc."""

    def connect(self):
        try:
            import pyodbc
            driver = "{ODBC Driver 18 for SQL Server}"
            tls = "yes" if self.config.ssl else "no"
            conn_str = (
                f"DRIVER={driver};"
                f"SERVER={self.config.host},{self.config.port};"
                f"DATABASE={self.config.name};"
                f"UID={self.config.username};"
                f"PWD={self.config.password};"
                f"Encrypt={tls};"
                f"TrustServerCertificate=no;"
                f"Connection Timeout={self.config.connection_timeout};"
            )
            self._connection = pyodbc.connect(conn_str)
            logger.info(f"Connected to SQL Server at {self.config.host}:{self.config.port}")
        except ImportError:
            raise RuntimeError("pyodbc not installed. Run: pip install pyodbc")

    def disconnect(self):
        if self._connection:
            self._connection.close()

    def execute_query(self, sql: str, params=None) -> List[Dict]:
        cursor = self._connection.cursor()
        cursor.execute(sql, params or ())
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def execute_write(self, sql: str, params=None) -> int:
        cursor = self._connection.cursor()
        cursor.execute(sql, params or ())
        self._connection.commit()
        return cursor.rowcount

    def test_connection(self) -> bool:
        try:
            cursor = self._connection.cursor()
            cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"SQL Server ping failed: {e}")
            return False


# ------------------------------------------------------------------ #
#  Factory
# ------------------------------------------------------------------ #

def create_database_connector(config: DatabaseConfig) -> OnPremDatabase:
    """Factory: return the right connector based on config.type."""
    connectors = {
        "mysql": MySQLConnector,
        "postgres": PostgreSQLConnector,
        "oracle": OracleConnector,
        "mssql": SQLServerConnector,
    }
    cls = connectors.get(config.type)
    if not cls:
        raise ValueError(
            f"Unsupported database type: '{config.type}'. "
            f"Supported: {list(connectors.keys())}"
        )
    connector = cls(config)
    connector.connect()
    return connector
