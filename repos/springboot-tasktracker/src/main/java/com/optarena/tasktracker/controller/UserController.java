package com.optarena.tasktracker.controller;

import com.optarena.tasktracker.dto.UserCreateRequest;
import com.optarena.tasktracker.dto.UserResponse;
import com.optarena.tasktracker.service.UserService;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class UserController {
    private final UserService userService;

    public UserController(UserService userService) {
        this.userService = userService;
    }

    @PostMapping("/users")
    @ResponseStatus(HttpStatus.CREATED)
    public UserResponse createUser(@RequestBody UserCreateRequest request) {
        return UserResponse.from(userService.createUser(request));
    }

    @GetMapping("/users")
    public List<UserResponse> listUsers(
            @RequestParam(defaultValue = "0") int skip,
            @RequestParam(defaultValue = "100") int limit
    ) {
        return userService.listUsers(skip, limit).stream().map(UserResponse::from).toList();
    }

    @GetMapping("/users/{userId}")
    public UserResponse getUser(@PathVariable Long userId) {
        return UserResponse.from(userService.getUser(userId));
    }
}
