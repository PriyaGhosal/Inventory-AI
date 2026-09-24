"""Explainable demand forecasting helpers and the Stage 11 ML baseline.

The moving average remains the safe fallback. Random Forest is trained only
when enough real completed-sales observations are available and its metrics
are compared with the moving-average predictions on the same time-ordered
test period.
"""

from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from math import ceil, sqrt
from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


FEATURE_COLUMNS = (
    "day_of_week",
    "day_of_month",
    "month",
    "day_of_year",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_7",
    "rolling_mean_7",
)
MINIMUM_TRAINING_ROWS = 30


def get_daily_product_sales(connection, product_id, days=90):
    """Return daily completed-sale quantities, including zero-sales days."""
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

    quantities_by_day = {row["sale_day"]: row["quantity"] for row in rows}
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
        value = observation.get("quantity", 0) if isinstance(observation, dict) else observation
        values.append(float(value))
    return values


def _normalize_daily_sales(daily_sales):
    """Give numeric test observations deterministic calendar dates."""
    observations = list(daily_sales)
    dates = [
        observation.get("date") if isinstance(observation, dict) else None
        for observation in observations
    ]
    if any(current_date is None for current_date in dates):
        start_date = date.today() - timedelta(days=len(observations) - 1)
        dates = [start_date + timedelta(days=index) for index in range(len(observations))]
    return [
        {"date": current_date, "quantity": quantity}
        for current_date, quantity in zip(dates, _quantity_values(observations))
    ]


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


def calculate_reorder_recommendation(current_stock, forecast_demand, reorder_level):
    """Return a simple baseline replenishment recommendation."""
    current = Decimal(str(current_stock))
    forecast = Decimal(str(forecast_demand))
    reorder = Decimal(str(reorder_level))
    recommended_stock = max(reorder, Decimal(ceil(forecast)))
    recommended_order_quantity = max(Decimal("0"), recommended_stock - current)
    return {
        "current_stock": current,
        "reorder_level": reorder,
        "forecasted_demand": forecast,
        "recommended_stock": recommended_stock,
        "recommended_order_quantity": recommended_order_quantity,
    }


def build_demand_features(daily_sales):
    """Build leakage-safe calendar, lag, and rolling features.

    A row starts at index seven, so every feature uses only observations before
    the row's target date. The current day's quantity is never included.
    """
    normalized_sales = _normalize_daily_sales(daily_sales)
    dates = [observation["date"] for observation in normalized_sales]
    values = _quantity_values(normalized_sales)
    rows = []
    for index in range(7, len(values)):
        current_date = dates[index]
        previous_values = values[:index]
        rows.append(
            {
                "date": current_date,
                "day_of_week": current_date.weekday(),
                "day_of_month": current_date.day,
                "month": current_date.month,
                "day_of_year": current_date.timetuple().tm_yday,
                "lag_1": values[index - 1],
                "lag_2": values[index - 2],
                "lag_3": values[index - 3],
                "lag_7": values[index - 7],
                "rolling_mean_7": sum(previous_values[-7:]) / 7,
                "target": values[index],
            }
        )
    return rows


def _feature_vector(row):
    return [row[column] for column in FEATURE_COLUMNS]


def _baseline_predictions(values, start_index, window=7):
    """Predict each test target using only values available before it."""
    predictions = []
    for index in range(start_index, len(values)):
        history = values[:index]
        predictions.append(sum(history[-window:]) / min(window, len(history)))
    return predictions


def compare_forecasting_methods(actual, baseline_predictions, ml_predictions):
    """Compare both methods and prefer lower MAE, with baseline tie-breaking."""
    baseline_mae = mean_absolute_error(actual, baseline_predictions)
    baseline_rmse = sqrt(mean_squared_error(actual, baseline_predictions))
    ml_mae = mean_absolute_error(actual, ml_predictions)
    ml_rmse = sqrt(mean_squared_error(actual, ml_predictions))
    preferred_method = (
        "Random Forest Regression"
        if ml_mae < baseline_mae
        else "7-day moving average"
    )
    return {
        "baseline_mae": baseline_mae,
        "baseline_rmse": baseline_rmse,
        "ml_mae": ml_mae,
        "ml_rmse": ml_rmse,
        "preferred_method": preferred_method,
    }


