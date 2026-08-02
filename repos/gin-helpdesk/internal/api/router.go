// Package api wires the gin routes for gin-helpdesk.
package api

import "github.com/gin-gonic/gin"

func NewRouter() *gin.Engine {
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(gin.Recovery())

	r.POST("/api/agents", createAgent)
	r.GET("/api/agents", listAgents)
	r.GET("/api/agents/:id", getAgent)

	r.POST("/api/tickets", createTicket)
	r.GET("/api/tickets", listTickets)
	r.GET("/api/tickets/:id", getTicket)
	r.PATCH("/api/tickets/:id", updateTicket)

	r.POST("/api/tickets/:id/comments", createComment)
	r.GET("/api/tickets/:id/comments", listComments)

	return r
}
