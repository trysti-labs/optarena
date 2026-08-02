public class Agent
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public string Email { get; set; } = "";
    public bool Active { get; set; } = true;
}

public class Ticket
{
    public int Id { get; set; }
    public string Subject { get; set; } = "";
    public string Status { get; set; } = "open";
    public string Priority { get; set; } = "normal";
    public int AgentId { get; set; }
}

public class Comment
{
    public int Id { get; set; }
    public int TicketId { get; set; }
    public string Author { get; set; } = "";
    public string Body { get; set; } = "";
    public string CreatedAt { get; set; } = "";
}
