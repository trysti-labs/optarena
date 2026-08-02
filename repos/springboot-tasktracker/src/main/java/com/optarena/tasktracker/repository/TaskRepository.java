package com.optarena.tasktracker.repository;

import com.optarena.tasktracker.model.Task;
import com.optarena.tasktracker.model.TaskStatus;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Repository;

@Repository
public class TaskRepository {
    private final Map<Long, Task> byId = new ConcurrentHashMap<>();
    private final AtomicLong nextId = new AtomicLong(1);

    public Task save(Task task) {
        if (task.getId() == null) {
            task.setId(nextId.getAndIncrement());
        }
        byId.put(task.getId(), task);
        return task;
    }

    public Optional<Task> findById(Long id) {
        return Optional.ofNullable(byId.get(id));
    }

    public void delete(Task task) {
        byId.remove(task.getId());
    }

    public List<Task> findAll(Long projectId, TaskStatus status, Long assigneeId, int skip, int limit) {
        List<Task> all = new ArrayList<>(byId.values());
        all.sort(Comparator.comparing(Task::getId));
        List<Task> filtered = new ArrayList<>();
        for (Task task : all) {
            if (projectId != null && !projectId.equals(task.getProjectId())) {
                continue;
            }
            if (status != null && status != task.getStatus()) {
                continue;
            }
            if (assigneeId != null && !assigneeId.equals(task.getAssigneeId())) {
                continue;
            }
            filtered.add(task);
        }
        return Paging.page(filtered, skip, limit);
    }

    public void clear() {
        byId.clear();
        nextId.set(1);
    }
}
