# kotlin-inventory

A small warehouse inventory tracker (Spring Boot, Kotlin). Three entities:
warehouses, items (belong to a warehouse), and stock movements (IN/OUT
adjustments against an item's quantity). In-memory repositories only (the
offline Maven cache only warms `spring-boot-starter-web`/`-test`, not a JPA
driver).

Run tests with `mvn -o -q test`. Integration tests use `@SpringBootTest` +
`MockMvc`, with each repository's `clear()` called in `@BeforeEach` to start
from a clean state.
