"""Application entry point for the Inventory-AI Flask project."""

import click
import mysql.connector
import re
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, session, url_for
from flask import request
from werkzeug.security import check_password_hash, generate_password_hash

# Load values from a local .env file when one exists.
# The real .env file is ignored by Git; .env.example documents the required keys.
load_dotenv()

from config import Config
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
    """Display a protected placeholder dashboard for authenticated users."""
    if "user_id" not in session:
        flash("Please log in to access the dashboard.", "error")
        return redirect(url_for("login"))
    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        full_name=session["full_name"],
        role=session["role"],
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
