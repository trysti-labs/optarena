package com.optarena.tasktracker.service;

import com.optarena.tasktracker.dto.ProjectCreateRequest;
import com.optarena.tasktracker.exception.NotFoundException;
import com.optarena.tasktracker.model.Project;
import com.optarena.tasktracker.repository.ProjectRepository;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class ProjectService {
    private final ProjectRepository projectRepository;

    public ProjectService(ProjectRepository projectRepository) {
        this.projectRepository = projectRepository;
    }

    public Project createProject(ProjectCreateRequest request) {
        Project project = new Project(null, request.name(),
                request.description() == null ? "" : request.description(), request.ownerId());
        return projectRepository.save(project);
    }

    public Project getProject(Long projectId) {
        return projectRepository.findById(projectId)
                .orElseThrow(() -> new NotFoundException("project not found"));
    }

    public List<Project> listProjects(int skip, int limit) {
        return projectRepository.findAll(skip, limit);
    }
}
