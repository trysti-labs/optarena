package com.optarena.inventory.controller

import com.optarena.inventory.model.Warehouse
import com.optarena.inventory.repository.WarehouseRepository
import org.springframework.http.HttpStatus
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.ResponseStatus
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.server.ResponseStatusException

data class CreateWarehouseRequest(val name: String? = null, val location: String? = null)

@RestController
class WarehouseController(private val warehouseRepository: WarehouseRepository) {

    @PostMapping("/api/warehouses")
    @ResponseStatus(HttpStatus.CREATED)
    fun createWarehouse(@RequestBody body: CreateWarehouseRequest): Warehouse {
        val name = body.name?.trim().orEmpty()
        val location = body.location?.trim().orEmpty()
        if (name.isEmpty()) {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "name is required")
        }
        if (location.isEmpty()) {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "location is required")
        }
        return warehouseRepository.save(Warehouse(name = name, location = location))
    }

    @GetMapping("/api/warehouses")
    fun listWarehouses(): List<Warehouse> = warehouseRepository.findAll()

    @GetMapping("/api/warehouses/{id}")
    fun getWarehouse(@PathVariable id: Long): Warehouse =
        warehouseRepository.findById(id) ?: throw ResponseStatusException(HttpStatus.NOT_FOUND, "warehouse not found")
}
