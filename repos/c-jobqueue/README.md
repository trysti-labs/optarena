# c-jobqueue

A small multi-file C job-scheduling library: a priority queue (lowest
priority number first, FIFO tiebreak) plus a scheduler layer that tracks
pending/running/done/failed job state.

Build and run each test binary with (each test file supplies its own
`main()`, so they're compiled and run separately):

```
gcc -std=c11 -Wall -Iinclude src/*.c tests/test_queue.c -o test_queue_bin && ./test_queue_bin
gcc -std=c11 -Wall -Iinclude src/*.c tests/test_scheduler.c -o test_scheduler_bin && ./test_scheduler_bin
```
