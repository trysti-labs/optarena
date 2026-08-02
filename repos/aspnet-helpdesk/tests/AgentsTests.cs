using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

public class AgentsTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public AgentsTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        Store.Reset();
    }

    [Fact]
    public async Task CreateAndListAgents()
    {
        var client = _factory.CreateClient();

        var created = await client.PostAsJsonAsync("/api/agents", new { name = "Ada", email = "ada@example.com" });
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var agent = await created.Content.ReadFromJsonAsync<Agent>();
        Assert.Equal("Ada", agent!.Name);

        var missingEmail = await client.PostAsJsonAsync("/api/agents", new { name = "Bob" });
        Assert.Equal(HttpStatusCode.BadRequest, missingEmail.StatusCode);

        var list = await client.GetFromJsonAsync<List<Agent>>("/api/agents");
        Assert.Single(list!);

        var notFound = await client.GetAsync("/api/agents/999");
        Assert.Equal(HttpStatusCode.NotFound, notFound.StatusCode);
    }
}
