using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

public class TicketsTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public TicketsTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        Store.Reset();
    }

    private static async Task<int> MakeAgentAsync(HttpClient client)
    {
        var resp = await client.PostAsJsonAsync("/api/agents", new { name = "Ada", email = "ada@example.com" });
        var agent = await resp.Content.ReadFromJsonAsync<Agent>();
        return agent!.Id;
    }

    [Fact]
    public async Task CreateTicketValidatesAgent()
    {
        var client = _factory.CreateClient();

        var bad = await client.PostAsJsonAsync("/api/tickets", new { subject = "Help", agentId = 999 });
        Assert.Equal(HttpStatusCode.BadRequest, bad.StatusCode);

        var agentId = await MakeAgentAsync(client);
        var created = await client.PostAsJsonAsync("/api/tickets", new { subject = "Help", agentId });
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var ticket = await created.Content.ReadFromJsonAsync<Ticket>();
        Assert.Equal("open", ticket!.Status);
    }

    [Fact]
    public async Task ListTicketsFiltersByStatusAndAgent()
    {
        var client = _factory.CreateClient();
        var a1 = await MakeAgentAsync(client);
        var a2 = await MakeAgentAsync(client);

        var t1resp = await client.PostAsJsonAsync("/api/tickets", new { subject = "A", agentId = a1 });
        var t1 = await t1resp.Content.ReadFromJsonAsync<Ticket>();
        await client.PostAsJsonAsync("/api/tickets", new { subject = "B", agentId = a1 });
        await client.PostAsJsonAsync("/api/tickets", new { subject = "C", agentId = a2 });

        await client.PatchAsJsonAsync($"/api/tickets/{t1!.Id}", new { status = "closed" });

        var byAgent = await client.GetFromJsonAsync<List<Ticket>>($"/api/tickets?agentId={a1}");
        Assert.Equal(2, byAgent!.Count);

        var byStatus = await client.GetFromJsonAsync<List<Ticket>>("/api/tickets?status=closed");
        Assert.Single(byStatus!);
    }

    [Fact]
    public async Task UpdateTicketValidatesStatusAndPriority()
    {
        var client = _factory.CreateClient();
        var createdResp = await client.PostAsJsonAsync("/api/tickets", new { subject = "A" });
        var created = await createdResp.Content.ReadFromJsonAsync<Ticket>();

        var badStatus = await client.PatchAsJsonAsync($"/api/tickets/{created!.Id}", new { status = "bogus" });
        Assert.Equal(HttpStatusCode.BadRequest, badStatus.StatusCode);

        var ok = await client.PatchAsJsonAsync($"/api/tickets/{created.Id}", new { priority = "high" });
        Assert.Equal(HttpStatusCode.OK, ok.StatusCode);
        var updated = await ok.Content.ReadFromJsonAsync<Ticket>();
        Assert.Equal("high", updated!.Priority);
    }
}
