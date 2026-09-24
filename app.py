"""Application entry point for the Inventory-AI Flask project."""

import click
import mysql.connector
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, session, url_for
from flask import request
from werkzeug.security import check_password_hash, generate_password_hash

# Load values from a local .env file when one exists.
# The real .env file is ignored by Git; .env.example documents the required keys.
load_dotenv()

from config import Config
from utils.forecasting import (
    calculate_reorder_recommendation,
    forecast_demand,
    get_daily_product_sales,
)
from utils.helpers import get_db_connection


app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    """Show the public landing page."""
    return render_template(
        "base.html",
        page_title="Inventory-AI",
        home_page=True,
        logged_in=bool(session.get("user_id")),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate an active user and store only safe identity data in session."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("login.html", page_title="Login")

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT user_id, full_name, username, password_hash, role, is_active
                FROM users
                WHERE username = %s
                LIMIT 1
                """,
                (username,),
            )
            user = cursor.fetchone()
        except mysql.connector.Error:
            # Do not expose connection details or credentials to the browser.
            app.logger.exception("Database error while attempting user login")
            flash("The login service is temporarily unavailable. Please try again.", "error")
            return render_template("login.html", page_title="Login")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        password_valid = False
        if user is not None and user["is_active"]:
            try:
                password_valid = check_password_hash(user["password_hash"], password)
            except ValueError:
                # Treat a malformed legacy hash as invalid without exposing details.
                password_valid = False

        if not password_valid:
            flash("Invalid username or password, or the account is inactive.", "error")
            return render_template("login.html", page_title="Login")

        # Clear any old session data before establishing the new login session.
        session.clear()
        session["user_id"] = user["user_id"]
        session["full_name"] = user["full_name"]
        session["role"] = user["role"]
        return redirect(url_for("dashboard"))

    return render_template("login.html", page_title="Login")


@app.route("/dashboard")
def dashboard():
    """Display the authenticated inventory and activity overview."""
    if "user_id" not in session:
        flash("Please log in to access the dashboard.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    dashboard_data = {
        "summary": {
            "total_products": 0,
            "total_categories": 0,
            "total_suppliers": 0,
            "low_stock_items": 0,
            "out_of_stock": 0,
            "inventory_value": Decimal("0.00"),
            "todays_sales": Decimal("0.00"),
            "todays_purchases": Decimal("0.00"),
        },
        "low_stock_products": [],
        "recent_sales": [],
        "recent_purchases": [],
        "recent_activity": [],
    }
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM products WHERE is_active = TRUE) AS total_products,
                (SELECT COUNT(*) FROM categories) AS total_categories,
                (SELECT COUNT(*) FROM suppliers) AS total_suppliers,
                (SELECT COUNT(*) FROM products
                 WHERE is_active = TRUE
                   AND current_stock > 0
                   AND current_stock <= reorder_level) AS low_stock_items,
                (SELECT COUNT(*) FROM products
                 WHERE is_active = TRUE AND current_stock <= 0) AS out_of_stock,
                COALESCE((SELECT SUM(current_stock * cost_price)
                          FROM products WHERE is_active = TRUE), 0) AS inventory_value,
                COALESCE((SELECT SUM(total_amount) FROM sales
                          WHERE status = 'completed'
                            AND DATE(sale_date) = CURDATE()), 0) AS todays_sales,
                COALESCE((SELECT SUM(total_amount) FROM purchases
                          WHERE status = 'received'
                            AND DATE(purchase_date) = CURDATE()), 0) AS todays_purchases
            """
        )
        dashboard_data["summary"] = cursor.fetchone()

        cursor.execute(
            """
            SELECT product_id, product_name, sku, current_stock,
                   reorder_level, unit
            FROM products
            WHERE is_active = TRUE
              AND current_stock <= reorder_level
            ORDER BY current_stock ASC, product_name ASC
            LIMIT 10
            """
        )
        dashboard_data["low_stock_products"] = cursor.fetchall()

        cursor.execute(
            """
            SELECT sale_id, customer_name, sale_date, total_amount,
                   payment_method, status
            FROM sales
            WHERE status = 'completed'
            ORDER BY sale_date DESC, sale_id DESC
            LIMIT 5
            """
        )
        dashboard_data["recent_sales"] = cursor.fetchall()

        cursor.execute(
            """
            SELECT p.purchase_id, p.invoice_number, p.purchase_date,
                   p.total_amount, p.status, s.supplier_name
            FROM purchases AS p
            INNER JOIN suppliers AS s ON p.supplier_id = s.supplier_id
            WHERE p.status = 'received'
            ORDER BY p.purchase_date DESC, p.purchase_id DESC
            LIMIT 5
            """
        )
        dashboard_data["recent_purchases"] = cursor.fetchall()

        cursor.execute(
            """
            SELECT st.transaction_id, st.transaction_date,
                   st.transaction_type, st.quantity, st.balance_after,
                   p.product_name, p.sku, u.full_name
            FROM stock_transactions AS st
            INNER JOIN products AS p ON st.product_id = p.product_id
            INNER JOIN users AS u ON st.user_id = u.user_id
            ORDER BY st.transaction_date DESC, st.transaction_id DESC
            LIMIT 8
            """
        )
        dashboard_data["recent_activity"] = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading dashboard")
        flash("Dashboard data is temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        full_name=session["full_name"],
        role=session["role"],
        **dashboard_data,
    )


@app.get("/forecast")
def forecast():
    """Display a baseline demand forecast for one active product."""
    if "user_id" not in session:
        flash("Please log in to access demand forecasting.", "error")
        return redirect(url_for("login"))

    selected_product_id = request.args.get("product_id", "").strip()
    product_id = None
    if selected_product_id:
        try:
            product_id = int(selected_product_id)
            if product_id <= 0:
                raise ValueError
        except ValueError:
            flash("Please select a valid product.", "error")

    connection = None
    cursor = None
    products = []
    product = None
    historical_sales = []
    forecast_result = None
    recommendation = None
    has_sales_history = False
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT product_id, product_name, sku, current_stock, reorder_level
            FROM products
            WHERE is_active = TRUE
            ORDER BY product_name ASC
            """
        )
        products = cursor.fetchall()

        if product_id is not None:
            cursor.execute(
                """
                SELECT product_id, product_name, sku, current_stock, reorder_level
                FROM products
                WHERE product_id = %s AND is_active = TRUE
                LIMIT 1
                """,
                (product_id,),
            )
            product = cursor.fetchone()
            if product is None:
                flash("The selected product is not available.", "error")
            else:
                daily_sales = get_daily_product_sales(
                    connection, product["product_id"], days=90
                )
                has_sales_history = any(
                    observation["quantity"] > 0 for observation in daily_sales
                )
                if has_sales_history:
                    forecast_result = forecast_demand(
                        daily_sales, forecast_days=7, window=7
                    )
                    recommendation = calculate_reorder_recommendation(
                        product["current_stock"],
                        forecast_result["total_forecast"],
                        product["reorder_level"],
                    )
                    historical_sales = daily_sales[-30:]
    except mysql.connector.Error:
        app.logger.exception("Database error while loading demand forecast")
        flash("Forecast data is temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "forecast.html",
        page_title="Demand Forecast",
        products=products,
        product=product,
        historical_sales=historical_sales,
        forecast_result=forecast_result,
        recommendation=recommendation,
        has_sales_history=has_sales_history,
        product_selected=bool(selected_product_id),
    )


@app.get("/categories")
def categories():
    """Display all categories for authenticated users."""
    if "user_id" not in session:
        flash("Please log in to access categories.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT category_id, category_name, description, created_at
            FROM categories
            ORDER BY category_name ASC
            """
        )
        category_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading categories")
        flash("Categories are temporarily unavailable. Please try again.", "error")
        category_rows = []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "categories.html",
        page_title="Category Management",
        categories=category_rows,
    )


