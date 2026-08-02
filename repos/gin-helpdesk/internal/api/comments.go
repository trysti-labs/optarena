package api

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"gin-helpdesk/internal/store"
)

func createComment(c *gin.Context) {
	ticketID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	if _, ok := store.GetTicket(ticketID); !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	var body struct {
		Author string `json:"author"`
		Body   string `json:"body"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	author := strings.TrimSpace(body.Author)
	text := strings.TrimSpace(body.Body)
	if author == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "author is required"})
		return
	}
	if text == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "body is required"})
		return
	}
	comment := store.CreateComment(ticketID, author, text, time.Now().UTC().Format(time.RFC3339Nano))
	c.JSON(http.StatusCreated, comment)
}

func listComments(c *gin.Context) {
	ticketID, err := strconv.Atoi(c.Param("id"))
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	if _, ok := store.GetTicket(ticketID); !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "ticket not found"})
		return
	}
	c.JSON(http.StatusOK, store.ListCommentsForTicket(ticketID))
}
