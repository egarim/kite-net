// kite: the Kite.NET compiler and runner (#91).
//   kite check prog.kite [--json]     errors and warnings: Kite analyzer (KT), C# compiler (CS), .NET analyzers (CA)
//   kite run   prog.kite              compile and run main()
//   kite test  prog.kite [--json]     compile and run every `test` block
//   kite build prog.kite -o prog.dll  write the assembly and its prog.pdb (step through Kite lines in a debugger)
//   kite emit  prog.kite              print the generated C# (debugging the compiler)
using System.Collections.Immutable;
using System.Globalization;
using System.Reflection;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.Diagnostics;
using Microsoft.CodeAnalysis.Emit;
using Microsoft.CodeAnalysis.Text;

namespace Kite;

public static class Program
{
    // .NET analyzer rules that fight Kite's own style (snake_case names, records with lowercase fields, one
    // static class per module), and CS0105: kite adds the usings to plain C#, so a program's own are no fault;
    // everything else runs at its default severity
    static readonly string[] Off = ["CS0105", "CA1707", "CA1050", "CA1716", "CA1052", "CA1515", "CA1305", "CA1310", "CA1307"];
    // off by default in the .NET SDK, on for Kite: dead code, wasted work, lost results
    static readonly string[] On = ["CA1508", "CA1806", "CA1829", "CA1851", "CA1854", "CA1860", "CA1862", "CA1847",
        "CA1865", "CA1866", "CA1867", "CA2201", "CA1825", "CA1826", "CA1827", "CA1828"];

