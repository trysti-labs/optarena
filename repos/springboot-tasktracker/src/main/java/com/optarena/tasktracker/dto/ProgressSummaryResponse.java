package com.optarena.tasktracker.dto;

public record ProgressSummaryResponse(int total, int todo, int inProgress, int done, double percentDone) {
}
