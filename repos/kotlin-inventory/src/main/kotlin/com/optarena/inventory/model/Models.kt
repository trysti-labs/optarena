package com.optarena.inventory.model

data class Warehouse(
    var id: Long? = null,
    var name: String = "",
    var location: String = ""
)

data class Item(
    var id: Long? = null,
    var sku: String = "",
    var name: String = "",
    var warehouseId: Long = 0,
    var quantity: Int = 0,
    var reorderThreshold: Int = 0
)

data class Movement(
    var id: Long? = null,
    var itemId: Long = 0,
    var type: String = "",
    var quantity: Int = 0,
    var note: String? = null
)
