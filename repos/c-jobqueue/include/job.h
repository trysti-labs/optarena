#pragma once

typedef enum { JOB_PENDING, JOB_RUNNING, JOB_DONE, JOB_FAILED } JobState;

typedef struct {
    int id;
    int priority; /* lower number = higher priority */
    char payload[128];
    JobState state;
    long insertion_order; /* tiebreak: earlier submissions run first */
} Job;
