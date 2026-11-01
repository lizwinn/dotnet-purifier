var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();
app.MapGet("/", () => "hello");

if (args.Contains("--describe"))
{
    Console.WriteLine($"application: {app.Environment.ApplicationName}");
    return;
}

app.Run();
