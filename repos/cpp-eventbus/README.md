# cpp-eventbus

A small multi-file C++ pub/sub event bus: a capped `EventLog` history plus an
`EventBus` that records every published event and invokes exact-topic-match
subscribers.

Build and run each test binary with (each test file supplies its own
`main()`, so they're compiled and run separately):

```
g++ -std=c++17 -Iinclude tests/test_event_log.cpp -o test_event_log_bin && ./test_event_log_bin
g++ -std=c++17 -Iinclude tests/test_event_bus.cpp -o test_event_bus_bin && ./test_event_bus_bin
```
