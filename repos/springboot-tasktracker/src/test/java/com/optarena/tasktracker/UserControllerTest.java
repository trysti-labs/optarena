package com.optarena.tasktracker;

import static org.hamcrest.Matchers.is;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

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
class UserControllerTest {

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

    @Test
    void createAndGetUser() throws Exception {
        mockMvc.perform(post("/users")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"dev\",\"email\":\"dev@example.com\"}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.username", is("dev")))
                .andExpect(jsonPath("$.active", is(true)));

        mockMvc.perform(get("/users/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email", is("dev@example.com")));
    }

    @Test
    void getMissingUserReturns404() throws Exception {
        mockMvc.perform(get("/users/999"))
                .andExpect(status().isNotFound());
    }

    @Test
    void listUsersIsPaginated() throws Exception {
        for (int i = 0; i < 5; i++) {
            mockMvc.perform(post("/users")
                    .contentType(MediaType.APPLICATION_JSON)
                    .content("{\"username\":\"user" + i + "\",\"email\":\"user" + i + "@example.com\"}"));
        }

        mockMvc.perform(get("/users").param("skip", "0").param("limit", "3"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()", is(3)))
                .andExpect(jsonPath("$[0].username", is("user0")))
                .andExpect(jsonPath("$[2].username", is("user2")));

        mockMvc.perform(get("/users").param("skip", "3").param("limit", "3"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()", is(2)))
                .andExpect(jsonPath("$[0].username", is("user3")));
    }
}
