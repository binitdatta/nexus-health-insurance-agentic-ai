"""
Shared infrastructure: a pooled MySQL connection factory and the
application/file logger. No ORM, no auto-DDL — every query elsewhere in
this app is a hand-written statement against the tables defined in
schema.sql.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

import pymysql
from dbutils.pooled_db import PooledDB

_pool = None


def init_pool(config) -> PooledDB:
    """Create (once) the process-wide pooled DB connection factory."""
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=config.MYSQL_POOL_SIZE,
            mincached=1,
            maxcached=config.MYSQL_POOL_SIZE,
            blocking=True,
            ping=1,  # ping connection on check-out, reconnect if stale
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=config.MYSQL_DB,
            connect_timeout=config.MYSQL_CONNECT_TIMEOUT,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    return _pool


def get_connection():
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool(config) first")
    return _pool.connection()


def init_app_logger(config) -> logging.Logger:
    """
    Flat-file JSON-lines logger for structured Gateway/LLM call logging.
    Rotates at LOG_MAX_BYTES, keeps LOG_BACKUP_COUNT backups. This is
    separate from Flask's own request logging — it's the audit trail
    that mirrors what also gets written to llm_call_log in MySQL.
    """
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.LOG_DIR, config.LOG_FILE_NAME)

    logger = logging.getLogger("llm_gateway")
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(
            log_path, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        # Also echo to stdout so `docker logs` / gunicorn capture works
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(stream_handler)

    return logger
