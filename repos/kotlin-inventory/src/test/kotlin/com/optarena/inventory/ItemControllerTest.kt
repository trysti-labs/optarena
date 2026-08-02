package com.optarena.inventory

import com.jayway.jsonpath.JsonPath
import com.optarena.inventory.repository.ItemRepository
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
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.status

@SpringBootTest
@AutoConfigureMockMvc
class ItemControllerTest {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var warehouseRepository: WarehouseRepository

    @Autowired
    private lateinit var itemRepository: ItemRepository

    @BeforeEach
    fun resetState() {
        warehouseRepository.clear()
        itemRepository.clear()
    }

    private fun createWarehouse(name: String = "Main DC"): Int {
        val body = mockMvc.perform(
            post("/api/warehouses")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"name":"$name","location":"x"}""")
        ).andReturn().response.contentAsString
        return JsonPath.read(body, "$.id")
    }

    @Test
    fun createItemRequiresExistingWarehouse() {
        mockMvc.perform(
            post("/api/items")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"SKU1","name":"Widget","warehouseId":999}""")
        ).andExpect(status().isBadRequest)

        val warehouseId = createWarehouse()
        mockMvc.perform(
            post("/api/items")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"SKU1","name":"Widget","warehouseId":$warehouseId,"quantity":5,"reorderThreshold":2}""")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.quantity", `is`(5)))

        mockMvc.perform(
            post("/api/items")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"SKU1","name":"Widget 2","warehouseId":$warehouseId}""")
        ).andExpect(status().isConflict)
    }

    @Test
    fun listItemsFiltersByWarehouseAndLowStock() {
        val w1 = createWarehouse("W1")
        val w2 = createWarehouse("W2")

        mockMvc.perform(
            post("/api/items").contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"A","name":"A","warehouseId":$w1,"quantity":1,"reorderThreshold":5}""")
        )
        mockMvc.perform(
            post("/api/items").contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"B","name":"B","warehouseId":$w1,"quantity":10,"reorderThreshold":5}""")
        )
        mockMvc.perform(
            post("/api/items").contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"C","name":"C","warehouseId":$w2,"quantity":1,"reorderThreshold":5}""")
        )

        mockMvc.perform(get("/api/items?warehouseId=$w1"))
            .andExpect(jsonPath("$.length()", `is`(2)))

        mockMvc.perform(get("/api/items?low=true"))
            .andExpect(jsonPath("$.length()", `is`(2)))

        mockMvc.perform(get("/api/items?warehouseId=$w1&low=true"))
            .andExpect(jsonPath("$.length()", `is`(1)))
            .andExpect(jsonPath("$[0].sku", `is`("A")))
    }

    @Test
    fun patchItemUpdatesNameAndReorderThresholdOnly() {
        val warehouseId = createWarehouse()
        val body = mockMvc.perform(
            post("/api/items").contentType(MediaType.APPLICATION_JSON)
                .content("""{"sku":"A","name":"A","warehouseId":$warehouseId,"quantity":3,"reorderThreshold":1}""")
        ).andReturn().response.contentAsString
        val itemId: Int = JsonPath.read(body, "$.id")

        mockMvc.perform(
            patch("/api/items/$itemId")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"name":"Renamed","reorderThreshold":9}""")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.name", `is`("Renamed")))
            .andExpect(jsonPath("$.reorderThreshold", `is`(9)))
            .andExpect(jsonPath("$.quantity", `is`(3)))
    }
}
