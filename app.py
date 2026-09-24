"""Application entry point for the Inventory-AI Flask project."""

import click
import mysql.connector
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
