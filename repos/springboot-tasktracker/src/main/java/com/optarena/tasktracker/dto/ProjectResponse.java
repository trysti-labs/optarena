package com.optarena.tasktracker.dto;

import com.optarena.tasktracker.model.Project;

public record ProjectResponse(Long id, String name, String description, Long ownerId) {
    public static ProjectResponse from(Project project) {
        return new ProjectResponse(project.getId(), project.getName(), project.getDescription(), project.getOwnerId());
    }
}
