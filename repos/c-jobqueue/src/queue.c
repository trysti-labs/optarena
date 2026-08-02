#include "queue.h"

#include <string.h>

void queue_init(JobQueue *q) {
    q->count = 0;
    q->next_id = 1;
    q->next_order = 0;
}

int queue_push(JobQueue *q, int priority, const char *payload) {
    if (q->count >= QUEUE_MAX_JOBS) {
        return -1;
    }
    Job *job = &q->jobs[q->count];
    job->id = q->next_id++;
    job->priority = priority;
    job->state = JOB_PENDING;
    job->insertion_order = q->next_order++;
    strncpy(job->payload, payload, sizeof(job->payload) - 1);
    job->payload[sizeof(job->payload) - 1] = '\0';
    q->count++;
    return job->id;
}

int queue_pop(JobQueue *q, Job *out) {
    if (q->count == 0) {
        return 0;
    }
    int best = 0;
    for (int i = 1; i < q->count; i++) {
        if (q->jobs[i].priority < q->jobs[best].priority ||
            (q->jobs[i].priority == q->jobs[best].priority &&
             q->jobs[i].insertion_order < q->jobs[best].insertion_order)) {
            best = i;
        }
    }
    *out = q->jobs[best];
    for (int i = best; i < q->count - 1; i++) {
        q->jobs[i] = q->jobs[i + 1];
    }
    q->count--;
    return 1;
}

int queue_size(const JobQueue *q) {
    return q->count;
}
