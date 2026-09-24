"""Explainable demand-forecasting helpers for the Inventory-AI project.

This module intentionally implements a small moving-average baseline rather
than a machine-learning model. It is easy to inspect and can be replaced by a
more advanced model in a later project stage.
"""

from datetime import date, timedelta
from decimal import Decimal
from math import ceil


def get_daily_product_sales(connection, product_id, days=90):
    """Return daily completed-sale quantities, including zero-sales days.

    The returned list contains dictionaries with ``date`` and ``quantity``
    keys, ordered from oldest to newest. The database query only returns days
    with sales; Python fills the requested date range with zero quantities so
    the baseline does not ignore days without demand.
    """
    if not isinstance(days, int) or isinstance(days, bool) or days <= 0:
        raise ValueError("days must be a positive integer")
    if not isinstance(product_id, int) or isinstance(product_id, bool) or product_id <= 0:
        raise ValueError("product_id must be a positive integer")

    start_date = date.today() - timedelta(days=days - 1)
    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT DATE(s.sale_date) AS sale_day,
                   COALESCE(SUM(si.quantity), 0) AS quantity
            FROM sales AS s
            INNER JOIN sale_items AS si ON s.sale_id = si.sale_id
            WHERE s.status = 'completed'
              AND si.product_id = %s
              AND DATE(s.sale_date) >= %s
              AND DATE(s.sale_date) <= CURDATE()
            GROUP BY DATE(s.sale_date)
            ORDER BY sale_day ASC
            """,
            (product_id, start_date),
        )
        rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()

    quantities_by_day = {
        row["sale_day"]: row["quantity"]
        for row in rows
    }
    return [
        {
            "date": start_date + timedelta(days=offset),
            "quantity": quantities_by_day.get(
                start_date + timedelta(days=offset), Decimal("0")
            ),
        }
        for offset in range(days)
    ]


def _quantity_values(daily_sales):
    """Normalize numeric observations and date/quantity dictionaries."""
    values = []
    for observation in daily_sales:
        if isinstance(observation, dict):
            value = observation.get("quantity", 0)
        else:
            value = observation
        values.append(float(value))
    return values


def calculate_moving_average(daily_sales, window=7):
    """Calculate the latest moving average using available observations."""
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("window must be a positive integer")

    values = _quantity_values(daily_sales)
    if not values:
        return 0.0
    observations = values[-window:]
    return sum(observations) / len(observations)


def forecast_demand(daily_sales, forecast_days=7, window=7):
    """Return a constant moving-average forecast for the next period."""
    if (
        not isinstance(forecast_days, int)
        or isinstance(forecast_days, bool)
        or forecast_days <= 0
    ):
        raise ValueError("forecast_days must be a positive integer")

    daily_forecast = calculate_moving_average(daily_sales, window=window)
    return {
        "daily_forecast": daily_forecast,
        "forecast_days": forecast_days,
        "total_forecast": daily_forecast * forecast_days,
        "method": f"{window}-day moving average",
    }


def calculate_reorder_recommendation(
    current_stock, forecast_demand, reorder_level
):
    """Return a simple baseline replenishment recommendation.

    The recommendation covers forecasted demand or the existing reorder level,
    whichever is higher. It does not account for lead time, safety stock,
    seasonality, promotions, or sudden demand changes.
    """
    current = Decimal(str(current_stock))
    forecast = Decimal(str(forecast_demand))
    reorder = Decimal(str(reorder_level))
    recommended_stock = max(reorder, Decimal(ceil(forecast)))
    recommended_order_quantity = max(
        Decimal("0"), recommended_stock - current
    )
    return {
        "current_stock": current,
        "reorder_level": reorder,
        "forecasted_demand": forecast,
        "recommended_stock": recommended_stock,
        "recommended_order_quantity": recommended_order_quantity,
    }
