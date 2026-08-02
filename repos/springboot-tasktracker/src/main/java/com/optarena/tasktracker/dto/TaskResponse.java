package com.optarena.tasktracker.dto;

import com.optarena.tasktracker.model.Task;
import com.optarena.tasktracker.model.TaskStatus;

public record TaskResponse(
        Long id,
        String title,
        String description,
        TaskStatus status,
        int priority,
        Long projectId,
        Long assigneeId
) {
    public static TaskResponse from(Task task) {
        return new TaskResponse(
                task.getId(),
                task.getTitle(),
                task.getDescription(),
                task.getStatus(),
                task.getPriority(),
                task.getProjectId(),
                task.getAssigneeId()
        );
    }
}
