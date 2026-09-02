import logging
import os
from logging.handlers import RotatingFileHandler

import pymysql
from dbutils.pooled_db import PooledDB

_pool = None


def init_pool(config) -> PooledDB:
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=config.MYSQL_POOL_SIZE,
            mincached=1,
            maxcached=config.MYSQL_POOL_SIZE,
            blocking=True,
            ping=1,
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
    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.LOG_DIR, config.LOG_FILE_NAME)

    logger = logging.getLogger("management_chatbot")
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False

    if not logger.handlers:
        handler = RotatingFileHandler(log_path, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(stream_handler)

    return logger
