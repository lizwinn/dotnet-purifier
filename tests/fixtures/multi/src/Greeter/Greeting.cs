using Humanizer;

namespace Example.Greeter;

public static class Greeting
{
    public static string For(string name, int count) =>
        $"Hello, {name}! You have {"message".ToQuantity(count)}.";
}
