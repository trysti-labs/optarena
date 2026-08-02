package com.optarena.inventory.controller

import com.optarena.inventory.model.Item
import com.optarena.inventory.model.Movement
import com.optarena.inventory.repository.ItemRepository
import com.optarena.inventory.repository.MovementRepository
import com.optarena.inventory.service.InventoryService
import org.springframework.http.HttpStatus
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PathVariable
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.ResponseStatus
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.server.ResponseStatusException

data class CreateMovementRequest(val type: String? = null, val quantity: Int? = null, val note: String? = null)

data class MovementResult(val movement: Movement, val item: Item)

@RestController
class MovementController(
    private val itemRepository: ItemRepository,
    private val movementRepository: MovementRepository,
    private val inventoryService: InventoryService
) {

    @PostMapping("/api/items/{id}/movements")
    @ResponseStatus(HttpStatus.CREATED)
    fun createMovement(@PathVariable id: Long, @RequestBody body: CreateMovementRequest): MovementResult {
        if (itemRepository.findById(id) == null) {
            throw ResponseStatusException(HttpStatus.NOT_FOUND, "item not found")
        }
        val movement = inventoryService.applyMovement(id, body.type.orEmpty(), body.quantity ?: 0, body.note)
        val item = itemRepository.findById(id)!!
        return MovementResult(movement, item)
    }

    @GetMapping("/api/items/{id}/movements")
    fun listMovements(@PathVariable id: Long): List<Movement> {
        if (itemRepository.findById(id) == null) {
            throw ResponseStatusException(HttpStatus.NOT_FOUND, "item not found")
        }
        return movementRepository.findForItem(id)
    }
}
