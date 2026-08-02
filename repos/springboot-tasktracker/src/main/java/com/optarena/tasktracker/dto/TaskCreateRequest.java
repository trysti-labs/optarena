package com.optarena.tasktracker.dto;

public record TaskCreateRequest(String title, String description, Integer priority, Long projectId, Long assigneeId) {
}
