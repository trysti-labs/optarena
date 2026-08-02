CREATE VIEW customer_order_totals AS
SELECT c.id AS customer_id,
       c.name,
       COALESCE(SUM(oi.quantity * oi.unit_price_cents), 0) AS total_cents
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
LEFT JOIN order_items oi ON oi.order_id = o.id
GROUP BY c.id, c.name;

CREATE VIEW product_sales_summary AS
SELECT p.id AS product_id,
       p.name,
       COALESCE(SUM(oi.quantity), 0) AS units_sold
FROM products p
LEFT JOIN order_items oi ON oi.product_id = p.id
GROUP BY p.id, p.name;
