package com.optarena.tasktracker.controller;

import com.optarena.tasktracker.dto.TaskCreateRequest;
import com.optarena.tasktracker.dto.TaskResponse;
import com.optarena.tasktracker.dto.TaskUpdateRequest;
import com.optarena.tasktracker.model.TaskStatus;
import com.optarena.tasktracker.service.TaskService;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TaskController {
    private final TaskService taskService;

    public TaskController(TaskService taskService) {
        this.taskService = taskService;
    }

    @PostMapping("/tasks")
    @ResponseStatus(HttpStatus.CREATED)
    public TaskResponse createTask(@RequestBody TaskCreateRequest request) {
        return TaskResponse.from(taskService.createTask(request));
    }

    @GetMapping("/tasks")
    public List<TaskResponse> listTasks(
            @RequestParam(required = false) Long projectId,
            @RequestParam(required = false) TaskStatus status,
            @RequestParam(required = false) Long assigneeId,
            @RequestParam(defaultValue = "0") int skip,
            @RequestParam(defaultValue = "100") int limit
    ) {
        return taskService.listTasks(projectId, status, assigneeId, skip, limit).stream()
                .map(TaskResponse::from).toList();
    }

    @GetMapping("/tasks/{taskId}")
    public TaskResponse getTask(@PathVariable Long taskId) {
        return TaskResponse.from(taskService.getTask(taskId));
    }

    @PatchMapping("/tasks/{taskId}")
    public TaskResponse updateTask(@PathVariable Long taskId, @RequestBody TaskUpdateRequest request) {
        return TaskResponse.from(taskService.updateTask(taskId, request));
    }

    @DeleteMapping("/tasks/{taskId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteTask(@PathVariable Long taskId) {
        taskService.deleteTask(taskId);
    }
}
