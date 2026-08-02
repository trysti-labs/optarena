package com.optarena.tasktracker.repository;

import com.optarena.tasktracker.model.Project;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Repository;

@Repository
public class ProjectRepository {
    private final Map<Long, Project> byId = new ConcurrentHashMap<>();
    private final AtomicLong nextId = new AtomicLong(1);

    public Project save(Project project) {
        if (project.getId() == null) {
            project.setId(nextId.getAndIncrement());
        }
        byId.put(project.getId(), project);
        return project;
    }

    public Optional<Project> findById(Long id) {
        return Optional.ofNullable(byId.get(id));
    }

    public boolean existsByName(String name) {
        return byId.values().stream().anyMatch(p -> p.getName().equals(name));
    }

    public List<Project> findAll(int skip, int limit) {
        List<Project> all = new ArrayList<>(byId.values());
        all.sort(Comparator.comparing(Project::getId));
        return Paging.page(all, skip, limit);
    }

    public void clear() {
        byId.clear();
        nextId.set(1);
    }
}
