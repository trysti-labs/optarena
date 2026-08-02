package com.optarena.tasktracker.service;

import com.optarena.tasktracker.dto.TaskCreateRequest;
import com.optarena.tasktracker.dto.TaskUpdateRequest;
import com.optarena.tasktracker.exception.NotFoundException;
import com.optarena.tasktracker.model.Task;
import com.optarena.tasktracker.model.TaskStatus;
import com.optarena.tasktracker.repository.TaskRepository;
import java.util.List;
import org.springframework.stereotype.Service;

@Service
public class TaskService {
    private final TaskRepository taskRepository;

    public TaskService(TaskRepository taskRepository) {
        this.taskRepository = taskRepository;
    }

    public Task createTask(TaskCreateRequest request) {
        Task task = new Task(
                null,
                request.title(),
                request.description() == null ? "" : request.description(),
                request.priority() == null ? 3 : request.priority(),
                request.projectId(),
                request.assigneeId()
        );
        return taskRepository.save(task);
    }

    public Task getTask(Long taskId) {
        return taskRepository.findById(taskId)
                .orElseThrow(() -> new NotFoundException("task not found"));
    }

    public List<Task> listTasks(Long projectId, TaskStatus status, Long assigneeId, int skip, int limit) {
        return taskRepository.findAll(projectId, status, assigneeId, skip, limit);
    }

    public Task updateTask(Long taskId, TaskUpdateRequest request) {
        Task task = getTask(taskId);
        if (request.title() != null) {
            task.setTitle(request.title());
        }
        if (request.description() != null) {
            task.setDescription(request.description());
        }
        if (request.status() != null) {
            task.setStatus(request.status());
        }
        if (request.priority() != null) {
            task.setPriority(request.priority());
        }
        if (request.assigneeId() != null) {
            task.setAssigneeId(request.assigneeId());
        }
        return taskRepository.save(task);
    }

    public void deleteTask(Long taskId) {
        Task task = getTask(taskId);
        taskRepository.delete(task);
    }
}
