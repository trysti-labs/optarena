public static class Store
{
    private static Dictionary<int, Agent> _agents = new();
    private static Dictionary<int, Ticket> _tickets = new();
    private static Dictionary<int, Comment> _comments = new();
    private static int _nextAgentId = 1;
    private static int _nextTicketId = 1;
    private static int _nextCommentId = 1;

    public static Agent CreateAgent(string name, string email)
    {
        var agent = new Agent { Id = _nextAgentId++, Name = name, Email = email, Active = true };
        _agents[agent.Id] = agent;
        return agent;
    }

    public static Agent? GetAgent(int id) => _agents.TryGetValue(id, out var a) ? a : null;

    public static List<Agent> ListAgents() => _agents.Values.OrderBy(a => a.Id).ToList();

    public static Ticket CreateTicket(string subject, string priority, int agentId)
    {
        var ticket = new Ticket { Id = _nextTicketId++, Subject = subject, Status = "open", Priority = priority, AgentId = agentId };
        _tickets[ticket.Id] = ticket;
        return ticket;
    }

    public static Ticket? GetTicket(int id) => _tickets.TryGetValue(id, out var t) ? t : null;

    public static List<Ticket> ListTickets() => _tickets.Values.OrderBy(t => t.Id).ToList();

    public static Comment CreateComment(int ticketId, string author, string body, string createdAt)
    {
        var comment = new Comment { Id = _nextCommentId++, TicketId = ticketId, Author = author, Body = body, CreatedAt = createdAt };
        _comments[comment.Id] = comment;
        return comment;
    }

    public static List<Comment> ListCommentsForTicket(int ticketId) =>
        _comments.Values.Where(c => c.TicketId == ticketId).OrderBy(c => c.Id).ToList();

    /// <summary>Test-only hook: wipes all state so each test starts clean.</summary>
    public static void Reset()
    {
        _agents = new();
        _tickets = new();
        _comments = new();
        _nextAgentId = 1;
        _nextTicketId = 1;
        _nextCommentId = 1;
    }
}
