package com.optarena.inventory.controller

import com.optarena.inventory.model.Item
import com.optarena.inventory.repository.ItemRepository
import com.optarena.inventory.repository.WarehouseRepository
import org.springframework.http.HttpStatus
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PatchMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.ResponseStatus
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.server.ResponseStatusException

data class CreateItemRequest(
    val sku: String? = null,
    val name: String? = null,
    val warehouseId: Long? = null,
    val quantity: Int? = null,
    val reorderThreshold: Int? = null
)

data class UpdateItemRequest(val name: String? = null, val reorderThreshold: Int? = null)

@RestController
class ItemController(
    private val itemRepository: ItemRepository,
    private val warehouseRepository: WarehouseRepository
) {

    @PostMapping("/api/items")
    @ResponseStatus(HttpStatus.CREATED)
    fun createItem(@RequestBody body: CreateItemRequest): Item {
        val sku = body.sku?.trim().orEmpty()
        val name = body.name?.trim().orEmpty()
        if (sku.isEmpty()) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "sku is required")
        if (name.isEmpty()) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "name is required")
        val warehouseId = body.warehouseId
            ?: throw ResponseStatusException(HttpStatus.BAD_REQUEST, "warehouseId must reference an existing warehouse")
        if (warehouseRepository.findById(warehouseId) == null) {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "warehouseId must reference an existing warehouse")
        }
        val quantity = body.quantity ?: 0
        val reorderThreshold = body.reorderThreshold ?: 0
        if (quantity < 0) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "quantity must be a non-negative integer")
        if (reorderThreshold < 0) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "reorderThreshold must be a non-negative integer")
        if (itemRepository.findBySku(sku) != null) {
            throw ResponseStatusException(HttpStatus.CONFLICT, "sku already in use")
        }
        return itemRepository.save(
            Item(sku = sku, name = name, warehouseId = warehouseId, quantity = quantity, reorderThreshold = reorderThreshold)
        )
    }

    @GetMapping("/api/items")
    fun listItems(
        @RequestParam(required = false) warehouseId: Long?,
        @RequestParam(required = false) low: Boolean?
    ): List<Item> {
        var items = itemRepository.findAll()
        if (warehouseId != null) {
            items = items.filter { it.warehouseId == warehouseId }
        }
        if (low == true) {
            items = items.filter { it.quantity <= it.reorderThreshold }
        }
        return items
    }

    @GetMapping("/api/items/{id}")
    fun getItem(@PathVariable id: Long): Item =
        itemRepository.findById(id) ?: throw ResponseStatusException(HttpStatus.NOT_FOUND, "item not found")

    @PatchMapping("/api/items/{id}")
    fun updateItem(@PathVariable id: Long, @RequestBody body: UpdateItemRequest): Item {
        val item = itemRepository.findById(id) ?: throw ResponseStatusException(HttpStatus.NOT_FOUND, "item not found")
        if (body.name != null) {
            val trimmed = body.name.trim()
            if (trimmed.isEmpty()) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "name must be a non-empty string")
            item.name = trimmed
        }
        if (body.reorderThreshold != null) {
            if (body.reorderThreshold < 0) throw ResponseStatusException(HttpStatus.BAD_REQUEST, "reorderThreshold must be a non-negative integer")
            item.reorderThreshold = body.reorderThreshold
        }
        return itemRepository.save(item)
    }
}
