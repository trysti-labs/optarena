package com.optarena.inventory.service

import com.optarena.inventory.model.Movement
import com.optarena.inventory.repository.ItemRepository
import com.optarena.inventory.repository.MovementRepository
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Service
import org.springframework.web.server.ResponseStatusException

@Service
class InventoryService(
    private val itemRepository: ItemRepository,
    private val movementRepository: MovementRepository
) {
    fun applyMovement(itemId: Long, type: String, quantity: Int, note: String?): Movement {
        val item = itemRepository.findById(itemId)
            ?: throw ResponseStatusException(HttpStatus.NOT_FOUND, "item not found")
        if (type != "IN" && type != "OUT") {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "type must be IN or OUT")
        }
        if (quantity <= 0) {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "quantity must be a positive integer")
        }
        val delta = if (type == "IN") quantity else -quantity
        val newQuantity = item.quantity + delta
        if (newQuantity < 0) {
            throw ResponseStatusException(HttpStatus.BAD_REQUEST, "movement would take quantity below zero")
        }
        item.quantity = newQuantity
        itemRepository.save(item)
        return movementRepository.save(Movement(itemId = itemId, type = type, quantity = quantity, note = note))
    }
}
