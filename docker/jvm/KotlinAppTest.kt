package com.optarena

import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

@SpringBootTest
@AutoConfigureMockMvc
class KotlinAppTest {

    @Autowired
    lateinit var mvc: MockMvc

    @Test
    fun pingWorks() {
        mvc.perform(get("/ping"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("\$.status").value("ok"))
    }
}
