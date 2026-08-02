package com.optarena.inventory.repository

import com.optarena.inventory.model.Item
import com.optarena.inventory.model.Movement
import com.optarena.inventory.model.Warehouse
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import org.springframework.stereotype.Repository

@Repository
class WarehouseRepository {
    private val byId = ConcurrentHashMap<Long, Warehouse>()
    private val nextId = AtomicLong(1)

    fun save(warehouse: Warehouse): Warehouse {
        if (warehouse.id == null) {
            warehouse.id = nextId.getAndIncrement()
        }
        byId[warehouse.id!!] = warehouse
        return warehouse
    }

    fun findById(id: Long): Warehouse? = byId[id]

    fun findAll(): List<Warehouse> = byId.values.sortedBy { it.id }

    fun clear() {
        byId.clear()
        nextId.set(1)
    }
}

@Repository
class ItemRepository {
    private val byId = ConcurrentHashMap<Long, Item>()
    private val nextId = AtomicLong(1)

    fun save(item: Item): Item {
        if (item.id == null) {
            item.id = nextId.getAndIncrement()
        }
        byId[item.id!!] = item
        return item
    }

    fun findById(id: Long): Item? = byId[id]

    fun findBySku(sku: String): Item? = byId.values.find { it.sku == sku }

    fun findAll(): List<Item> = byId.values.sortedBy { it.id }

    fun clear() {
        byId.clear()
        nextId.set(1)
    }
}

@Repository
class MovementRepository {
    private val byId = ConcurrentHashMap<Long, Movement>()
    private val nextId = AtomicLong(1)

    fun save(movement: Movement): Movement {
        if (movement.id == null) {
            movement.id = nextId.getAndIncrement()
        }
        byId[movement.id!!] = movement
        return movement
    }

    fun findForItem(itemId: Long): List<Movement> =
        byId.values.filter { it.itemId == itemId }.sortedByDescending { it.id }

    fun clear() {
        byId.clear()
        nextId.set(1)
    }
}
