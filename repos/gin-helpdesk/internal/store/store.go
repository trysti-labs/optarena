// Package store holds the in-memory data for gin-helpdesk: agents, tickets,
// and comments. There is no database - state lives in process memory and
// Reset() is a test-only hook to start each test from a clean slate.
package store

import "sort"

type Agent struct {
	ID     int    `json:"id"`
	Name   string `json:"name"`
	Email  string `json:"email"`
	Active bool   `json:"active"`
}

type Ticket struct {
	ID       int    `json:"id"`
	Subject  string `json:"subject"`
	Status   string `json:"status"`
	Priority string `json:"priority"`
	AgentID  int    `json:"agentId"`
}

type Comment struct {
	ID        int    `json:"id"`
	TicketID  int    `json:"ticketId"`
	Author    string `json:"author"`
	Body      string `json:"body"`
	CreatedAt string `json:"createdAt"`
}

var (
	agents      = map[int]*Agent{}
	tickets     = map[int]*Ticket{}
	comments    = map[int]*Comment{}
	nextAgentID = 1
	nextTicket  = 1
	nextComment = 1
)

func CreateAgent(name, email string) *Agent {
	a := &Agent{ID: nextAgentID, Name: name, Email: email, Active: true}
	agents[a.ID] = a
	nextAgentID++
	return a
}

func GetAgent(id int) (*Agent, bool) {
	a, ok := agents[id]
	return a, ok
}

func ListAgents() []*Agent {
	out := make([]*Agent, 0, len(agents))
	for _, a := range agents {
		out = append(out, a)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func CreateTicket(subject, priority string, agentID int) *Ticket {
	t := &Ticket{ID: nextTicket, Subject: subject, Status: "open", Priority: priority, AgentID: agentID}
	tickets[t.ID] = t
	nextTicket++
	return t
}

func GetTicket(id int) (*Ticket, bool) {
	t, ok := tickets[id]
	return t, ok
}

func ListTickets() []*Ticket {
	out := make([]*Ticket, 0, len(tickets))
	for _, t := range tickets {
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func CreateComment(ticketID int, author, body, createdAt string) *Comment {
	c := &Comment{ID: nextComment, TicketID: ticketID, Author: author, Body: body, CreatedAt: createdAt}
	comments[c.ID] = c
	nextComment++
	return c
}

func ListCommentsForTicket(ticketID int) []*Comment {
	out := make([]*Comment, 0)
	for _, c := range comments {
		if c.TicketID == ticketID {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Reset wipes all in-memory state. Test-only hook: each test file starts
// from an empty store.
func Reset() {
	agents = map[int]*Agent{}
	tickets = map[int]*Ticket{}
	comments = map[int]*Comment{}
	nextAgentID = 1
	nextTicket = 1
	nextComment = 1
}
