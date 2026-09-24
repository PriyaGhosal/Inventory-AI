# Inventory-AI

Inventory-AI is a beginner-friendly Inventory Management System foundation
built with Python 3.10, Flask, MySQL, HTML, CSS, vanilla JavaScript, and
Jinja2. The project is being developed in small steps; this first step only
creates the application shell and database schema.

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

The application currently does not perform authentication or inventory
operations. Those features will be added in later steps.
