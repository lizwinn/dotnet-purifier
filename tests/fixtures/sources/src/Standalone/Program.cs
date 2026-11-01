using Example.Local;
using Newtonsoft.Json;

Console.WriteLine(JsonConvert.SerializeObject(new { origin = Origin.Name }));
