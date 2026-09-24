"""Train and evaluate a persisted demand model for one active product.

Usage:
    python scripts/train_demand_model.py 12
"""

import argparse
import sys
from pathlib import Path

import mysql.connector

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app import app
from utils.forecasting import get_daily_product_sales, train_and_forecast_ml
from utils.helpers import get_db_connection


def main():
    parser = argparse.ArgumentParser(description="Train a product demand model.")
    parser.add_argument("product_id", type=int, help="Active product ID")
    args = parser.parse_args()
    if args.product_id <= 0:
        parser.error("product_id must be positive")

    connection = None
    cursor = None
    try:
        with app.app_context():
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT product_id, product_name, sku
                FROM products
                WHERE product_id = %s AND is_active = TRUE
                LIMIT 1
                """,
                (args.product_id,),
            )
            product = cursor.fetchone()
            if product is None:
                print("The selected product is not available.")
                return 1

            daily_sales = get_daily_product_sales(
                connection, product["product_id"], days=90
            )
            model_path = (
                project_root
                / "models"
                / f"demand_model_{product['product_id']}.joblib"
            )
            result = train_and_forecast_ml(
                daily_sales, forecast_days=7, model_path=model_path, force_retrain=True
            )
            print(f"Product: {product['product_name']} ({product['sku']})")
            if not result["available"]:
                print(result["reason"])
                return 0
            print(f"Training rows: {result['training_rows']}")
            print(f"Test rows: {result['test_rows']}")
            print(f"Baseline MAE: {result['baseline_mae']:.4f}")
            print(f"Baseline RMSE: {result['baseline_rmse']:.4f}")
            print(f"ML MAE: {result['mae']:.4f}")
            print(f"ML RMSE: {result['rmse']:.4f}")
            print(f"Selected method: {result['preferred_method']}")
            print(f"Saved model: {model_path}")
            return 0
    except (mysql.connector.Error, OSError, ValueError, KeyError):
        app.logger.exception("Unable to train demand model")
        print("Demand model training failed. Check the application log.")
        return 1
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
