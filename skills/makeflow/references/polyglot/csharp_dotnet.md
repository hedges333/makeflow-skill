# Polyglot Stack Guide: C# (.NET)

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for C# and .NET.

## 1. Detection Markers

Look for these files when scanning a .NET solution or project:

- `*.csproj`, `*.sln`, `global.json`, `Directory.Build.props`

## 2. Standard Commands

Run these commands inside the .NET project or solution directory:

- Targeted Test: `dotnet test --filter <FullyQualifiedName>`
- Format: `dotnet format --verify-no-changes`
- Lint and Build: `dotnet build /warnaserror`

## 3. Smoke Test Template

If no test project exists, create `tests/SmokeTests.cs` with xUnit:

```csharp
using Xunit;

namespace Tests;

public class SmokeTests
{
    [Fact]
    public void SmokeTestHarness()
    {
        Assert.True(true);
    }
}
```

## 4. Test Database Isolation

Follow three database safety rules in .NET tests:

1. Use Entity Framework Core `UseInMemoryDatabase("TestDb")` or SQLite `:memory:` connections for unit tests.
2. For SQL Server or PostgreSQL integration tests, start isolated containers with Testcontainers for .NET.
3. Point `appsettings.Testing.json` only to local test databases, never to live servers.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place domain models and class libraries in `src/<Project>/`. Keep domain projects independent of web or CLI hosts.
- **Executable Entrypoints and Controllers**: Keep `Program.cs` and host controllers thin inside `src/<Project>.Api/` or `src/<Project>.Cli/`. Reference domain class libraries one-way.
- **Test Suite Placement and Naming**: Place xUnit or NUnit test projects in `tests/<Project>.Tests/` with `*Tests.cs` files mirroring the source namespaces.
- **Structural Anti-Patterns to Flag at Target 15**: Flag business logic inside `Program.cs` or controllers, circular `.csproj` references, or test classes inside `src/`.
