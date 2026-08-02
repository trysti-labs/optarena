package com.optarena.tasktracker.model;

public class Task {
    private Long id;
    private String title;
    private String description = "";
    private TaskStatus status = TaskStatus.TODO;
    private int priority = 3;
    private Long projectId;
    private Long assigneeId;

    public Task() {
    }

    public Task(Long id, String title, String description, int priority, Long projectId, Long assigneeId) {
        this.id = id;
        this.title = title;
        this.description = description;
        this.priority = priority;
        this.projectId = projectId;
        this.assigneeId = assigneeId;
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public TaskStatus getStatus() {
        return status;
    }

    public void setStatus(TaskStatus status) {
        this.status = status;
    }

    public int getPriority() {
        return priority;
    }

    public void setPriority(int priority) {
        this.priority = priority;
    }

    public Long getProjectId() {
        return projectId;
    }

    public void setProjectId(Long projectId) {
        this.projectId = projectId;
    }

    public Long getAssigneeId() {
        return assigneeId;
    }

    public void setAssigneeId(Long assigneeId) {
        this.assigneeId = assigneeId;
    }
}
