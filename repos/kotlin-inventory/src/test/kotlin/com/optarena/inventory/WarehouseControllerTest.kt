package com.optarena.inventory

import com.optarena.inventory.repository.WarehouseRepository
import org.hamcrest.Matchers.`is`
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

@SpringBootTest
@AutoConfigureMockMvc
class WarehouseControllerTest {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var warehouseRepository: WarehouseRepository

    @BeforeEach
    fun resetState() {
        warehouseRepository.clear()
    }

    @Test
    fun createAndListWarehouses() {
        mockMvc.perform(
            post("/api/warehouses")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"name":"Main DC","location":"Austin, TX"}""")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.name", `is`("Main DC")))

        mockMvc.perform(
            post("/api/warehouses")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"location":"x"}""")
        ).andExpect(status().isBadRequest)

        mockMvc.perform(get("/api/warehouses"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.length()", `is`(1)))

        mockMvc.perform(get("/api/warehouses/999")).andExpect(status().isNotFound)
    }
}
