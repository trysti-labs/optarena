package com.optarena.tasktracker.repository;

import com.optarena.tasktracker.model.User;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Repository;

@Repository
public class UserRepository {
    private final Map<Long, User> byId = new ConcurrentHashMap<>();
    private final AtomicLong nextId = new AtomicLong(1);

    public User save(User user) {
        if (user.getId() == null) {
            user.setId(nextId.getAndIncrement());
        }
        byId.put(user.getId(), user);
        return user;
    }

    public Optional<User> findById(Long id) {
        return Optional.ofNullable(byId.get(id));
    }

    public boolean existsByUsername(String username) {
        return byId.values().stream().anyMatch(u -> u.getUsername().equals(username));
    }

    public boolean existsByEmail(String email) {
        return byId.values().stream().anyMatch(u -> u.getEmail().equals(email));
    }

    public List<User> findAll(int skip, int limit) {
        List<User> all = new ArrayList<>(byId.values());
        all.sort(Comparator.comparing(User::getId));
        return Paging.page(all, skip, limit);
    }

    public void clear() {
        byId.clear();
        nextId.set(1);
    }
}
