using Example.Greeter;
using Newtonsoft.Json;

var name = args.Length > 0 ? args[0] : "world";
var greeting = Greeting.For(name, 3);
Console.WriteLine(JsonConvert.SerializeObject(new { greeting }));
