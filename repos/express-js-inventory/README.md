# express-js-inventory

A small warehouse inventory tracker (Express, plain JS - no TypeScript). Three
entities: warehouses, items (belong to a warehouse), and stock movements
(IN/OUT adjustments against an item's quantity).

Run tests with `npm test` (`node --test`). No external test framework or HTTP
client library - tests start the app on an ephemeral port (`listen(0)`) and
talk to it with the global `fetch`.
