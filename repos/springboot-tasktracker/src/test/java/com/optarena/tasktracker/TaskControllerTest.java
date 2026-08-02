package com.optarena.tasktracker;

import static org.hamcrest.Matchers.is;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.jayway.jsonpath.JsonPath;
import com.optarena.tasktracker.repository.ProjectRepository;
import com.optarena.tasktracker.repository.TaskRepository;
import com.optarena.tasktracker.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class TaskControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private ProjectRepository projectRepository;

    @Autowired
    private TaskRepository taskRepository;

    @BeforeEach
    void resetState() {
        userRepository.clear();
        projectRepository.clear();
        taskRepository.clear();
    }

    private int createProject() throws Exception {
        String userBody = mockMvc.perform(post("/users")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"dev\",\"email\":\"dev@example.com\"}"))
                .andReturn().getResponse().getContentAsString();
        int userId = JsonPath.read(userBody, "$.id");
        String projBody = mockMvc.perform(post("/projects")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"Mercury\",\"ownerId\":" + userId + "}"))
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(projBody, "$.id");
    }

    @Test
    void createAndUpdateTask() throws Exception {
        int projectId = createProject();

        String body = mockMvc.perform(post("/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"Write tests\",\"projectId\":" + projectId + "}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.status", is("TODO")))
                .andReturn().getResponse().getContentAsString();
        int taskId = JsonPath.read(body, "$.id");

        mockMvc.perform(patch("/tasks/" + taskId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"DONE\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status", is("DONE")));
    }

    @Test
    void listTasksFiltersByStatus() throws Exception {
        int projectId = createProject();
        mockMvc.perform(post("/tasks")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"title\":\"A\",\"projectId\":" + projectId + "}"));
        String bBody = mockMvc.perform(post("/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"B\",\"projectId\":" + projectId + "}"))
                .andReturn().getResponse().getContentAsString();
        int taskBId = JsonPath.read(bBody, "$.id");
        mockMvc.perform(patch("/tasks/" + taskBId)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"DONE\"}"));

        mockMvc.perform(get("/tasks").param("projectId", String.valueOf(projectId)).param("status", "DONE"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()", is(1)))
                .andExpect(jsonPath("$[0].id", is(taskBId)));
    }

    @Test
    void deleteTaskThenGetReturns404() throws Exception {
        int projectId = createProject();
        String body = mockMvc.perform(post("/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"temp\",\"projectId\":" + projectId + "}"))
                .andReturn().getResponse().getContentAsString();
        int taskId = JsonPath.read(body, "$.id");

        mockMvc.perform(delete("/tasks/" + taskId)).andExpect(status().isNoContent());
        mockMvc.perform(get("/tasks/" + taskId)).andExpect(status().isNotFound());
    }
}
