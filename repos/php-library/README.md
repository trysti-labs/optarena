# php-library

A small library lending tracker (plain PHP, no framework - Composer packages
aren't available offline unless vendored, so this uses manual `require_once`
includes and an `App` namespace, matching the rest of this corpus's PHP
cases). Three entities: books, members, and loans (borrow/return, with a
copies-available invariant).

Run tests with `phpunit tests`.
