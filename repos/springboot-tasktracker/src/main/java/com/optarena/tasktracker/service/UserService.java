package com.optarena.tasktracker.service;

import com.optarena.tasktracker.dto.UserCreateRequest;
import com.optarena.tasktracker.exception.NotFoundException;
import com.optarena.tasktracker.model.User;
import com.optarena.tasktracker.repository.UserRepository;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class UserService {
    private final UserRepository userRepository;

    public UserService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    public User createUser(UserCreateRequest request) {
        User user = new User(null, request.username(), request.email(),
                request.fullName() == null ? "" : request.fullName(), true);
        return userRepository.save(user);
    }

    public User getUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new NotFoundException("user not found"));
    }

    public List<User> listUsers(int skip, int limit) {
        return userRepository.findAll(skip, limit);
    }
}
