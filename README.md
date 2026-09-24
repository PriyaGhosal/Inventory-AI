# Inventory-AI

Inventory-AI is a beginner-friendly Inventory Management System built with
Python 3.10, Flask, MySQL, HTML, CSS, vanilla JavaScript, and Jinja2. The
project is being developed in small steps. Stage 2 adds real MySQL-backed user
authentication and a protected dashboard placeholder. Stage 3 adds protected
category management with add, edit, list, and safe delete operations. Stage 4
adds protected supplier management with search and safe delete operations.

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
├── templates/suppliers.html
├── templates/supplier_form.html
├── templates/products.html
├── templates/product_form.html
├── templates/purchases.html
├── templates/purchase_form.html
├── templates/purchase_detail.html
├── templates/sales.html
├── templates/sale_form.html
├── templates/sale_detail.html
├── templates/inventory.html
├── templates/stock_adjustment.html
├── templates/stock_history.html
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

Purchase, sales, reporting, and AI functionality are not implemented yet.

## Category management

After logging in, use the **Categories** link in the navigation to manage
categories. Category names are required, limited to 100 characters, and must
be unique. Descriptions are optional and limited to the existing database
column size of 255 characters. A category that is referenced by a product
cannot be deleted.

## Supplier management

After logging in, use the **Suppliers** link in the navigation to manage
supplier contact details. Supplier lists can be filtered with the search box
or with a URL such as `/suppliers?search=acme`. Searches check supplier name,
contact person, email, and phone. A supplier that is referenced by a purchase
cannot be deleted.

## Product management

After logging in, use the **Products** link to add and edit products, search
by product name, SKU, category, or supplier, and filter by active status.
Products can be activated or deactivated without deleting historical records.
Current stock is manually editable at this stage; automatic stock transactions
are not yet implemented.

## Purchase management

The protected **Purchases** module creates pending purchases with multiple
items. Totals are recalculated on the server. Creating a pending purchase does
not change stock. Receiving a purchase uses one database transaction to update
all product stock values, create matching `stock_transactions` records, and
mark the purchase as received. Cancelling a pending purchase does not change
stock.

## Sales management

The protected **Sales** module records completed sales with multiple items,
reduces stock atomically, and creates matching `stock_transactions` rows.
Sales can be searched by sale ID, customer name, or phone, and filtered by
payment method. Cancelling a completed sale restores stock in one transaction
and records return transactions. A cancelled sale cannot be cancelled again.

## Inventory and stock history

The protected **Inventory** page summarizes current stock, supports product
search and stock/active-status filters, and provides controlled manual
adjustments. Increases record positive adjustment quantities; decreases record
negative adjustment quantities. Every adjustment locks the product row and
updates `products.current_stock` and `stock_transactions` in one transaction.
Purchases and sales continue to own their existing stock-update logic.
