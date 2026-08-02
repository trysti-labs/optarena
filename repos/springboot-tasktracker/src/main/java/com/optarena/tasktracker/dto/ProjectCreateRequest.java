package com.optarena.tasktracker.dto;

public record ProjectCreateRequest(String name, String description, Long ownerId) {
}
