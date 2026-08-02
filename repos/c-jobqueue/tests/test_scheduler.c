#include "scheduler.h"

#include <assert.h>
#include <stdio.h>

static void test_submit_and_run_next(void) {
    Scheduler s;
    scheduler_init(&s);
    int id_a = scheduler_submit(&s, 5, "a");
    int id_b = scheduler_submit(&s, 1, "b");
    (void)id_a;

    int running_id = 0;
    assert(scheduler_run_next(&s, &running_id) == 1);
    assert(running_id == id_b);

    JobState state;
    assert(scheduler_find_state(&s, id_b, &state) == 1);
    assert(state == JOB_RUNNING);
}

static void test_complete_moves_job_to_history(void) {
    Scheduler s;
    scheduler_init(&s);
    int id = scheduler_submit(&s, 1, "a");
    int running_id = 0;
    scheduler_run_next(&s, &running_id);

    assert(scheduler_complete(&s, id) == 1);

    JobState state;
    assert(scheduler_find_state(&s, id, &state) == 1);
    assert(state == JOB_DONE);

    /* Completing an already-finished job again must fail, not double-count. */
    assert(scheduler_complete(&s, id) == 0);
}

static void test_fail_moves_job_to_history_as_failed(void) {
    Scheduler s;
    scheduler_init(&s);
    int id = scheduler_submit(&s, 1, "a");
    int running_id = 0;
    scheduler_run_next(&s, &running_id);

    assert(scheduler_fail(&s, id) == 1);

    JobState state;
    assert(scheduler_find_state(&s, id, &state) == 1);
    assert(state == JOB_FAILED);
}

static void test_run_next_returns_zero_when_nothing_pending(void) {
    Scheduler s;
    scheduler_init(&s);
    int running_id = 0;
    assert(scheduler_run_next(&s, &running_id) == 0);
}

int main(void) {
    test_submit_and_run_next();
    test_complete_moves_job_to_history();
    test_fail_moves_job_to_history_as_failed();
    test_run_next_returns_zero_when_nothing_pending();
    printf("PASS\n");
    return 0;
}
