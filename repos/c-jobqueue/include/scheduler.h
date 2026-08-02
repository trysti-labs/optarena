#pragma once

#include "job.h"
#include "queue.h"

#define SCHEDULER_MAX_RUNNING 256
#define SCHEDULER_MAX_HISTORY 256

typedef struct {
    JobQueue pending;
    Job running[SCHEDULER_MAX_RUNNING];
    int running_count;
    Job history[SCHEDULER_MAX_HISTORY];
    int history_count;
} Scheduler;

void scheduler_init(Scheduler *s);

/* Submits a new pending job; returns its id, or -1 if the queue is full. */
int scheduler_submit(Scheduler *s, int priority, const char *payload);

/* Moves the highest-priority pending job to RUNNING; writes its id to
 * *out_id. Returns 1 on success, 0 if nothing is pending. */
int scheduler_run_next(Scheduler *s, int *out_id);

/* Marks a RUNNING job DONE and moves it to history.
 * Returns 1 on success, 0 if the id isn't currently running. */
int scheduler_complete(Scheduler *s, int id);

/* Marks a RUNNING job FAILED and moves it to history.
 * Returns 1 on success, 0 if the id isn't currently running. */
int scheduler_fail(Scheduler *s, int id);

/* Writes the job's current state to *out_state. Returns 1 if found
 * (pending, running, or in history), 0 otherwise. */
int scheduler_find_state(const Scheduler *s, int id, JobState *out_state);
