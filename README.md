# Inventory-AI

Inventory-AI is a beginner-friendly Inventory Management System built with
Python 3.10, Flask, MySQL, HTML, CSS, vanilla JavaScript, and Jinja2. The
project is being developed in small steps. Stage 2 adds real MySQL-backed user authentication and a protected dashboard
placeholder. Stage 3 adds protected category management with add, edit, list,
and safe delete operations.

## Project structure

```text
Inventory-AI/
├── app.py
├── config.py
├── requirements.txt
├── .env.example
├── database/inventory.sql
├── templates/base.html
├── templates/login.html
├── templates/dashboard.html
├── templates/categories.html
├── templates/category_form.html
├── static/css/style.css
├── static/js/script.js
└── utils/helpers.py
```

## Setup

1. Use Python 3.10 and create or activate a virtual environment.
2. Install the dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and set your local MySQL values.
4. Create the database and tables by running `database/inventory.sql` in
   MySQL Workbench or the MySQL command line client.
5. Start the development server:

   ```powershell
   python app.py
   ```

6. Open <http://127.0.0.1:5000/> in a browser. The initial login screen is
   available at <http://127.0.0.1:5000/login>.

## Create a local development admin

After the database has been created and `.env` is configured, run:

```powershell
flask --app app create-admin
```

The command prompts for the full name, username, email, and password. The
password is entered privately, hashed with Werkzeug, and never stored in plain
text or included in the command line.

Log in at `/login`. Successful authentication redirects to `/dashboard`.
The dashboard is protected and redirects unauthenticated visitors to the
login page. Use the Logout button to clear the session.

No product, supplier, purchase, sales, reporting, or AI functionality is
implemented yet.

## Category management

After logging in, use the **Categories** link in the navigation to manage
categories. Category names are required, limited to 100 characters, and must
be unique. Descriptions are optional and limited to the existing database
column size of 255 characters. A category that is referenced by a product
cannot be deleted.
