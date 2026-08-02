#include "scheduler.h"

void scheduler_init(Scheduler *s) {
    queue_init(&s->pending);
    s->running_count = 0;
    s->history_count = 0;
}

int scheduler_submit(Scheduler *s, int priority, const char *payload) {
    return queue_push(&s->pending, priority, payload);
}

int scheduler_run_next(Scheduler *s, int *out_id) {
    if (s->running_count >= SCHEDULER_MAX_RUNNING) {
        return 0;
    }
    Job job;
    if (!queue_pop(&s->pending, &job)) {
        return 0;
    }
    job.state = JOB_RUNNING;
    s->running[s->running_count++] = job;
    *out_id = job.id;
    return 1;
}

static int find_running_index(Scheduler *s, int id) {
    for (int i = 0; i < s->running_count; i++) {
        if (s->running[i].id == id) {
            return i;
        }
    }
    return -1;
}

static int finish_job(Scheduler *s, int id, JobState final_state) {
    int idx = find_running_index(s, id);
    if (idx < 0) {
        return 0;
    }
    Job job = s->running[idx];
    job.state = final_state;
    for (int i = idx; i < s->running_count - 1; i++) {
        s->running[i] = s->running[i + 1];
    }
    s->running_count--;
    if (s->history_count < SCHEDULER_MAX_HISTORY) {
        s->history[s->history_count++] = job;
    }
    return 1;
}

int scheduler_complete(Scheduler *s, int id) {
    return finish_job(s, id, JOB_DONE);
}

int scheduler_fail(Scheduler *s, int id) {
    return finish_job(s, id, JOB_FAILED);
}

int scheduler_find_state(const Scheduler *s, int id, JobState *out_state) {
    for (int i = 0; i < s->pending.count; i++) {
        if (s->pending.jobs[i].id == id) {
            *out_state = s->pending.jobs[i].state;
            return 1;
        }
    }
    for (int i = 0; i < s->running_count; i++) {
        if (s->running[i].id == id) {
            *out_state = s->running[i].state;
            return 1;
        }
    }
    for (int i = 0; i < s->history_count; i++) {
        if (s->history[i].id == id) {
            *out_state = s->history[i].state;
            return 1;
        }
    }
    return 0;
}
