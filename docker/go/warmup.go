package main

import (
	"github.com/gin-gonic/gin"
	"github.com/gofiber/fiber/v2"
)

func main() {
	r := gin.Default()
	r.GET("/health", func(c *gin.Context) {
		c.JSON(200, gin.H{"status": "ok"})
	})
	_ = r

	f := fiber.New()
	f.Get("/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "ok"})
	})
	_ = f
}
