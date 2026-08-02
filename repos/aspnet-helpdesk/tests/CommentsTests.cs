using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

public class CommentsTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly WebApplicationFactory<Program> _factory;

    public CommentsTests(WebApplicationFactory<Program> factory)
    {
        _factory = factory;
        Store.Reset();
    }

    [Fact]
    public async Task CommentsScopedToTicket()
    {
        var client = _factory.CreateClient();

        var t1resp = await client.PostAsJsonAsync("/api/tickets", new { subject = "A" });
        var t1 = await t1resp.Content.ReadFromJsonAsync<Ticket>();
        var t2resp = await client.PostAsJsonAsync("/api/tickets", new { subject = "B" });
        var t2 = await t2resp.Content.ReadFromJsonAsync<Ticket>();

        var missing = await client.PostAsJsonAsync($"/api/tickets/{t1!.Id}/comments", new { author = "", body = "hi" });
        Assert.Equal(HttpStatusCode.BadRequest, missing.StatusCode);

        await client.PostAsJsonAsync($"/api/tickets/{t1.Id}/comments", new { author = "Ada", body = "first" });
        await client.PostAsJsonAsync($"/api/tickets/{t1.Id}/comments", new { author = "Ada", body = "second" });
        await client.PostAsJsonAsync($"/api/tickets/{t2!.Id}/comments", new { author = "Bob", body = "other ticket" });

        var list1 = await client.GetFromJsonAsync<List<Comment>>($"/api/tickets/{t1.Id}/comments");
        Assert.Equal(2, list1!.Count);

        var notFound = await client.GetAsync("/api/tickets/999/comments");
        Assert.Equal(HttpStatusCode.NotFound, notFound.StatusCode);
    }
}
