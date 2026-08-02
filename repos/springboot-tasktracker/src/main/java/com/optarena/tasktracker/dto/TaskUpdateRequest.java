package com.optarena.tasktracker.dto;

import com.optarena.tasktracker.model.TaskStatus;

public record TaskUpdateRequest(
        String title,
        String description,
        TaskStatus status,
        Integer priority,
        Long assigneeId
) {
}
