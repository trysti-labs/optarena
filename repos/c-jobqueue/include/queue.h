#pragma once

#include "job.h"

#define QUEUE_MAX_JOBS 256

typedef struct {
    Job jobs[QUEUE_MAX_JOBS];
    int count;
    int next_id;
    long next_order;
} JobQueue;

void queue_init(JobQueue *q);

/* Adds a pending job; returns its assigned id, or -1 if the queue is full. */
int queue_push(JobQueue *q, int priority, const char *payload);

/* Pops the highest-priority pending job (lowest priority number, FIFO among
 * ties) into *out; returns 1 on success, 0 if the queue is empty. */
int queue_pop(JobQueue *q, Job *out);

int queue_size(const JobQueue *q);
