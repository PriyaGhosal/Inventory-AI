"""Small reusable helpers for the Inventory-AI application."""

from flask import current_app
import mysql.connector


def get_db_connection():
    """Open and return a MySQL connection using the Flask configuration.

    The connection is created only when a future route explicitly needs it;
    importing or starting the foundation app does not require MySQL to be up.
    """
    return mysql.connector.connect(**current_app.config["MYSQL_CONFIG"])