@app.route("/categories/add", methods=["GET", "POST"])
def add_category():
    """Display and process the add-category form."""
    if "user_id" not in session:
        flash("Please log in to manage categories.", "error")
        return redirect(url_for("login"))

    category = {
        "category_name": "",
        "description": "",
    }
    if request.method == "POST":
        category["category_name"] = request.form.get("category_name", "").strip()
        category["description"] = request.form.get("description", "").strip()
        validation_error = validate_category_input(category)
        if validation_error:
            flash(validation_error, "error")
            return render_template(
                "category_form.html",
                page_title="Add Category",
                form_title="Add Category",
                submit_label="Add Category",
                category=category,
            )

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO categories (category_name, description)
                VALUES (%s, %s)
                """,
                (category["category_name"], category["description"] or None),
            )
            connection.commit()
        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()
            flash("A category with that name already exists.", "error")
            return render_template(
                "category_form.html",
                page_title="Add Category",
                form_title="Add Category",
                submit_label="Add Category",
                category=category,
            )
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while adding category")
            flash("The category could not be added. Please try again.", "error")
            return render_template(
                "category_form.html",
                page_title="Add Category",
                form_title="Add Category",
                submit_label="Add Category",
                category=category,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Category added successfully.", "success")
        return redirect(url_for("categories"))

    return render_template(
        "category_form.html",
        page_title="Add Category",
        form_title="Add Category",
        submit_label="Add Category",
        category=category,
    )


@app.route("/categories/edit/<int:category_id>", methods=["GET", "POST"])
def edit_category(category_id):
    """Display and process the edit form for one category."""
    if "user_id" not in session:
        flash("Please log in to manage categories.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT category_id, category_name, description
            FROM categories
            WHERE category_id = %s
            """,
            (category_id,),
        )
        category = cursor.fetchone()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading category %s", category_id)
        flash("The category could not be loaded. Please try again.", "error")
        return redirect(url_for("categories"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    if category is None:
        flash("Category not found.", "error")
        return redirect(url_for("categories"))

    if request.method == "POST":
        category["category_name"] = request.form.get("category_name", "").strip()
        category["description"] = request.form.get("description", "").strip()
        validation_error = validate_category_input(category)
        if validation_error:
            flash(validation_error, "error")
            return render_template(
                "category_form.html",
                page_title="Edit Category",
                form_title="Edit Category",
                submit_label="Save Changes",
                category=category,
            )

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE categories
                SET category_name = %s, description = %s
                WHERE category_id = %s
                """,
                (
                    category["category_name"],
                    category["description"] or None,
                    category_id,
                ),
            )
            connection.commit()
        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()
            flash("A category with that name already exists.", "error")
            return render_template(
                "category_form.html",
                page_title="Edit Category",
                form_title="Edit Category",
                submit_label="Save Changes",
                category=category,
            )
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while editing category %s", category_id)
            flash("The category could not be updated. Please try again.", "error")
            return render_template(
                "category_form.html",
                page_title="Edit Category",
                form_title="Edit Category",
                submit_label="Save Changes",
                category=category,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Category updated successfully.", "success")
        return redirect(url_for("categories"))

    return render_template(
        "category_form.html",
        page_title="Edit Category",
        form_title="Edit Category",
        submit_label="Save Changes",
        category=category,
    )


@app.post("/categories/delete/<int:category_id>")
def delete_category(category_id):
    """Delete a category when no products depend on it."""
    if "user_id" not in session:
        flash("Please log in to manage categories.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM categories WHERE category_id = %s",
            (category_id,),
        )
        if cursor.rowcount == 0:
            flash("Category not found.", "error")
        else:
            connection.commit()
            flash("Category deleted successfully.", "success")
            return redirect(url_for("categories"))
    except mysql.connector.IntegrityError:
        if connection is not None:
            connection.rollback()
        flash(
            "This category cannot be deleted because products are using it.",
            "error",
        )
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while deleting category %s", category_id)
        flash("The category could not be deleted. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("categories"))


def validate_category_input(category):
    """Return a friendly validation message, or None when input is valid."""
    if not category["category_name"]:
        return "Category name is required."
    if len(category["category_name"]) > 100:
        return "Category name must be 100 characters or fewer."
    if len(category["description"]) > 255:
        return "Description must be 255 characters or fewer."
    return None


@app.get("/suppliers")
def suppliers():
    """Display suppliers, optionally filtered by a simple search term."""
    if "user_id" not in session:
        flash("Please log in to access suppliers.", "error")
        return redirect(url_for("login"))

    search_term = request.args.get("search", "").strip()
    connection = None
    cursor = None
    supplier_rows = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        if search_term:
            search_pattern = f"%{search_term}%"
            cursor.execute(
                """
                SELECT supplier_id, supplier_name, contact_person, email,
                       phone, address, created_at
                FROM suppliers
                WHERE supplier_name LIKE %s
                   OR contact_person LIKE %s
                   OR email LIKE %s
                   OR phone LIKE %s
                ORDER BY supplier_name ASC
                """,
                (search_pattern, search_pattern, search_pattern, search_pattern),
            )
        else:
            cursor.execute(
                """
                SELECT supplier_id, supplier_name, contact_person, email,
                       phone, address, created_at
                FROM suppliers
                ORDER BY supplier_name ASC
                """
            )
        supplier_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading suppliers")
        flash("Suppliers are temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "suppliers.html",
        page_title="Supplier Management",
        suppliers=supplier_rows,
        search_term=search_term,
    )


@app.route("/suppliers/add", methods=["GET", "POST"])
def add_supplier():
    """Display and process the add-supplier form."""
    if "user_id" not in session:
        flash("Please log in to manage suppliers.", "error")
        return redirect(url_for("login"))

    supplier = empty_supplier()
    if request.method == "POST":
        supplier = supplier_from_form()
        validation_error = validate_supplier_input(supplier)
        if validation_error:
            flash(validation_error, "error")
            return render_supplier_form(supplier, "Add Supplier", "Add Supplier")

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO suppliers
                    (supplier_name, contact_person, email, phone, address)
                VALUES (%s, %s, %s, %s, %s)
                """,
                supplier_values(supplier),
            )
            connection.commit()
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while adding supplier")
            flash("The supplier could not be added. Please try again.", "error")
            return render_supplier_form(supplier, "Add Supplier", "Add Supplier")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Supplier added successfully.", "success")
        return redirect(url_for("suppliers"))

    return render_supplier_form(supplier, "Add Supplier", "Add Supplier")


@app.route("/suppliers/edit/<int:supplier_id>", methods=["GET", "POST"])
def edit_supplier(supplier_id):
    """Display and process the edit form for one supplier."""
    if "user_id" not in session:
        flash("Please log in to manage suppliers.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT supplier_id, supplier_name, contact_person, email, phone, address
            FROM suppliers
            WHERE supplier_id = %s
            """,
            (supplier_id,),
        )
        supplier = cursor.fetchone()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading supplier %s", supplier_id)
        flash("The supplier could not be loaded. Please try again.", "error")
        return redirect(url_for("suppliers"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    if supplier is None:
        flash("Supplier not found.", "error")
        return redirect(url_for("suppliers"))

    if request.method == "POST":
        supplier.update(supplier_from_form())
        validation_error = validate_supplier_input(supplier)
        if validation_error:
            flash(validation_error, "error")
            return render_supplier_form(supplier, "Edit Supplier", "Save Changes")

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE suppliers
                SET supplier_name = %s, contact_person = %s, email = %s,
                    phone = %s, address = %s
                WHERE supplier_id = %s
                """,
                supplier_values(supplier) + (supplier_id,),
            )
            connection.commit()
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while editing supplier %s", supplier_id)
            flash("The supplier could not be updated. Please try again.", "error")
            return render_supplier_form(supplier, "Edit Supplier", "Save Changes")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Supplier updated successfully.", "success")
        return redirect(url_for("suppliers"))

    return render_supplier_form(supplier, "Edit Supplier", "Save Changes")


@app.post("/suppliers/delete/<int:supplier_id>")
def delete_supplier(supplier_id):
    """Delete a supplier only when no purchase records reference it."""
    if "user_id" not in session:
        flash("Please log in to manage suppliers.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM suppliers WHERE supplier_id = %s",
            (supplier_id,),
        )
        if cursor.rowcount == 0:
            flash("Supplier not found.", "error")
        else:
            connection.commit()
            flash("Supplier deleted successfully.", "success")
            return redirect(url_for("suppliers"))
    except mysql.connector.IntegrityError:
        if connection is not None:
            connection.rollback()
        flash(
            "This supplier cannot be deleted because purchase records are associated with it.",
            "error",
        )
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while deleting supplier %s", supplier_id)
        flash("The supplier could not be deleted. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("suppliers"))


def empty_supplier():
    """Return the fields used by the supplier form."""
    return {
        "supplier_name": "",
        "contact_person": "",
        "email": "",
        "phone": "",
        "address": "",
    }


def supplier_from_form():
    """Read and trim supplier fields submitted by the browser."""
    return {
        "supplier_name": request.form.get("supplier_name", "").strip(),
        "contact_person": request.form.get("contact_person", "").strip(),
        "email": request.form.get("email", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "address": request.form.get("address", "").strip(),
    }


def supplier_values(supplier):
    """Return supplier fields in database column order."""
    return (
        supplier["supplier_name"],
        supplier["contact_person"] or None,
        supplier["email"] or None,
        supplier["phone"] or None,
        supplier["address"] or None,
    )


def validate_supplier_input(supplier):
    """Return a friendly validation message, or None when input is valid."""
    limits = (
        ("supplier_name", 150, "Supplier name"),
        ("contact_person", 100, "Contact person"),
        ("email", 150, "Email"),
        ("phone", 30, "Phone"),
        ("address", 255, "Address"),
    )
    if not supplier["supplier_name"]:
        return "Supplier name is required."
    for field_name, maximum, label in limits:
        if len(supplier[field_name]) > maximum:
            return f"{label} must be {maximum} characters or fewer."
    if supplier["email"] and not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", supplier["email"]
    ):
        return "Please enter a valid email address."
    return None


def render_supplier_form(supplier, form_title, submit_label):
    """Render the shared add/edit supplier form."""
    return render_template(
        "supplier_form.html",
        page_title=form_title,
        form_title=form_title,
        submit_label=submit_label,
        supplier=supplier,
    )


@app.get("/products")
def products():
    """Display products with optional text search and active-status filtering."""
    if "user_id" not in session:
        flash("Please log in to access products.", "error")
        return redirect(url_for("login"))

    search_term = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip().lower()
    if status_filter not in {"active", "inactive"}:
        status_filter = ""

    connection = None
    cursor = None
    product_rows = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        conditions = []
        parameters = []
        if search_term:
            search_pattern = f"%{search_term}%"
            conditions.append(
                """
                (p.product_name LIKE %s OR p.sku LIKE %s
                 OR c.category_name LIKE %s OR s.supplier_name LIKE %s)
                """
            )
            parameters.extend(
                [search_pattern, search_pattern, search_pattern, search_pattern]
            )
        if status_filter:
            conditions.append("p.is_active = %s")
            parameters.append(status_filter == "active")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(
            f"""
            SELECT p.product_id, p.product_name, p.sku, p.description, p.unit,
                   p.cost_price, p.selling_price, p.current_stock,
                   p.reorder_level, p.is_active, p.created_at,
                   c.category_name, s.supplier_name
            FROM products AS p
            INNER JOIN categories AS c ON p.category_id = c.category_id
            LEFT JOIN suppliers AS s ON p.supplier_id = s.supplier_id
            {where_clause}
            ORDER BY p.product_name ASC
            """,
            tuple(parameters),
        )
        product_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading products")
        flash("Products are temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "products.html",
        page_title="Product Management",
        products=product_rows,
        search_term=search_term,
        status_filter=status_filter,
    )


@app.route("/products/add", methods=["GET", "POST"])
def add_product():
    """Display and process the add-product form."""
    if "user_id" not in session:
        flash("Please log in to manage products.", "error")
        return redirect(url_for("login"))

    product = empty_product()
    if request.method == "POST":
        product = product_from_form()
        validation_error, warning = validate_product_input(product)
        if validation_error:
            flash(validation_error, "error")
            return render_product_form(product, "Add Product", "Add Product")
        if warning:
            flash(warning, "warning")

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            relationship_error = validate_product_relationships(cursor, product)
            if relationship_error:
                flash(relationship_error, "error")
                return render_product_form(product, "Add Product", "Add Product")
            cursor.execute(
                """
                INSERT INTO products
                    (category_id, supplier_id, product_name, sku, description,
                     unit, cost_price, selling_price, current_stock,
                     reorder_level, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                product_values(product),
            )
            connection.commit()
        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()
            flash("A product with this SKU already exists.", "error")
            return render_product_form(product, "Add Product", "Add Product")
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while adding product")
            flash("The product could not be added. Please try again.", "error")
            return render_product_form(product, "Add Product", "Add Product")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Product added successfully.", "success")
        return redirect(url_for("products"))

    return render_product_form(product, "Add Product", "Add Product")


@app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id):
    """Display and process the edit form for one product."""
    if "user_id" not in session:
        flash("Please log in to manage products.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT product_id, category_id, supplier_id, product_name, sku,
                   description, unit, cost_price, selling_price, current_stock,
                   reorder_level, is_active
            FROM products
            WHERE product_id = %s
            """,
            (product_id,),
        )
        product = cursor.fetchone()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading product %s", product_id)
        flash("The product could not be loaded. Please try again.", "error")
        return redirect(url_for("products"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    if product is None:
        flash("Product not found.", "error")
        return redirect(url_for("products"))

    if request.method == "POST":
        submitted_product = product_from_form()
        product.update(submitted_product)
        validation_error, warning = validate_product_input(product)
        if validation_error:
            flash(validation_error, "error")
            return render_product_form(product, "Edit Product", "Save Changes")
        if warning:
            flash(warning, "warning")

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            relationship_error = validate_product_relationships(cursor, product)
            if relationship_error:
                flash(relationship_error, "error")
                return render_product_form(product, "Edit Product", "Save Changes")
            cursor.execute(
                """
                UPDATE products
                SET category_id = %s, supplier_id = %s, product_name = %s,
                    sku = %s, description = %s, unit = %s, cost_price = %s,
                    selling_price = %s, current_stock = %s, reorder_level = %s
                WHERE product_id = %s
                """,
                product_values(product) + (product_id,),
            )
            connection.commit()
        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()
            flash("A product with this SKU already exists.", "error")
            return render_product_form(product, "Edit Product", "Save Changes")
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while editing product %s", product_id)
            flash("The product could not be updated. Please try again.", "error")
            return render_product_form(product, "Edit Product", "Save Changes")
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Product updated successfully.", "success")
        return redirect(url_for("products"))

    return render_product_form(product, "Edit Product", "Save Changes")


@app.post("/products/toggle/<int:product_id>")
def toggle_product(product_id):
    """Activate or deactivate a product without deleting its history."""
    if "user_id" not in session:
        flash("Please log in to manage products.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT is_active FROM products WHERE product_id = %s",
            (product_id,),
        )
        product = cursor.fetchone()
        if product is None:
            flash("Product not found.", "error")
            return redirect(url_for("products"))

        new_status = not product["is_active"]
        cursor.execute(
            "UPDATE products SET is_active = %s WHERE product_id = %s",
            (new_status, product_id),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while toggling product %s", product_id)
        flash("The product status could not be changed. Please try again.", "error")
        return redirect(url_for("products"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    flash("Product activated." if new_status else "Product deactivated.", "success")
    return redirect(url_for("products"))


def empty_product():
    """Return the fields used by the product form."""
    return {
        "product_name": "",
        "sku": "",
        "category_id": "",
        "supplier_id": "",
        "description": "",
        "unit": "piece",
        "cost_price": "",
        "selling_price": "",
        "current_stock": "0",
        "reorder_level": "0",
    }


def product_from_form():
    """Read product fields from the browser and trim text inputs."""
    return {
        "product_name": request.form.get("product_name", "").strip(),
        "sku": request.form.get("sku", "").strip(),
        "category_id": request.form.get("category_id", "").strip(),
        "supplier_id": request.form.get("supplier_id", "").strip(),
        "description": request.form.get("description", "").strip(),
        "unit": request.form.get("unit", "").strip(),
        "cost_price": request.form.get("cost_price", "").strip(),
        "selling_price": request.form.get("selling_price", "").strip(),
        "current_stock": request.form.get("current_stock", "").strip(),
        "reorder_level": request.form.get("reorder_level", "").strip(),
    }


def validate_product_input(product):
    """Return an error and optional warning for product form values."""
    text_limits = (
        ("product_name", 150, "Product name"),
        ("sku", 50, "SKU"),
        ("description", 500, "Description"),
        ("unit", 30, "Unit"),
    )
    if not product["product_name"]:
        return "Product name is required.", None
    if not product["sku"]:
        return "SKU is required.", None
    for field_name, maximum, label in text_limits:
        if len(product[field_name]) > maximum:
            return f"{label} must be {maximum} characters or fewer.", None

    parsed_prices = []
    for field_name, label in (
        ("cost_price", "Cost price"),
        ("selling_price", "Selling price"),
    ):
        try:
            value = Decimal(product[field_name])
        except (InvalidOperation, TypeError):
            return f"{label} must be a valid number.", None
        if not value.is_finite() or value < 0 or value.as_tuple().exponent < -2:
            return f"{label} must be non-negative with at most 2 decimal places.", None
        parsed_prices.append(value)

    for field_name, label in (
        ("current_stock", "Current stock"),
        ("reorder_level", "Reorder level"),
    ):
        try:
            value = int(product[field_name])
        except (TypeError, ValueError):
            return f"{label} must be a non-negative integer.", None
        if value < 0 or str(value) != product[field_name]:
            return f"{label} must be a non-negative integer.", None

    product["_cost_price"] = parsed_prices[0]
    product["_selling_price"] = parsed_prices[1]
    if parsed_prices[1] < parsed_prices[0]:
        return None, "Warning: Selling price is lower than cost price."
    return None, None


def validate_product_relationships(cursor, product):
    """Verify submitted category and optional supplier IDs exist."""
    try:
        category_id = int(product["category_id"])
    except (TypeError, ValueError):
        return "Please select a valid category."
    cursor.execute(
        "SELECT category_id FROM categories WHERE category_id = %s",
        (category_id,),
    )
    if cursor.fetchone() is None:
        return "Please select an existing category."
    product["_category_id"] = category_id

    supplier_id = None
    if product["supplier_id"]:
        try:
            supplier_id = int(product["supplier_id"])
        except (TypeError, ValueError):
            return "Please select a valid supplier."
        cursor.execute(
            "SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
            (supplier_id,),
        )
        if cursor.fetchone() is None:
            return "Please select an existing supplier."
    product["_supplier_id"] = supplier_id
    return None


def product_values(product):
    """Return validated product fields in database column order."""
    return (
        product["_category_id"],
        product["_supplier_id"],
        product["product_name"],
        product["sku"],
        product["description"] or None,
        product["unit"] or None,
        product["_cost_price"],
        product["_selling_price"],
        int(product["current_stock"]),
        int(product["reorder_level"]),
    )


def load_product_options():
    """Load categories and suppliers for the product form dropdowns."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT category_id, category_name FROM categories ORDER BY category_name"
        )
        categories = cursor.fetchall()
        cursor.execute(
            "SELECT supplier_id, supplier_name FROM suppliers ORDER BY supplier_name"
        )
        suppliers = cursor.fetchall()
        return categories, suppliers
    except mysql.connector.Error:
        app.logger.exception("Database error while loading product form options")
        return [], []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def render_product_form(product, form_title, submit_label):
    """Render the shared add/edit product form."""
    categories, suppliers = load_product_options()
    return render_template(
        "product_form.html",
        page_title=form_title,
        form_title=form_title,
        submit_label=submit_label,
        product=product,
        categories=categories,
        suppliers=suppliers,
    )


@app.get("/purchases")
def purchases():
    """Display purchases with optional invoice or supplier search."""
    if "user_id" not in session:
        flash("Please log in to access purchases.", "error")
        return redirect(url_for("login"))

    search_term = request.args.get("search", "").strip()
    connection = None
    cursor = None
    purchase_rows = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        if search_term:
            pattern = f"%{search_term}%"
            cursor.execute(
                """
                SELECT p.purchase_id, p.invoice_number, p.purchase_date,
                       p.total_amount, p.status, p.created_at,
                       s.supplier_name, u.full_name
                FROM purchases AS p
                INNER JOIN suppliers AS s ON p.supplier_id = s.supplier_id
                INNER JOIN users AS u ON p.user_id = u.user_id
                WHERE p.invoice_number LIKE %s OR s.supplier_name LIKE %s
                ORDER BY p.purchase_date DESC, p.purchase_id DESC
                """,
                (pattern, pattern),
            )
        else:
            cursor.execute(
                """
                SELECT p.purchase_id, p.invoice_number, p.purchase_date,
                       p.total_amount, p.status, p.created_at,
                       s.supplier_name, u.full_name
                FROM purchases AS p
                INNER JOIN suppliers AS s ON p.supplier_id = s.supplier_id
                INNER JOIN users AS u ON p.user_id = u.user_id
                ORDER BY p.purchase_date DESC, p.purchase_id DESC
                """
            )
        purchase_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading purchases")
        flash("Purchases are temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "purchases.html",
        page_title="Purchase Management",
        purchases=purchase_rows,
        search_term=search_term,
    )


@app.route("/purchases/add", methods=["GET", "POST"])
def add_purchase():
    """Display and atomically create a pending purchase with its items."""
    if "user_id" not in session:
        flash("Please log in to manage purchases.", "error")
        return redirect(url_for("login"))

    form_data = empty_purchase_form()
    if request.method == "POST":
        form_data = purchase_form_from_request()
        validation_error, purchase_items, total_amount = validate_purchase_form(form_data)
        if validation_error:
            flash(validation_error, "error")
            return render_purchase_form(form_data)

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                "SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
                (form_data["supplier_id"],),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                flash("Please select an existing supplier.", "error")
                return render_purchase_form(form_data)

            verified_items = []
            for item in purchase_items:
                cursor.execute(
                    """
                    SELECT product_id, product_name, sku
                    FROM products
                    WHERE product_id = %s AND is_active = TRUE
                    """,
                    (item["product_id"],),
                )
                product = cursor.fetchone()
                if product is None:
                    connection.rollback()
                    flash(
                        "Every selected product must exist and be active.",
                        "error",
                    )
                    return render_purchase_form(form_data)
                verified_items.append((item, product))

            cursor.execute(
                """
                INSERT INTO purchases
                    (supplier_id, user_id, purchase_date, invoice_number,
                     total_amount, status, notes)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                """,
                (
                    int(form_data["supplier_id"]),
                    session["user_id"],
                    form_data["purchase_date"],
                    form_data["invoice_number"],
                    total_amount,
                    form_data["notes"] or None,
                ),
            )
            purchase_id = cursor.lastrowid
            for item, _product in verified_items:
                cursor.execute(
                    """
                    INSERT INTO purchase_items
                        (purchase_id, product_id, quantity, unit_cost, line_total)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        purchase_id,
                        item["product_id"],
                        item["quantity"],
                        item["unit_cost"],
                        item["line_total"],
                    ),
                )
            connection.commit()
        except mysql.connector.IntegrityError:
            if connection is not None:
                connection.rollback()
            flash("A purchase with this invoice number already exists.", "error")
            return render_purchase_form(form_data)
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while creating purchase")
            flash("The purchase could not be created. Please try again.", "error")
            return render_purchase_form(form_data)
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Purchase created as pending.", "success")
        return redirect(url_for("purchase_detail", purchase_id=purchase_id))

    return render_purchase_form(form_data)


@app.get("/purchases/<int:purchase_id>")
def purchase_detail(purchase_id):
    """Display one purchase and its line items."""
    if "user_id" not in session:
        flash("Please log in to access purchases.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT p.purchase_id, p.invoice_number, p.purchase_date,
                   p.total_amount, p.status, p.notes, p.created_at,
                   s.supplier_name, u.full_name
            FROM purchases AS p
            INNER JOIN suppliers AS s ON p.supplier_id = s.supplier_id
            INNER JOIN users AS u ON p.user_id = u.user_id
            WHERE p.purchase_id = %s
            """,
            (purchase_id,),
        )
        purchase = cursor.fetchone()
        if purchase is None:
            flash("Purchase not found.", "error")
            return redirect(url_for("purchases"))
        cursor.execute(
            """
            SELECT pi.product_id, pi.quantity, pi.unit_cost, pi.line_total,
                   pr.product_name, pr.sku
            FROM purchase_items AS pi
            INNER JOIN products AS pr ON pi.product_id = pr.product_id
            WHERE pi.purchase_id = %s
            ORDER BY pi.purchase_item_id
            """,
            (purchase_id,),
        )
        items = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading purchase %s", purchase_id)
        flash("The purchase could not be loaded. Please try again.", "error")
        return redirect(url_for("purchases"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "purchase_detail.html",
        page_title="Purchase Details",
        purchase=purchase,
        items=items,
    )


@app.post("/purchases/<int:purchase_id>/receive")
def receive_purchase(purchase_id):
    """Receive a pending purchase in one atomic stock transaction."""
    if "user_id" not in session:
        flash("Please log in to manage purchases.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT purchase_id, status
            FROM purchases
            WHERE purchase_id = %s
            FOR UPDATE
            """,
            (purchase_id,),
        )
        purchase = cursor.fetchone()
        if purchase is None:
            connection.rollback()
            flash("Purchase not found.", "error")
            return redirect(url_for("purchases"))
        if purchase["status"] == "received":
            connection.rollback()
            flash("Purchase has already been received.", "error")
            return redirect(url_for("purchase_detail", purchase_id=purchase_id))
        if purchase["status"] == "cancelled":
            connection.rollback()
            flash("Cancelled purchases cannot be received.", "error")
            return redirect(url_for("purchase_detail", purchase_id=purchase_id))

        cursor.execute(
            """
            SELECT product_id, quantity
            FROM purchase_items
            WHERE purchase_id = %s
            ORDER BY purchase_item_id
            """,
            (purchase_id,),
        )
        items = cursor.fetchall()
        if not items:
            connection.rollback()
            flash("This purchase has no items and cannot be received.", "error")
            return redirect(url_for("purchase_detail", purchase_id=purchase_id))

        for item in items:
            cursor.execute(
                """
                SELECT current_stock
                FROM products
                WHERE product_id = %s
                FOR UPDATE
                """,
                (item["product_id"],),
            )
            product = cursor.fetchone()
            if product is None:
                raise mysql.connector.Error("Purchase item product no longer exists")
            new_stock = Decimal(str(product["current_stock"])) + Decimal(
                str(item["quantity"])
            )
            cursor.execute(
                """
                UPDATE products
                SET current_stock = %s
                WHERE product_id = %s
                """,
                (new_stock, item["product_id"]),
            )
            cursor.execute(
                """
                INSERT INTO stock_transactions
                    (product_id, user_id, purchase_id, transaction_type,
                     quantity, balance_after, notes)
                VALUES (%s, %s, %s, 'purchase', %s, %s, %s)
                """,
                (
                    item["product_id"],
                    session["user_id"],
                    purchase_id,
                    item["quantity"],
                    new_stock,
                    f"Stock received for purchase #{purchase_id}",
                ),
            )

        cursor.execute(
            """
            UPDATE purchases
            SET status = 'received'
            WHERE purchase_id = %s
            """,
            (purchase_id,),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while receiving purchase %s", purchase_id)
        flash("The purchase could not be received. No stock was changed.", "error")
        return redirect(url_for("purchase_detail", purchase_id=purchase_id))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    flash("Purchase received and stock updated.", "success")
    return redirect(url_for("purchase_detail", purchase_id=purchase_id))


@app.post("/purchases/<int:purchase_id>/cancel")
def cancel_purchase(purchase_id):
    """Cancel a pending purchase without changing stock."""
    if "user_id" not in session:
        flash("Please log in to manage purchases.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT status
            FROM purchases
            WHERE purchase_id = %s
            FOR UPDATE
            """,
            (purchase_id,),
        )
        purchase = cursor.fetchone()
        if purchase is None:
            flash("Purchase not found.", "error")
        elif purchase["status"] == "received":
            flash("Received purchases cannot be cancelled.", "error")
        elif purchase["status"] == "cancelled":
            flash("This purchase is already cancelled.", "error")
        else:
            cursor.execute(
                "UPDATE purchases SET status = 'cancelled' WHERE purchase_id = %s",
                (purchase_id,),
            )
            connection.commit()
            flash("Purchase cancelled.", "success")
            return redirect(url_for("purchase_detail", purchase_id=purchase_id))
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while cancelling purchase %s", purchase_id)
        flash("The purchase could not be cancelled. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return redirect(url_for("purchase_detail", purchase_id=purchase_id))


def empty_purchase_form():
    """Return the default values used by the purchase form."""
    return {
        "supplier_id": "",
        "invoice_number": "",
        "purchase_date": datetime.now().strftime("%Y-%m-%d"),
        "notes": "",
        "items": [{"product_id": "", "quantity": "", "unit_cost": ""}],
    }


def purchase_form_from_request():
    """Read header and repeated item fields from the purchase form."""
    product_ids = request.form.getlist("product_id[]")
    quantities = request.form.getlist("quantity[]")
    unit_costs = request.form.getlist("unit_cost[]")
    item_count = max(len(product_ids), len(quantities), len(unit_costs))
    items = []
    for index in range(item_count):
        items.append(
            {
                "product_id": product_ids[index] if index < len(product_ids) else "",
                "quantity": quantities[index] if index < len(quantities) else "",
                "unit_cost": unit_costs[index] if index < len(unit_costs) else "",
            }
        )
    return {
        "supplier_id": request.form.get("supplier_id", "").strip(),
        "invoice_number": request.form.get("invoice_number", "").strip(),
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "items": items or [{"product_id": "", "quantity": "", "unit_cost": ""}],
    }


def validate_purchase_form(form_data):
    """Validate purchase fields and calculate trusted item totals server-side."""
    if not form_data["supplier_id"]:
        return "Supplier is required.", [], Decimal("0.00")
    if not form_data["invoice_number"]:
        return "Invoice number is required.", [], Decimal("0.00")
    if len(form_data["invoice_number"]) > 80:
        return "Invoice number must be 80 characters or fewer.", [], Decimal("0.00")
    try:
        form_data["purchase_date"] = datetime.strptime(
            form_data["purchase_date"], "%Y-%m-%d"
        ).date()
    except (TypeError, ValueError):
        return "Please enter a valid purchase date.", [], Decimal("0.00")
    if not form_data["items"]:
        return "At least one purchase item is required.", [], Decimal("0.00")

    validated_items = []
    total_amount = Decimal("0.00")
    for item in form_data["items"]:
        if not item["product_id"]:
            return "Every purchase item must have a product.", [], Decimal("0.00")
        try:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
        except (TypeError, ValueError):
            return "Product IDs and quantities must be valid integers.", [], Decimal("0.00")
        if product_id <= 0 or quantity <= 0 or str(quantity) != item["quantity"]:
            return "Quantity must be a positive integer.", [], Decimal("0.00")
        try:
            unit_cost = Decimal(item["unit_cost"])
        except (InvalidOperation, TypeError):
            return "Unit cost must be a valid number.", [], Decimal("0.00")
        if not unit_cost.is_finite() or unit_cost < 0 or unit_cost.as_tuple().exponent < -2:
            return (
                "Unit cost must be non-negative with at most 2 decimal places.",
                [],
                Decimal("0.00"),
            )
        line_total = (Decimal(quantity) * unit_cost).quantize(Decimal("0.01"))
        total_amount += line_total
        validated_items.append(
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "line_total": line_total,
            }
        )
    form_data["items"] = validated_items
    return None, validated_items, total_amount.quantize(Decimal("0.01"))


def load_purchase_options():
    """Load suppliers and active products for the purchase form."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT supplier_id, supplier_name FROM suppliers ORDER BY supplier_name"
        )
        suppliers = cursor.fetchall()
        cursor.execute(
            """
            SELECT product_id, product_name, sku, current_stock
            FROM products
            WHERE is_active = TRUE
            ORDER BY product_name
            """
        )
        products = cursor.fetchall()
        return suppliers, products
    except mysql.connector.Error:
        app.logger.exception("Database error while loading purchase form options")
        return [], []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def render_purchase_form(form_data):
    """Render the purchase form with supplier and active-product options."""
    suppliers, products = load_purchase_options()
    return render_template(
        "purchase_form.html",
        page_title="New Purchase",
        suppliers=suppliers,
        products=products,
        purchase=form_data,
    )


@app.get("/sales")
def sales():
    """Display completed and cancelled sales with optional filters."""
    if "user_id" not in session:
        flash("Please log in to access sales.", "error")
        return redirect(url_for("login"))

    search_term = request.args.get("search", "").strip()
    payment_filter = request.args.get("payment_method", "").strip().lower()
    if payment_filter not in {"cash", "card", "upi", "other"}:
        payment_filter = ""

    connection = None
    cursor = None
    sale_rows = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        conditions = []
        parameters = []
        if search_term:
            pattern = f"%{search_term}%"
            conditions.append(
                "(CAST(s.sale_id AS CHAR) LIKE %s OR s.customer_name LIKE %s "
                "OR s.customer_phone LIKE %s)"
            )
            parameters.extend([pattern, pattern, pattern])
        if payment_filter:
            conditions.append("s.payment_method = %s")
            parameters.append(payment_filter)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(
            f"""
            SELECT s.sale_id, s.customer_name, s.customer_phone, s.sale_date,
                   s.total_amount, s.payment_method, s.status, s.created_at,
                   u.full_name
            FROM sales AS s
            INNER JOIN users AS u ON s.user_id = u.user_id
            {where_clause}
            ORDER BY s.sale_date DESC, s.sale_id DESC
            """,
            tuple(parameters),
        )
        sale_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading sales")
        flash("Sales are temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "sales.html",
        page_title="Sales Management",
        sales=sale_rows,
        search_term=search_term,
        payment_filter=payment_filter,
    )


@app.route("/sales/add", methods=["GET", "POST"])
def add_sale():
    """Create a completed sale and reduce stock atomically."""
    if "user_id" not in session:
        flash("Please log in to manage sales.", "error")
        return redirect(url_for("login"))

    form_data = empty_sale_form()
    if request.method == "POST":
        form_data = sale_form_from_request()
        validation_error, sale_items, total_amount = validate_sale_form(form_data)
        if validation_error:
            flash(validation_error, "error")
            return render_sale_form(form_data)

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                INSERT INTO sales
                    (user_id, customer_name, customer_phone, sale_date,
                     total_amount, payment_method, status, notes)
                VALUES (%s, %s, %s, %s, %s, %s, 'completed', %s)
                """,
                (
                    session["user_id"],
                    form_data["customer_name"] or None,
                    form_data["customer_phone"] or None,
                    form_data["sale_date"],
                    total_amount,
                    form_data["payment_method"],
                    form_data["notes"] or None,
                ),
            )
            sale_id = cursor.lastrowid

            for item in sale_items:
                cursor.execute(
                    """
                    SELECT product_id, product_name, current_stock, is_active
                    FROM products
                    WHERE product_id = %s
                    FOR UPDATE
                    """,
                    (item["product_id"],),
                )
                product = cursor.fetchone()
                if product is None or not product["is_active"]:
                    raise SaleValidationError(
                        "Every selected product must exist and be active."
                    )
                current_stock = Decimal(str(product["current_stock"]))
                if Decimal(item["quantity"]) > current_stock:
                    raise SaleValidationError(
                        f"Insufficient stock for {product['product_name']}. "
                        f"Available: {product['current_stock']}, "
                        f"requested: {item['quantity']}."
                    )
                new_stock = current_stock - Decimal(item["quantity"])
                cursor.execute(
                    """
                    UPDATE products SET current_stock = %s
                    WHERE product_id = %s
                    """,
                    (new_stock, item["product_id"]),
                )
                cursor.execute(
                    """
                    INSERT INTO sale_items
                        (sale_id, product_id, quantity, unit_price, line_total)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        sale_id,
                        item["product_id"],
                        item["quantity"],
                        item["unit_price"],
                        item["line_total"],
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO stock_transactions
                        (product_id, user_id, sale_id, transaction_type,
                         quantity, balance_after, notes)
                    VALUES (%s, %s, %s, 'sale', %s, %s, %s)
                    """,
                    (
                        item["product_id"],
                        session["user_id"],
                        sale_id,
                        item["quantity"],
                        new_stock,
                        f"Stock reduced for sale #{sale_id}",
                    ),
                )
            connection.commit()
        except SaleValidationError as error:
            if connection is not None:
                connection.rollback()
            flash(str(error), "error")
            return render_sale_form(form_data)
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while creating sale")
            flash("The sale could not be completed. No stock was changed.", "error")
            return render_sale_form(form_data)
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Sale completed and stock updated.", "success")
        return redirect(url_for("sale_detail", sale_id=sale_id))

    return render_sale_form(form_data)


@app.get("/sales/<int:sale_id>")
def sale_detail(sale_id):
    """Display one sale and its items."""
    if "user_id" not in session:
        flash("Please log in to access sales.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT s.sale_id, s.customer_name, s.customer_phone, s.sale_date,
                   s.total_amount, s.payment_method, s.status, s.notes,
                   u.full_name
            FROM sales AS s
            INNER JOIN users AS u ON s.user_id = u.user_id
            WHERE s.sale_id = %s
            """,
            (sale_id,),
        )
        sale = cursor.fetchone()
        if sale is None:
            flash("Sale not found.", "error")
            return redirect(url_for("sales"))
        cursor.execute(
            """
            SELECT si.quantity, si.unit_price, si.line_total,
                   p.product_name, p.sku
            FROM sale_items AS si
            INNER JOIN products AS p ON si.product_id = p.product_id
            WHERE si.sale_id = %s
            ORDER BY si.sale_item_id
            """,
            (sale_id,),
        )
        items = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading sale %s", sale_id)
        flash("The sale could not be loaded. Please try again.", "error")
        return redirect(url_for("sales"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "sale_detail.html",
        page_title="Sale Details",
        sale=sale,
        items=items,
    )


@app.post("/sales/<int:sale_id>/cancel")
def cancel_sale(sale_id):
    """Cancel a completed sale and restore stock atomically."""
    if "user_id" not in session:
        flash("Please log in to manage sales.", "error")
        return redirect(url_for("login"))

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT sale_id, status
            FROM sales
            WHERE sale_id = %s
            FOR UPDATE
            """,
            (sale_id,),
        )
        sale = cursor.fetchone()
        if sale is None:
            connection.rollback()
            flash("Sale not found.", "error")
            return redirect(url_for("sales"))
        if sale["status"] == "cancelled":
            connection.rollback()
            flash("Sale has already been cancelled.", "error")
            return redirect(url_for("sale_detail", sale_id=sale_id))

        cursor.execute(
            """
            SELECT product_id, quantity
            FROM sale_items
            WHERE sale_id = %s
            ORDER BY sale_item_id
            """,
            (sale_id,),
        )
        items = cursor.fetchall()
        for item in items:
            cursor.execute(
                """
                SELECT current_stock
                FROM products
                WHERE product_id = %s
                FOR UPDATE
                """,
                (item["product_id"],),
            )
            product = cursor.fetchone()
            if product is None:
                raise mysql.connector.Error("Sale item product no longer exists")
            new_stock = Decimal(str(product["current_stock"])) + Decimal(
                str(item["quantity"])
            )
            cursor.execute(
                "UPDATE products SET current_stock = %s WHERE product_id = %s",
                (new_stock, item["product_id"]),
            )
            cursor.execute(
                """
                INSERT INTO stock_transactions
                    (product_id, user_id, sale_id, transaction_type,
                     quantity, balance_after, notes)
                VALUES (%s, %s, %s, 'return', %s, %s, %s)
                """,
                (
                    item["product_id"],
                    session["user_id"],
                    sale_id,
                    item["quantity"],
                    new_stock,
                    f"Stock restored after cancelling sale #{sale_id}",
                ),
            )
        cursor.execute(
            "UPDATE sales SET status = 'cancelled' WHERE sale_id = %s",
            (sale_id,),
        )
        connection.commit()
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        app.logger.exception("Database error while cancelling sale %s", sale_id)
        flash("The sale could not be cancelled. No stock was changed.", "error")
        return redirect(url_for("sale_detail", sale_id=sale_id))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    flash("Sale cancelled and stock restored.", "success")
    return redirect(url_for("sale_detail", sale_id=sale_id))


class SaleValidationError(Exception):
    """Expected validation failure during an atomic sale transaction."""


def empty_sale_form():
    """Return default values used by the sale form."""
    return {
        "customer_name": "",
        "customer_phone": "",
        "sale_date": datetime.now().strftime("%Y-%m-%d"),
        "payment_method": "cash",
        "notes": "",
        "items": [{"product_id": "", "quantity": "", "unit_price": ""}],
    }


def sale_form_from_request():
    """Read sale header and repeated item fields from the form."""
    product_ids = request.form.getlist("product_id[]")
    quantities = request.form.getlist("quantity[]")
    unit_prices = request.form.getlist("unit_price[]")
    item_count = max(len(product_ids), len(quantities), len(unit_prices))
    items = []
    for index in range(item_count):
        items.append(
            {
                "product_id": product_ids[index] if index < len(product_ids) else "",
                "quantity": quantities[index] if index < len(quantities) else "",
                "unit_price": unit_prices[index] if index < len(unit_prices) else "",
            }
        )
    return {
        "customer_name": request.form.get("customer_name", "").strip(),
        "customer_phone": request.form.get("customer_phone", "").strip(),
        "sale_date": request.form.get("sale_date", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip().lower(),
        "notes": request.form.get("notes", "").strip(),
        "items": items or [{"product_id": "", "quantity": "", "unit_price": ""}],
    }


def validate_sale_form(form_data):
    """Validate sale fields and calculate trusted totals server-side."""
    if len(form_data["customer_name"]) > 100:
        return "Customer name must be 100 characters or fewer.", [], Decimal("0.00")
    if len(form_data["customer_phone"]) > 30:
        return "Customer phone must be 30 characters or fewer.", [], Decimal("0.00")
    if form_data["payment_method"] not in {"cash", "card", "upi", "other"}:
        return "Please select a valid payment method.", [], Decimal("0.00")
    try:
        datetime.strptime(form_data["sale_date"], "%Y-%m-%d")
    except (TypeError, ValueError):
        return "Please enter a valid sale date.", [], Decimal("0.00")
    if not form_data["items"]:
        return "At least one sale item is required.", [], Decimal("0.00")

    sale_items = []
    total_amount = Decimal("0.00")
    for item in form_data["items"]:
        if not item["product_id"]:
            return "Every sale item must have a product.", [], Decimal("0.00")
        try:
            product_id = int(item["product_id"])
            quantity = int(item["quantity"])
        except (TypeError, ValueError):
            return "Product IDs and quantities must be valid integers.", [], Decimal("0.00")
        if product_id <= 0 or quantity <= 0 or str(quantity) != item["quantity"]:
            return "Quantity must be a positive integer.", [], Decimal("0.00")
        try:
            unit_price = Decimal(item["unit_price"])
        except (InvalidOperation, TypeError):
            return "Unit price must be a valid number.", [], Decimal("0.00")
        if not unit_price.is_finite() or unit_price < 0 or unit_price.as_tuple().exponent < -2:
            return (
                "Unit price must be non-negative with at most 2 decimal places.",
                [],
                Decimal("0.00"),
            )
        line_total = (Decimal(quantity) * unit_price).quantize(Decimal("0.01"))
        total_amount += line_total
        sale_items.append(
            {
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            }
        )
    form_data["items"] = sale_items
    return None, sale_items, total_amount.quantize(Decimal("0.01"))


def load_sale_options():
    """Load active products for the sale form."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT product_id, product_name, sku, current_stock, selling_price
            FROM products
            WHERE is_active = TRUE
            ORDER BY product_name
            """
        )
        return cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading sale form options")
        return []
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()


def render_sale_form(form_data):
    """Render the sale form with active product options."""
    return render_template(
        "sale_form.html",
        page_title="New Sale",
        products=load_sale_options(),
        sale=form_data,
    )


@app.get("/inventory")
def inventory():
    """Display current inventory, summary counts, and optional filters."""
    if "user_id" not in session:
        flash("Please log in to access inventory.", "error")
        return redirect(url_for("login"))

    search_term = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip().lower()
    allowed_statuses = {"out_of_stock", "low_stock", "in_stock", "active", "inactive"}
    if status_filter not in allowed_statuses:
        status_filter = ""

    connection = None
    cursor = None
    product_rows = []
    summary = {
        "total_products": 0,
        "in_stock": 0,
        "low_stock": 0,
        "out_of_stock": 0,
    }
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_products,
                COALESCE(SUM(CASE WHEN current_stock > reorder_level THEN 1 ELSE 0 END), 0) AS in_stock,
                COALESCE(SUM(CASE WHEN current_stock > 0 AND current_stock <= reorder_level THEN 1 ELSE 0 END), 0) AS low_stock,
                COALESCE(SUM(CASE WHEN current_stock <= 0 THEN 1 ELSE 0 END), 0) AS out_of_stock
            FROM products
            """
        )
        summary_row = cursor.fetchone()
        if summary_row:
            summary = {
                "total_products": summary_row["total_products"],
                "in_stock": summary_row["in_stock"],
                "low_stock": summary_row["low_stock"],
                "out_of_stock": summary_row["out_of_stock"],
            }

        conditions = []
        parameters = []
        if search_term:
            pattern = f"%{search_term}%"
            conditions.append(
                "(p.product_name LIKE %s OR p.sku LIKE %s "
                "OR c.category_name LIKE %s OR s.supplier_name LIKE %s)"
            )
            parameters.extend([pattern, pattern, pattern, pattern])
        status_conditions = {
            "out_of_stock": "p.current_stock <= 0",
            "low_stock": "p.current_stock > 0 AND p.current_stock <= p.reorder_level",
            "in_stock": "p.current_stock > p.reorder_level",
            "active": "p.is_active = %s",
            "inactive": "p.is_active = %s",
        }
        if status_filter:
            conditions.append(status_conditions[status_filter])
            if status_filter in {"active", "inactive"}:
                parameters.append(status_filter == "active")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(
            f"""
            SELECT p.product_id, p.product_name, p.sku, p.current_stock,
                   p.unit, p.reorder_level, p.is_active,
                   c.category_name, s.supplier_name
            FROM products AS p
            INNER JOIN categories AS c ON p.category_id = c.category_id
            LEFT JOIN suppliers AS s ON p.supplier_id = s.supplier_id
            {where_clause}
            ORDER BY p.product_name ASC
            """,
            tuple(parameters),
        )
        product_rows = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading inventory")
        flash("Inventory is temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "inventory.html",
        page_title="Inventory",
        products=product_rows,
        summary=summary,
        search_term=search_term,
        status_filter=status_filter,
    )


@app.route("/inventory/adjust/<int:product_id>", methods=["GET", "POST"])
def adjust_inventory(product_id):
    """Apply an atomic manual increase or decrease to active product stock."""
    if "user_id" not in session:
        flash("Please log in to adjust inventory.", "error")
        return redirect(url_for("login"))

    adjustment = {
        "adjustment_type": "increase",
        "quantity": "",
        "notes": "",
    }
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT product_id, product_name, sku, current_stock
            FROM products
            WHERE product_id = %s
            """,
            (product_id,),
        )
        product = cursor.fetchone()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading adjustment product %s", product_id)
        flash("The product could not be loaded. Please try again.", "error")
        return redirect(url_for("inventory"))
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    if product is None:
        flash("Product not found.", "error")
        return redirect(url_for("inventory"))

    if request.method == "POST":
        adjustment["adjustment_type"] = request.form.get("adjustment_type", "").strip()
        adjustment["quantity"] = request.form.get("quantity", "").strip()
        adjustment["notes"] = request.form.get("notes", "").strip()
        validation_error = validate_adjustment_input(adjustment)
        if validation_error:
            flash(validation_error, "error")
            return render_template(
                "stock_adjustment.html",
                page_title="Adjust Stock",
                product=product,
                adjustment=adjustment,
            )

        connection = None
        cursor = None
        try:
            connection = get_db_connection()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT product_id, product_name, current_stock, is_active
                FROM products
                WHERE product_id = %s
                FOR UPDATE
                """,
                (product_id,),
            )
            locked_product = cursor.fetchone()
            if locked_product is None:
                raise InventoryAdjustmentError("Product not found.")
            if not locked_product["is_active"]:
                raise InventoryAdjustmentError(
                    "Only active products can be adjusted."
                )

            quantity = int(adjustment["quantity"])
            current_stock = Decimal(str(locked_product["current_stock"]))
            signed_quantity = (
                quantity
                if adjustment["adjustment_type"] == "increase"
                else -quantity
            )
            new_stock = current_stock + Decimal(signed_quantity)
            if new_stock < 0:
                raise InventoryAdjustmentError(
                    f"Insufficient stock. Current stock: {locked_product['current_stock']}."
                )

            cursor.execute(
                "UPDATE products SET current_stock = %s WHERE product_id = %s",
                (new_stock, product_id),
            )
            cursor.execute(
                """
                INSERT INTO stock_transactions
                    (product_id, user_id, transaction_type, quantity,
                     balance_after, notes)
                VALUES (%s, %s, 'adjustment', %s, %s, %s)
                """,
                (
                    product_id,
                    session["user_id"],
                    signed_quantity,
                    new_stock,
                    adjustment["notes"],
                ),
            )
            connection.commit()
        except InventoryAdjustmentError as error:
            if connection is not None:
                connection.rollback()
            flash(str(error), "error")
            return render_template(
                "stock_adjustment.html",
                page_title="Adjust Stock",
                product=product,
                adjustment=adjustment,
            )
        except mysql.connector.Error:
            if connection is not None:
                connection.rollback()
            app.logger.exception("Database error while adjusting product %s", product_id)
            flash("The stock adjustment could not be saved. Please try again.", "error")
            return render_template(
                "stock_adjustment.html",
                page_title="Adjust Stock",
                product=product,
                adjustment=adjustment,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None and connection.is_connected():
                connection.close()

        flash("Stock adjustment saved successfully.", "success")
        return redirect(url_for("inventory"))

    return render_template(
        "stock_adjustment.html",
        page_title="Adjust Stock",
        product=product,
        adjustment=adjustment,
    )


@app.get("/inventory/history")
def inventory_history():
    """Display newest stock transactions with safe optional filters."""
    if "user_id" not in session:
        flash("Please log in to access stock history.", "error")
        return redirect(url_for("login"))

    product_filter = request.args.get("product_id", "").strip()
    transaction_filter = request.args.get("transaction_type", "").strip().lower()
    allowed_types = {"purchase", "sale", "adjustment", "return"}
    if transaction_filter not in allowed_types:
        transaction_filter = ""

    connection = None
    cursor = None
    transactions = []
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        conditions = []
        parameters = []
        if product_filter:
            try:
                product_id = int(product_filter)
            except ValueError:
                product_id = 0
            conditions.append("st.product_id = %s")
            parameters.append(product_id)
        if transaction_filter:
            conditions.append("st.transaction_type = %s")
            parameters.append(transaction_filter)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(
            f"""
            SELECT st.transaction_id, st.transaction_date, st.transaction_type,
                   st.quantity, st.balance_after, st.notes,
                   p.product_id, p.product_name, p.sku,
                   u.full_name, st.purchase_id, st.sale_id
            FROM stock_transactions AS st
            INNER JOIN products AS p ON st.product_id = p.product_id
            INNER JOIN users AS u ON st.user_id = u.user_id
            LEFT JOIN purchases AS pu ON st.purchase_id = pu.purchase_id
            LEFT JOIN sales AS sa ON st.sale_id = sa.sale_id
            {where_clause}
            ORDER BY st.transaction_date DESC, st.transaction_id DESC
            """,
            tuple(parameters),
        )
        transactions = cursor.fetchall()
    except mysql.connector.Error:
        app.logger.exception("Database error while loading stock history")
        flash("Stock history is temporarily unavailable. Please try again.", "error")
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    return render_template(
        "stock_history.html",
        page_title="Stock History",
        transactions=transactions,
        product_filter=product_filter,
        transaction_filter=transaction_filter,
    )


class InventoryAdjustmentError(Exception):
    """Expected validation failure during an inventory adjustment."""


def validate_adjustment_input(adjustment):
    """Validate adjustment type, positive quantity, and reason."""
    if adjustment["adjustment_type"] not in {"increase", "decrease"}:
        return "Please select a valid adjustment type."
    if not adjustment["quantity"]:
        return "Quantity is required."
    try:
        quantity = int(adjustment["quantity"])
    except (TypeError, ValueError):
        return "Quantity must be a positive integer."
    if quantity <= 0 or str(quantity) != adjustment["quantity"]:
        return "Quantity must be a positive integer."
    if not adjustment["notes"]:
        return "Reason is required."
    if len(adjustment["notes"]) > 255:
        return "Reason must be 255 characters or fewer."
    return None


@app.post("/logout")
def logout():
    """End the current session and return the user to the login page."""
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.cli.command("create-admin")
def create_admin():
    """Interactively create the first (or another) local development admin."""
    full_name = click.prompt("Full name").strip()
    username = click.prompt("Username").strip()
    email = click.prompt("Email").strip()
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

    if not full_name or not username or not email or not password:
        raise click.ClickException("Name, username, email, and password are required.")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO users
                (full_name, username, email, password_hash, role, is_active)
            VALUES (%s, %s, %s, %s, 'admin', TRUE)
            """,
            (full_name, username, email, generate_password_hash(password)),
        )
        connection.commit()
    except mysql.connector.IntegrityError:
        if connection is not None:
            connection.rollback()
        raise click.ClickException("That username or email is already registered.")
    except mysql.connector.Error:
        if connection is not None:
            connection.rollback()
        raise click.ClickException(
            "Could not create the admin because the database is unavailable."
        )
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

    click.echo(f"Admin user '{username}' created successfully.")


if __name__ == "__main__":
    # Debug mode is useful during development and can be disabled for deployment.
    app.run(debug=True)
