#include "queue.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static void test_pops_lowest_priority_first(void) {
    JobQueue q;
    queue_init(&q);
    queue_push(&q, 5, "low");
    queue_push(&q, 1, "high");
    queue_push(&q, 3, "mid");

    Job job;
    assert(queue_pop(&q, &job) == 1);
    assert(strcmp(job.payload, "high") == 0);
    assert(queue_pop(&q, &job) == 1);
    assert(strcmp(job.payload, "mid") == 0);
    assert(queue_pop(&q, &job) == 1);
    assert(strcmp(job.payload, "low") == 0);
    assert(queue_pop(&q, &job) == 0);
}

static void test_fifo_tiebreak_for_equal_priority(void) {
    JobQueue q;
    queue_init(&q);
    queue_push(&q, 1, "a");
    queue_push(&q, 1, "b");
    queue_push(&q, 1, "c");

    Job job;
    queue_pop(&q, &job);
    assert(strcmp(job.payload, "a") == 0);
    queue_pop(&q, &job);
    assert(strcmp(job.payload, "b") == 0);
    queue_pop(&q, &job);
    assert(strcmp(job.payload, "c") == 0);
}

static void test_size_tracks_pending_count(void) {
    JobQueue q;
    queue_init(&q);
    assert(queue_size(&q) == 0);
    queue_push(&q, 1, "a");
    queue_push(&q, 2, "b");
    assert(queue_size(&q) == 2);
    Job job;
    queue_pop(&q, &job);
    assert(queue_size(&q) == 1);
}

int main(void) {
    test_pops_lowest_priority_first();
    test_fifo_tiebreak_for_equal_priority();
    test_size_tracks_pending_count();
    printf("PASS\n");
    return 0;
}
