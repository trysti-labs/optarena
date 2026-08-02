package com.optarena.tasktracker.dto;

import com.optarena.tasktracker.model.User;

public record UserResponse(Long id, String username, String email, String fullName, boolean active) {
    public static UserResponse from(User user) {
        return new UserResponse(user.getId(), user.getUsername(), user.getEmail(), user.getFullName(), user.isActive());
    }
}
