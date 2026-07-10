package com.optarena

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@SpringBootApplication
class KotlinApp

@RestController
class PingController {
    @GetMapping("/ping")
    fun ping(): Map<String, String> = mapOf("status" to "ok")
}

fun main(args: Array<String>) {
    runApplication<KotlinApp>(*args)
}
