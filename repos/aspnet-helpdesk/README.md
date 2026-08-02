# aspnet-helpdesk

A small helpdesk ticketing API (ASP.NET Core minimal APIs). Three entities:
agents, tickets (assignable to an agent), and comments (belong to a ticket).

Run tests with `dotnet test tests/tests.csproj`. Integration tests use
`WebApplicationFactory<Program>` against the real minimal-API pipeline, with
`Store.Reset()` called per test to start from a clean in-memory state.
