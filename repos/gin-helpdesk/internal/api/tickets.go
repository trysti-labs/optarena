package api

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"gin-helpdesk/internal/store"
)

var validStatuses = map[string]bool{"open": true, "in_progress": true, "closed": true}
var validPriorities = map[string]bool{"low": true, "normal": true, "high": true}

func createTicket(c *gin.Context) {
	var body struct {
		Subject  string `json:"subject"`
		Priority string `json:"priority"`
		AgentID  *int   `json:"agentId"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	subject := strings.TrimSpace(body.Subject)
	if subject == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "subject is required"})
		return
	}
	priority := body.Priority
	if priority == "" {
		priority = "normal"
	}
	if !validPriorities[priority] {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid priority"})
		return
	}
	agentID := 0
	if body.AgentID != nil {
		if _, ok := store.GetAgent(*body.AgentID); !ok {
			c.JSON(http.StatusBadRequest, gin.H{"error": "agentId must reference an existing agent"})
			return
		}
		agentID = *body.AgentID
	}
	ticket := store.CreateTicket(subject, priority, agentID)
	c.JSON(http.StatusCreated, ticket)
}

func listTickets(c *gin.Context) {
	tickets := store.ListTickets()
	if status := c.Query("status"); status != "" {
		filtered := tickets[:0:0]
		for _, t := range tickets {
			if t.Status == status {
				filtered = append(filtered, t)
			}
		}
		tickets = filtered
	}
	if agentIDStr := c.Query("agentId"); agentIDStr != "" {
		agentID, err := strconv.Atoi(agentIDStr)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "agentId must be an integer"})
			return
		}
		filtered := tickets[:0:0]
		for _, t := range tickets {
			if t.AgentID == agentID {
				filtered = append(filtered, t)
			}
		}
		tickets = filtered
	}
	c.JSON(http.StatusOK, tickets)
}

func getTicket(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	ticket, ok := store.GetTicket(id)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	c.JSON(http.StatusOK, ticket)
}

func updateTicket(c *gin.Context) {
	id, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	ticket, ok := store.GetTicket(id)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	var body struct {
		Status   *string `json:"status"`
		Priority *string `json:"priority"`
		AgentID  *int    `json:"agentId"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	if body.Status != nil {
		if !validStatuses[*body.Status] {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid status"})
			return
		}
		ticket.Status = *body.Status
	}
	if body.Priority != nil {
		if !validPriorities[*body.Priority] {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid priority"})
			return
		}
		ticket.Priority = *body.Priority
	}
	if body.AgentID != nil {
		if _, ok := store.GetAgent(*body.AgentID); !ok {
			c.JSON(http.StatusBadRequest, gin.H{"error": "agentId must reference an existing agent"})
			return
		}
		ticket.AgentID = *body.AgentID
	}
	c.JSON(http.StatusOK, ticket)
}
