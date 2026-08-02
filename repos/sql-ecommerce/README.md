# sql-ecommerce

A small multi-file e-commerce schema (SQLite dialect): `schema.sql` defines
customers/products/orders/order_items, `views.sql` defines two aggregate
views (`customer_order_totals`, `product_sales_summary`) over them.

Cases are checked with `python3 check_X.py` scripts that load both files
into an in-memory `sqlite3` database (`con.executescript`), seed some rows,
and assert query results / cross-file consistency.
