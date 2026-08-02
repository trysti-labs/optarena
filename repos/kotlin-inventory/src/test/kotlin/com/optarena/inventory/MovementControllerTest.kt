package com.optarena.inventory

import com.jayway.jsonpath.JsonPath
import com.optarena.inventory.repository.ItemRepository
import com.optarena.inventory.repository.MovementRepository
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
class MovementControllerTest {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var warehouseRepository: WarehouseRepository

    @Autowired
    private lateinit var itemRepository: ItemRepository

    @Autowired
    private lateinit var movementRepository: MovementRepository

    @BeforeEach
    fun resetState() {
        warehouseRepository.clear()
        itemRepository.clear()
        movementRepository.clear()
    }

    private fun createItem(quantity: Int = 5): Int {
        val wBody = mockMvc.perform(
            post("/api/warehouses").contentType(MediaType.APPLICATION_JSON)
                .content("""{"name":"W","location":"x"}""")
        ).andReturn().response.contentAsString
        val warehouseId: Int = JsonPath.read(wBody, "$.id")

        val sku = "SKU-${System.nanoTime()}"
        val iBody = mockMvc.perform(
            post("/api/items").contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"$sku","name":"Widget","warehouseId":$warehouseId,"quantity":$quantity,"reorderThreshold":0}""")
        ).andReturn().response.contentAsString
        return JsonPath.read(iBody, "$.id")
    }

    @Test
    fun inMovementIncreasesOutDecreases() {
        val itemId = createItem(5)

        mockMvc.perform(
            post("/api/items/$itemId/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"IN","quantity":3}""")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.item.quantity", `is`(8)))

        mockMvc.perform(
            post("/api/items/$itemId/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"OUT","quantity":2}""")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.item.quantity", `is`(6)))
    }

    @Test
    fun outMovementCannotTakeQuantityBelowZero() {
        val itemId = createItem(2)

        mockMvc.perform(
            post("/api/items/$itemId/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"OUT","quantity":5}""")
        ).andExpect(status().isBadRequest)

        mockMvc.perform(get("/api/items/$itemId"))
            .andExpect(jsonPath("$.quantity", `is`(2)))
    }

    @Test
    fun movementsListIsNewestFirstAndScopedToItem() {
        val itemA = createItem(5)
        val itemB = createItem(5)

        mockMvc.perform(
            post("/api/items/$itemA/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"IN","quantity":1}""")
        )
        mockMvc.perform(
            post("/api/items/$itemA/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"IN","quantity":2}""")
        )
        mockMvc.perform(
            post("/api/items/$itemB/movements").contentType(MediaType.APPLICATION_JSON)
                .content("""{"type":"IN","quantity":9}""")
        )

        mockMvc.perform(get("/api/items/$itemA/movements"))
            .andExpect(jsonPath("$.length()", `is`(2)))
            .andExpect(jsonPath("$[0].quantity", `is`(2)))
            .andExpect(jsonPath("$[1].quantity", `is`(1)))
    }
}