def _data_signature(daily_sales):
    normalized_sales = _normalize_daily_sales(daily_sales)
    values = _quantity_values(normalized_sales)
    dates = [str(row["date"]) for row in normalized_sales]
    return sha256(repr(list(zip(dates, values))).encode("utf-8")).hexdigest()


def _future_features(history, future_date):
    """Build one recursive future feature row from known/predicted history."""
    return {
        "day_of_week": future_date.weekday(),
        "day_of_month": future_date.day,
        "month": future_date.month,
        "day_of_year": future_date.timetuple().tm_yday,
        "lag_1": history[-1],
        "lag_2": history[-2],
        "lag_3": history[-3],
        "lag_7": history[-7],
        "rolling_mean_7": sum(history[-7:]) / 7,
    }


def _recursive_forecast(model, daily_sales, forecast_days):
    normalized_sales = _normalize_daily_sales(daily_sales)
    values = _quantity_values(normalized_sales)
    last_date = normalized_sales[-1]["date"]
    predictions = []
    for offset in range(1, forecast_days + 1):
        future_date = last_date + timedelta(days=offset)
        features = _future_features(values, future_date)
        prediction = max(0.0, float(model.predict([_feature_vector(features)])[0]))
        predictions.append(prediction)
        values.append(prediction)
    return predictions


def train_and_forecast_ml(
    daily_sales, forecast_days=7, model_path=None, force_retrain=False
):
    """Train/load, evaluate, and recursively forecast with Random Forest.

    Models are cached with a signature of the real daily-sales input. A changed
    history causes retraining; otherwise the persisted model and metrics are
    reused so opening the page does not retrain on every request.
    """
    feature_rows = build_demand_features(daily_sales)
    if len(feature_rows) < MINIMUM_TRAINING_ROWS:
        return {
            "available": False,
            "reason": "Not enough historical data for ML forecasting.",
        }

    signature = _data_signature(daily_sales)
    artifact = None
    path = Path(model_path) if model_path else None
    if path and path.exists() and not force_retrain:
        try:
            artifact = joblib.load(path)
            if artifact.get("signature") != signature:
                artifact = None
        except (OSError, ValueError, KeyError):
            artifact = None

    values = _quantity_values(daily_sales)
    split_index = max(1, int(len(feature_rows) * 0.8))
    if artifact is None:
        train_rows = feature_rows[:split_index]
        test_rows = feature_rows[split_index:]
        model = RandomForestRegressor(
            n_estimators=100,
            random_state=42,
            min_samples_leaf=2,
        )
        model.fit(
            [_feature_vector(row) for row in train_rows],
            [row["target"] for row in train_rows],
        )
        ml_predictions = [
            max(0.0, float(value))
            for value in model.predict([_feature_vector(row) for row in test_rows])
        ]
        actual = [row["target"] for row in test_rows]
        test_start_value_index = 7 + split_index
        baseline_predictions = _baseline_predictions(
            values, test_start_value_index
        )
        metrics = compare_forecasting_methods(
            actual, baseline_predictions, ml_predictions
        )
        artifact = {
            "signature": signature,
            "model": model,
            "metrics": metrics,
            "training_rows": len(train_rows),
            "test_rows": len(test_rows),
        }
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(artifact, path)
    model = artifact["model"]
    predictions = _recursive_forecast(model, daily_sales, forecast_days)
    return {
        "available": True,
        "method": "Random Forest Regression",
        "daily_forecast": predictions,
        "total_forecast": sum(predictions),
        "mae": artifact["metrics"]["ml_mae"],
        "rmse": artifact["metrics"]["ml_rmse"],
        "baseline_mae": artifact["metrics"]["baseline_mae"],
        "baseline_rmse": artifact["metrics"]["baseline_rmse"],
        "preferred_method": artifact["metrics"]["preferred_method"],
        "training_rows": artifact["training_rows"],
        "test_rows": artifact["test_rows"],
    }
