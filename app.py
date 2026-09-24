"""Application entry point for the Inventory-AI Flask project."""

from flask import Flask, render_template
from dotenv import load_dotenv

from config import Config

# Load values from a local .env file when one exists.
# The real .env file is ignored by Git; .env.example documents the required keys.
load_dotenv()

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    """Show a small health page until the application modules are built."""
    return render_template("base.html", page_title="Inventory-AI", home_page=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Render the initial login screen.

    Authentication is intentionally not implemented in this foundation step.
    """
    return render_template("login.html", page_title="Login")


if __name__ == "__main__":
    # Debug mode is useful during development and can be disabled for deployment.
    app.run(debug=True)
