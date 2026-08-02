var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

var validStatuses = new HashSet<string> { "open", "in_progress", "closed" };
var validPriorities = new HashSet<string> { "low", "normal", "high" };

app.MapPost("/api/agents", (CreateAgentRequest body) =>
{
    var name = (body.Name ?? "").Trim();
    var email = (body.Email ?? "").Trim();
    if (name.Length == 0) return Results.BadRequest(new { error = "name is required" });
    if (email.Length == 0) return Results.BadRequest(new { error = "email is required" });

    var agent = Store.CreateAgent(name, email);
    return Results.Created($"/api/agents/{agent.Id}", agent);
});

app.MapGet("/api/agents", () => Results.Ok(Store.ListAgents()));

app.MapGet("/api/agents/{id:int}", (int id) =>
{
    var agent = Store.GetAgent(id);
    return agent is null ? Results.NotFound(new { error = "agent not found" }) : Results.Ok(agent);
});

app.MapPost("/api/tickets", (CreateTicketRequest body) =>
{
    var subject = (body.Subject ?? "").Trim();
    if (subject.Length == 0) return Results.BadRequest(new { error = "subject is required" });

    var priority = string.IsNullOrEmpty(body.Priority) ? "normal" : body.Priority;
    if (!validPriorities.Contains(priority)) return Results.BadRequest(new { error = "invalid priority" });

    var agentId = 0;
    if (body.AgentId.HasValue)
    {
        if (Store.GetAgent(body.AgentId.Value) is null)
            return Results.BadRequest(new { error = "agentId must reference an existing agent" });
        agentId = body.AgentId.Value;
    }

    var ticket = Store.CreateTicket(subject, priority, agentId);
    return Results.Created($"/api/tickets/{ticket.Id}", ticket);
});

app.MapGet("/api/tickets", (string? status, int? agentId) =>
{
    var tickets = Store.ListTickets();
    if (!string.IsNullOrEmpty(status)) tickets = tickets.Where(t => t.Status == status).ToList();
    if (agentId.HasValue) tickets = tickets.Where(t => t.AgentId == agentId.Value).ToList();
    return Results.Ok(tickets);
});

app.MapGet("/api/tickets/{id:int}", (int id) =>
{
    var ticket = Store.GetTicket(id);
    return ticket is null ? Results.NotFound(new { error = "ticket not found" }) : Results.Ok(ticket);
});

app.MapPatch("/api/tickets/{id:int}", (int id, UpdateTicketRequest body) =>
{
    var ticket = Store.GetTicket(id);
    if (ticket is null) return Results.NotFound(new { error = "ticket not found" });

    if (body.Status is not null)
    {
        if (!validStatuses.Contains(body.Status)) return Results.BadRequest(new { error = "invalid status" });
        ticket.Status = body.Status;
    }
    if (body.Priority is not null)
    {
        if (!validPriorities.Contains(body.Priority)) return Results.BadRequest(new { error = "invalid priority" });
        ticket.Priority = body.Priority;
    }
    if (body.AgentId.HasValue)
    {
        if (Store.GetAgent(body.AgentId.Value) is null)
            return Results.BadRequest(new { error = "agentId must reference an existing agent" });
        ticket.AgentId = body.AgentId.Value;
    }
    return Results.Ok(ticket);
});

app.MapPost("/api/tickets/{id:int}/comments", (int id, CreateCommentRequest body) =>
{
    if (Store.GetTicket(id) is null) return Results.NotFound(new { error = "ticket not found" });

    var author = (body.Author ?? "").Trim();
    var text = (body.Body ?? "").Trim();
    if (author.Length == 0) return Results.BadRequest(new { error = "author is required" });
    if (text.Length == 0) return Results.BadRequest(new { error = "body is required" });

    var comment = Store.CreateComment(id, author, text, DateTime.UtcNow.ToString("o"));
    return Results.Created($"/api/tickets/{id}/comments/{comment.Id}", comment);
});

app.MapGet("/api/tickets/{id:int}/comments", (int id) =>
{
    if (Store.GetTicket(id) is null) return Results.NotFound(new { error = "ticket not found" });
    return Results.Ok(Store.ListCommentsForTicket(id));
});

app.Run();

public record CreateAgentRequest(string? Name, string? Email);
public record CreateTicketRequest(string? Subject, string? Priority, int? AgentId);
public record UpdateTicketRequest(string? Status, string? Priority, int? AgentId);
public record CreateCommentRequest(string? Author, string? Body);

public partial class Program { }
