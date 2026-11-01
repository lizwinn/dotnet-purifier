using Example.Greeter;
using Xunit;

public class GreetingTests
{
    [Fact]
    public void CountsMessages() =>
        Assert.Equal("Hello, Ada! You have 3 messages.", Greeting.For("Ada", 3));
}
