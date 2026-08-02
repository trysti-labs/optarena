package com.optarena.tasktracker.controller;

import com.optarena.tasktracker.dto.ProgressSummaryResponse;
import com.optarena.tasktracker.dto.ProjectCreateRequest;
import com.optarena.tasktracker.dto.ProjectResponse;
import com.optarena.tasktracker.dto.TaskResponse;
import com.optarena.tasktracker.model.Project;
import com.optarena.tasktracker.model.Task;
import com.optarena.tasktracker.service.ProgressService;
import com.optarena.tasktracker.service.ProjectService;
import com.optarena.tasktracker.service.TaskService;
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
public class ProjectController {
    private final ProjectService projectService;
    private final TaskService taskService;
    private final ProgressService progressService;

    public ProjectController(ProjectService projectService, TaskService taskService, ProgressService progressService) {
        this.projectService = projectService;
        this.taskService = taskService;
        this.progressService = progressService;
    }

    @PostMapping("/projects")
    @ResponseStatus(HttpStatus.CREATED)
    public ProjectResponse createProject(@RequestBody ProjectCreateRequest request) {
        return ProjectResponse.from(projectService.createProject(request));
    }

    @GetMapping("/projects")
    public List<ProjectResponse> listProjects(
            @RequestParam(defaultValue = "0") int skip,
            @RequestParam(defaultValue = "100") int limit
    ) {
        return projectService.listProjects(skip, limit).stream().map(ProjectResponse::from).toList();
    }

    @GetMapping("/projects/{projectId}")
    public ProjectResponse getProject(@PathVariable Long projectId) {
        Project project = projectService.getProject(projectId);
        return ProjectResponse.from(project);
    }

    @GetMapping("/projects/{projectId}/tasks")
    public List<TaskResponse> listProjectTasks(@PathVariable Long projectId) {
        projectService.getProject(projectId);
        return taskService.listTasks(projectId, null, null, 0, Integer.MAX_VALUE).stream()
                .map(TaskResponse::from).toList();
    }

    @GetMapping("/projects/{projectId}/progress")
    public ProgressSummaryResponse projectProgress(@PathVariable Long projectId) {
        projectService.getProject(projectId);
        List<Task> tasks = taskService.listTasks(projectId, null, null, 0, Integer.MAX_VALUE);
        return progressService.summarize(tasks);
    }
}
