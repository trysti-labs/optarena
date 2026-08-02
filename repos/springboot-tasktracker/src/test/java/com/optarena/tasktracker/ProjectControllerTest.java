package com.optarena.tasktracker;

import static org.hamcrest.Matchers.is;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
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
class ProjectControllerTest {

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

    private int createUser(String username) throws Exception {
        String body = mockMvc.perform(post("/users")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"" + username + "\",\"email\":\"" + username + "@example.com\"}"))
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.id");
    }

    @Test
    void createAndGetProject() throws Exception {
        int userId = createUser("owner");

        mockMvc.perform(post("/projects")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"Mercury\",\"ownerId\":" + userId + "}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.name", is("Mercury")))
                .andExpect(jsonPath("$.ownerId", is(userId)));

        mockMvc.perform(get("/projects/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name", is("Mercury")));
    }

    @Test
    void getMissingProjectReturns404() throws Exception {
        mockMvc.perform(get("/projects/999"))
                .andExpect(status().isNotFound());
    }

    @Test
    void projectProgressAggregatesTaskStatuses() throws Exception {
        int userId = createUser("dev");
        String projBody = mockMvc.perform(post("/projects")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"name\":\"Venus\",\"ownerId\":" + userId + "}"))
                .andReturn().getResponse().getContentAsString();
        int projectId = JsonPath.read(projBody, "$.id");

        for (int i = 0; i < 3; i++) {
            mockMvc.perform(post("/tasks")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content("{\"title\":\"t" + i + "\",\"projectId\":" + projectId + "}"));
        }
        String taskBody = mockMvc.perform(post("/tasks")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"done-task\",\"projectId\":" + projectId + "}"))
                .andReturn().getResponse().getContentAsString();
        int doneTaskId = JsonPath.read(taskBody, "$.id");
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                        .patch("/tasks/" + doneTaskId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"DONE\"}"));

        mockMvc.perform(get("/projects/" + projectId + "/progress"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total", is(4)))
                .andExpect(jsonPath("$.done", is(1)))
                .andExpect(jsonPath("$.todo", is(3)));
    }
}
