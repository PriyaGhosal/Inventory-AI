"""Application configuration loaded from environment variables."""

import os


class Config:
    """Central configuration for Flask and the future MySQL connection."""

    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")

    MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "inventory_db")

    # This dictionary can be passed directly to mysql.connector.connect().
    MYSQL_CONFIG = {
        "host": MYSQL_HOST,
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "database": MYSQL_DATABASE,
    }