    public static int Main(string[] args)
    {
        CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
        CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
        if (args.Length < 2) { Console.Error.WriteLine("usage: kite check|run|test|build|emit <file.kite> [--json] [-o out.dll]"); return 2; }
        var (cmd, path) = (args[0], args[1]);
        bool json = args.Contains("--json");
        var source = File.ReadAllText(path);
        var file = Path.GetFileName(path);
        // .cs files are plain C# (the "not dumber" check, #91): same compiler, analyzers and test runner
        var (cs, front) = path.EndsWith(".cs") ? (CSharp(source, file), Front.Lower("", file).Front) : Front.Lower(source, file);
        if (cmd == "emit") { Console.Write(cs); return 0; }

        var dir = Path.GetDirectoryName(Path.GetFullPath(path))!;
        var (compilation, diags) = Compile(cs, file, front, dir);
        var errors = diags.Count(d => d.Severity == "error");
        var report = new Dictionary<string, object?>
        {
            ["file"] = file,
            ["ok"] = errors == 0,
            ["errors"] = errors,
            ["warnings"] = diags.Count(d => d.Severity == "warning"),
            ["diagnostics"] = diags,
        };
        if (cmd == "check" || errors > 0)
        {
            Print(report, json);
            return errors == 0 ? 0 : 1;
        }

        // Debug symbols: the #line markers already map every statement to its Kite line, so a portable PDB is all
        // a debugger needs to put breakpoints and stepping on the .kite file. The Kite source is embedded in it.
        var outPath = args.SkipWhile(a => a != "-o").Skip(1).FirstOrDefault() ?? Path.ChangeExtension(path, ".dll");
        using var ms = new MemoryStream();
        using var pdb = new MemoryStream();
        var emit = compilation.Emit(ms, pdb,
            options: new EmitOptions(debugInformationFormat: DebugInformationFormat.PortablePdb,
                pdbFilePath: Path.GetFileName(Path.ChangeExtension(outPath, ".pdb"))),
            embeddedTexts: [EmbeddedText.FromSource(Path.GetFullPath(path), SourceText.From(source, Encoding.UTF8))]);
        if (!emit.Success) { foreach (var d in emit.Diagnostics) Console.Error.WriteLine(d); report["ok"] = false; Print(report, json); return 1; }
        if (cmd == "build")
        {
            File.WriteAllBytes(outPath, ms.ToArray());
            File.WriteAllBytes(Path.ChangeExtension(outPath, ".pdb"), pdb.ToArray());
            // a program with main() also gets the runtimeconfig `dotnet prog.dll` (and so a debugger) needs to start it
            if (front.HasMain)
                File.WriteAllText(Path.ChangeExtension(outPath, ".runtimeconfig.json"),
                    $"{{\"runtimeOptions\":{{\"tfm\":\"net{Environment.Version.Major}.0\",\"framework\":{{\"name\":\"Microsoft.NETCore.App\",\"version\":\"{Environment.Version}\"}}}}}}");
            Console.WriteLine($"wrote {outPath}");
            return 0;
        }
        ms.Position = 0;
        pdb.Position = 0;
        var asm = new AssemblyLoadContext("kite", isCollectible: true).LoadFromStream(ms, pdb);
        if (cmd == "run")
        {
            var main = asm.GetTypes().SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Static)).FirstOrDefault(m => m.Name == "Main");
            if (main == null) { Console.Error.WriteLine("no fn main()"); return 1; }
            try { main.Invoke(null, null); return 0; }
            catch (TargetInvocationException e)
            {
                // the PDB is loaded with the assembly, so the stack trace knows the Kite line that failed
                var at = new System.Diagnostics.StackTrace(e.InnerException!, true).GetFrames()
                    .FirstOrDefault(f => f.GetFileName()?.EndsWith(".kite") == true);
                Console.Error.WriteLine($"error: {e.InnerException?.Message}" + (at == null ? "" : $" ({Path.GetFileName(at.GetFileName())}:{at.GetFileLineNumber()})"));
                return 1;
            }
        }
        if (cmd == "test")
        {
            var results = RunTests(asm);
            report["tests"] = results;
            report["passed"] = results.Count(r => (bool)r["pass"]!);
            report["ok"] = results.Count > 0 && results.All(r => (bool)r["pass"]!);
            Print(report, json);
            return (bool)report["ok"]! ? 0 : 1;
        }
        Console.Error.WriteLine($"unknown command {cmd}");
        return 2;
    }

    static string CSharp(string source, string file) =>
        "#nullable enable\nusing System;\nusing System.Collections.Generic;\nusing System.Linq;\nusing KiteStd;\n" +
        $"using static KiteStd.Std;\n#line 1 \"{file}\"\n" + source;

    static List<Dictionary<string, object?>> RunTests(Assembly asm)
    {
        var tests = asm.GetTypes().SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Static))
            .Select(m => (m, a: m.GetCustomAttributes().FirstOrDefault(x => x.GetType().Name == "KiteTestAttribute")))
            .Where(x => x.a != null).ToList();
        var results = new List<Dictionary<string, object?>>();
        foreach (var (m, a) in tests)
        {
            var name = (string)a!.GetType().GetProperty("Name")!.GetValue(a)!;
            string? error = null;
            var run = Task.Run(() =>
            {
                try { m.Invoke(null, null); }
                catch (TargetInvocationException e) { error = e.InnerException?.Message ?? e.Message; }
            });
            if (!run.Wait(TimeSpan.FromSeconds(5))) error = "timed out after 5 s";
            results.Add(new() { ["name"] = name, ["pass"] = error == null, ["error"] = error });
            if (error == "timed out after 5 s") break;      // the thread is still spinning: stop here
        }
        return results;
    }

    static (CSharpCompilation, List<Diag>) Compile(string cs, string file, Front front, string dir)
    {
        var std = new StreamReader(typeof(Program).Assembly.GetManifestResourceStream("Std.cs")!).ReadToEnd();
        var opts = CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest);
        var trees = new[] { CSharpSyntaxTree.ParseText(SourceText.From(cs, Encoding.UTF8), opts, path: file + ".cs"),
            CSharpSyntaxTree.ParseText(SourceText.From(std, Encoding.UTF8), opts, path: "Std.cs") }; // encoding: needed for debug info
        var refs = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator)
            .Select(p => MetadataReference.CreateFromFile(p));
        var compilation = CSharpCompilation.Create("KiteApp", trees, refs, new CSharpCompilationOptions(
                front.HasMain ? OutputKind.ConsoleApplication : OutputKind.DynamicallyLinkedLibrary,
                nullableContextOptions: NullableContextOptions.Enable,
                specificDiagnosticOptions: Off.Select(x => (x, ReportDiagnostic.Suppress))
                    .Concat(On.Select(x => (x, ReportDiagnostic.Warn))).ToDictionary(x => x.Item1, x => x.Item2))
            // the PDB names each #line file by its full path, so a debugger finds the .kite file it was compiled from
            .WithSourceReferenceResolver(new SourceFileResolver(ImmutableArray<string>.Empty, dir)));

        var diags = new List<Diag>(front.Diags);
        // a Kite syntax error makes Roslyn cascade into dozens of C# errors: show only Kite's own message then
        if (front.Diags.Any(d => d.Severity == "error" && d.Id is "KT000" or "KT004"))
            return (compilation, diags.OrderBy(d => d.Line).ToList());
        var found = compilation.GetDiagnostics().ToList();
        var analyzers = Analyzers();
        if (Environment.GetEnvironmentVariable("KITE_DEBUG") == "1")
            Console.Error.WriteLine($"{analyzers.Length} analyzers; rules on as warnings: " + string.Join(" ", analyzers
                .SelectMany(x => x.SupportedDiagnostics).Where(d => d.IsEnabledByDefault && d.DefaultSeverity >= DiagnosticSeverity.Warning)
                .Select(d => d.Id).Distinct().Order()));
        if (analyzers.Length > 0 && !found.Any(d => d.Severity == DiagnosticSeverity.Error))
        {
            var ad = compilation.WithAnalyzers(analyzers).GetAnalyzerDiagnosticsAsync().Result;
            if (Environment.GetEnvironmentVariable("KITE_DEBUG") == "1")
                foreach (var d in ad) Console.Error.WriteLine($"  analyzer: {d.Id} {d.Severity} {d.GetMessage()}");
            found.AddRange(ad);
        }
        foreach (var d in found.Where(d => d.Severity >= DiagnosticSeverity.Warning))
        {
            var span = d.Location.GetMappedLineSpan();
            if (span.Path == "Std.cs") continue;                 // the standard library's own warnings are ours, not the program's
            var line = span.HasMappedPath && span.Path == file ? span.StartLinePosition.Line + 1 : 0;
            diags.Add(new Diag(d.Id, d.Severity == DiagnosticSeverity.Error ? "error" : "warning", line, d.GetMessage(CultureInfo.InvariantCulture)));
        }
        return (compilation, diags.OrderBy(d => d.Line).ToList());
    }

    static ImmutableArray<DiagnosticAnalyzer> Analyzers()
    {
        var dir = Path.Combine(AppContext.BaseDirectory, "analyzers");
        if (!Directory.Exists(dir)) return [];
        var loader = new Loader();
        return [.. Directory.GetFiles(dir, "*.dll")
            .SelectMany(p => new AnalyzerFileReference(p, loader).GetAnalyzers(LanguageNames.CSharp))];
    }

    sealed class Loader : IAnalyzerAssemblyLoader
    {
        public void AddDependencyLocation(string fullPath) { }
        public Assembly LoadFromPath(string fullPath) => Assembly.LoadFrom(fullPath);
    }

    static void Print(Dictionary<string, object?> report, bool json)
    {
        if (json)
        {
            Console.WriteLine(JsonSerializer.Serialize(report, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase }));
            return;
        }
        foreach (var d in (List<Diag>)report["diagnostics"]!)
            Console.WriteLine($"{report["file"]}({d.Line}): {d.Severity} {d.Id}: {d.Message}");
        if (report.TryGetValue("tests", out var t) && t is List<Dictionary<string, object?>> tests)
            foreach (var r in tests)
                Console.WriteLine($"  {((bool)r["pass"]! ? "pass" : "FAIL")}  {r["name"]}{(r["error"] is string e ? ": " + e : "")}");
        Console.WriteLine($"{(((bool)report["ok"]!) ? "ok" : "failed")}: {report["errors"]} errors, {report["warnings"]} warnings");
    }
}
