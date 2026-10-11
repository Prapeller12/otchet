// Executable acceptance probe, compiled outside the pristine client package.
using System;
using System.IO;
using System.Reflection;
using System.Text;

internal static class ReadyMarkerProbe
{
    private static int Main(string[] args)
    {
        string marker = Path.Combine(args[1], "ready-marker-probe.tmp");
        try {
            var type = Assembly.LoadFrom(args[0]).GetType("NetworkBootstrap", true);
            var ready = type.GetMethod("Ready", BindingFlags.NonPublic | BindingFlags.Static);
            Func<string, bool> read = token => (bool)ready.Invoke(null, new object[] { marker, token });
            File.WriteAllText(marker, "exact-token", Encoding.UTF8);
            using (var held = new FileStream(marker, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) {
                if (read("exact-token")) throw new Exception("Locked marker acknowledged readiness.");
            }
            if (!read("exact-token")) throw new Exception("Unlocked complete marker was rejected.");
            if (read("different-token")) throw new Exception("Wrong token was accepted.");
            File.WriteAllText(marker, "exact-", Encoding.UTF8);
            if (read("exact-token")) throw new Exception("Partial token was accepted.");
            File.Delete(marker);
            if (read("exact-token")) throw new Exception("Missing marker was accepted.");
            File.WriteAllText(Path.Combine(args[1], "readiness-lock-result.json"),
                "{\"ok\":true,\"checks\":[\"exclusive_lock_retries\",\"unlock_ready\",\"wrong_token_rejected\",\"partial_token_rejected\",\"missing_marker_rejected\"]}", Encoding.UTF8);
            return 0;
        } catch (Exception error) {
            Console.Error.WriteLine(error);
            return 1;
        } finally {
            if (File.Exists(marker)) File.Delete(marker);
        }
    }
}
