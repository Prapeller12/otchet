// .NET Framework bootstrap: executes before loading Python or WebView2 from SMB.
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;

internal static class NetworkBootstrap
{
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8388608 };
    private static DateTime Deadline;
    private static Form Dialog;
    private static Label Message;
    private static int Result = 1;
    private static string[] Arguments;
    private static string ReportPath;
    private static bool TestMode;
    private static Process Child;
    private static readonly object ChildGate = new object();
    private static volatile bool Cancelled;
    private static volatile bool HandedOff;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool GetVolumePathName(string path, StringBuilder volume, uint length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern uint GetDriveType(string root);

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try { Arguments = ParseArguments(args); }
        catch (Exception error) { MessageBox.Show(error.Message, "Не удалось запустить отчётность", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        Deadline = DateTime.UtcNow.AddSeconds(300);
        Dialog = new Form { Text = "Подготовка системы отчётности", Width = 490, Height = 150,
            StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
            MaximizeBox = false, MinimizeBox = false };
        Message = new Label { Text = "Проверка общей папки…", Dock = DockStyle.Fill, Padding = new Padding(16), AutoSize = false };
        Dialog.Controls.Add(Message);
        var worker = new BackgroundWorker();
        worker.DoWork += delegate { PrepareAndStart(); };
        worker.RunWorkerCompleted += delegate(object sender, RunWorkerCompletedEventArgs eventArgs) {
            if (eventArgs.Error != null) Fail(eventArgs.Error.GetBaseException().Message);
            else { Result = 0; Dialog.Close(); }
        };
        var timer = new System.Windows.Forms.Timer { Interval = 250 };
        timer.Tick += delegate {
            if (DateTime.UtcNow > Deadline) {
                Fail("Подготовка превысила время ожидания. Проверьте доступ к общей папке и повторите запуск.");
                Environment.Exit(1);
            }
        };
        Dialog.Shown += delegate { timer.Start(); worker.RunWorkerAsync(); };
        Dialog.FormClosing += delegate { timer.Stop(); if (!HandedOff) CancelOwnedChild(); };
        Application.Run(Dialog);
        return Result;
    }
    private static string[] ParseArguments(string[] args)
    {
        var forwarded = new List<string>();
        for (int i = 0; i < args.Length; i++) {
            if (args[i] == "--network-bootstrap-report") {
                if (++i >= args.Length) throw new ArgumentException("Не указан файл протокола проверки.");
                ReportPath = Path.GetFullPath(args[i]);
            } else {
                if (args[i] == "--network-self-test") TestMode = true;
                if (new[] { "--root", "--network-share-root", "--network-client-data", "--network-ready-file", "--network-ready-token", "--network-lock-timeout", "--ui-self-test", "--ui-reopen-test" }.Contains(args[i]))
                    throw new ArgumentException("Этот параметр зарезервирован для локального клиента.");
                forwarded.Add(args[i]);
            }
        }
        return forwarded.ToArray();
    }
    private static void Fail(string reason)
    {
        Result = 1;
        Deadline = DateTime.MaxValue;
        CancelOwnedChild();
        if (ReportPath != null) { try { Directory.CreateDirectory(Path.GetDirectoryName(ReportPath)); File.WriteAllText(ReportPath, Json.Serialize(new { result = "error", message = reason }), Encoding.UTF8); } catch (IOException) { } }
        MessageBox.Show(Dialog, reason, "Не удалось запустить отчётность", MessageBoxButtons.OK, MessageBoxIcon.Error);
        Dialog.Close();
    }
    private static void Status(string text)
    {
        CheckTime();
        Dialog.BeginInvoke((Action)delegate { Message.Text = text; });
    }
    private static void CancelOwnedChild() { lock (ChildGate) { Cancelled = true; if (Child != null && !Child.HasExited && !HandedOff) { try { Child.Kill(); } catch (InvalidOperationException) { } } } }
    private static void CheckTime() { if (Cancelled || DateTime.UtcNow > Deadline) throw new IOException("Истекло время подготовки клиента."); }
    private static string HashBytes(byte[] data) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    private static string HashFile(string path) { using (var stream = File.OpenRead(path)) using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(); }
    private static Dictionary<string, object> ReadJson(string path) { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)); }
    private static string CanonicalDirectory(string directory)
    {
        using (var handle = CreateFile(Path.GetFullPath(directory), 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new IOException("Общая или локальная папка недоступна: " + directory);
            var result = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(handle, result, (uint)result.Capacity, 0);
            if (length == 0 || length >= result.Capacity) throw new IOException("Не определён фактический путь папки.");
            string value = result.ToString();
            if (value.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + value.Substring(8).TrimEnd('\\');
            if (value.StartsWith(@"\\?\")) return value.Substring(4).TrimEnd('\\');
            return value.TrimEnd('\\');
        }
    }
    private static void RequireLocal(string directory)
    {
        string actual = CanonicalDirectory(directory);
        if (actual.StartsWith(@"\\")) throw new IOException("Клиент перенаправлен на сетевой диск.");
        var volume = new StringBuilder(32768);
        if (!GetVolumePathName(actual, volume, (uint)volume.Capacity) || (GetDriveType(volume.ToString()) != 3 && GetDriveType(volume.ToString()) != 2)) throw new IOException("Клиент должен находиться на локальном диске.");
    }
    private static string LocalBase()
    {
        foreach (var candidate in new[] { Environment.GetEnvironmentVariable("LOCALAPPDATA"),
            Path.Combine(Environment.GetEnvironmentVariable("USERPROFILE") ?? "", "AppData", "Local"), Path.GetTempPath() }) {
            if (String.IsNullOrWhiteSpace(candidate)) continue;
            try {
                Directory.CreateDirectory(candidate);
                string actual = CanonicalDirectory(candidate);
                if (actual.StartsWith(@"\\")) continue;
                var volume = new StringBuilder(32768);
                if (!GetVolumePathName(actual, volume, (uint)volume.Capacity)) continue;
                uint kind = GetDriveType(volume.ToString());
                if (kind != 3 && kind != 2) continue;
                string directory = Path.Combine(actual, "ReportingSystemNetwork");
                Directory.CreateDirectory(directory);
                string probe = Path.Combine(directory, ".probe-" + Guid.NewGuid().ToString("N"));
                File.WriteAllBytes(probe, new byte[0]); File.Delete(probe);
                return directory;
            } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
        throw new IOException("Не найдена доступная локальная папка для клиента. Проверьте TEMP и профиль Windows.");
    }
    private static FileStream Acquire(string path)
    {
        var stream = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
        try {
            while (true) {
                CheckTime();
                try { stream.Lock(0, 1); return stream; }
                catch (IOException) { Thread.Sleep(100); }
            }
        } catch { stream.Dispose(); throw; }
    }
    private static string SafePath(string root, string relative)
    {
        if (String.IsNullOrWhiteSpace(relative) || relative.Contains("\\") || relative.Contains(":") || relative.StartsWith("/") || relative.Split('/').Any(x => x == ".." || x == "." || x == ""))
            throw new IOException("Небезопасный путь в манифесте.");
        string path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase)) throw new IOException("Путь вышел за комплект клиента.");
        return path;
    }
    private static List<Dictionary<string, object>> ManifestEntries(byte[] data)
    {
        var manifest = Json.Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(data));
        if (Convert.ToInt32(manifest["format_version"]) != 1 || Convert.ToString(manifest["target"]) != "windows-x64" || Convert.ToString(manifest["application"]) != "reporting-system")
            throw new IOException("Формат комплекта клиента не поддерживается.");
        var entries = ((IEnumerable)manifest["files"]).Cast<Dictionary<string, object>>().ToList();
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in entries) {
            string name = Convert.ToString(entry["path"]);
            SafePath(Path.GetTempPath().TrimEnd('\\'), name);
            if (!names.Add(name) || new[] { "data/", "imports/", "exports/", "temp/", "backups/", "attachments/", "shared/" }.Any(p => name.StartsWith(p, StringComparison.OrdinalIgnoreCase)))
                throw new IOException("Манифест содержит повторный путь или пользовательские данные.");
            string checksum = Convert.ToString(entry["sha256"]);
            if (checksum.Length != 64 || checksum.Any(c => "0123456789abcdef".IndexOf(c) < 0) || Convert.ToInt64(entry["size"]) < 0)
                throw new IOException("Некорректный контроль целостности комплекта.");
        }
        foreach (string required in new[] { "ReportingSystem.exe", "VERSION", "app/frontend/index.html", "runtime/webview2/msedgewebview2.exe" })
            if (!names.Contains(required)) throw new IOException("Комплект клиента неполон: " + required);
        return entries;
    }
    private static IEnumerable<string> TreeFiles(string root)
    {
        var pending = new Stack<string>(); pending.Push(root);
        while (pending.Count != 0) {
            string directory = pending.Pop();
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) throw new IOException("Ссылка вместо папки клиента.");
            foreach (string child in Directory.GetDirectories(directory)) {
                if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0) throw new IOException("Ссылка вместо папки клиента.");
                pending.Push(child);
            }
            foreach (string file in Directory.GetFiles(directory)) {
                if ((File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0) throw new IOException("Ссылка вместо файла клиента.");
                yield return file;
            }
        }
    }
    private static bool Verified(string root, List<Dictionary<string, object>> entries, string manifestHash)
    {
        if (!Directory.Exists(root) || !File.Exists(Path.Combine(root, "release-manifest.json"))) return false;
        RequireLocal(root);
        var allowed = new HashSet<string>(entries.Select(e => SafePath(root, Convert.ToString(e["path"]))), StringComparer.OrdinalIgnoreCase);
        allowed.Add(Path.Combine(root, "release-manifest.json"));
        if (TreeFiles(root).Any(f => !allowed.Contains(f))) return false;
        if (HashFile(Path.Combine(root, "release-manifest.json")) != manifestHash) return false;
        foreach (var entry in entries) {
            CheckTime();
            string file = SafePath(root, Convert.ToString(entry["path"]));
            if (!File.Exists(file) || (File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0 || new FileInfo(file).Length != Convert.ToInt64(entry["size"]) || HashFile(file) != Convert.ToString(entry["sha256"])) return false;
        }
        return true;
    }
    private static string Quote(string argument) { return "\"" + System.Text.RegularExpressions.Regex.Replace(argument, "(\\\\*)\"", "$1$1\\\"").TrimEnd('\\') + new string('\\', argument.Reverse().TakeWhile(c => c == '\\').Count() * 2) + "\""; }
    private static void PrepareAndStart()
    {
        string source = CanonicalDirectory(AppDomain.CurrentDomain.BaseDirectory);
        var settings = ReadJson(Path.Combine(source, "config", "network.defaults.json"));
        string overrideFile = Path.Combine(source, "network.local.json");
        if (File.Exists(overrideFile)) foreach (var value in ReadJson(overrideFile)) settings[value.Key] = value.Value;
        if (Convert.ToInt32(settings["protocol_version"]) != 1) throw new IOException("Сетевая конфигурация другой версии.");
        int timeout = Convert.ToInt32(settings["startup_timeout_seconds"]);
        int lockTimeout = Convert.ToInt32(settings["writer_lock_timeout_seconds"]);
        if (timeout < 30 || timeout > 900 || lockTimeout < 1 || lockTimeout > 120) throw new IOException("Некорректный сетевой таймаут.");
        Deadline = DateTime.UtcNow.AddSeconds(timeout);
        string sharedValue = Convert.ToString(settings["shared_directory"]);
        if (String.IsNullOrWhiteSpace(sharedValue)) throw new IOException("Не указана общая папка данных.");
        string share = Path.GetFullPath(Path.IsPathRooted(sharedValue) ? sharedValue : Path.Combine(source, sharedValue));
        if (!Directory.Exists(share)) throw new IOException("Общая папка данных недоступна. Администратор должен создать папку: " + share);
        share = CanonicalDirectory(share);
        Status("Проверка версии и подготовка локальной копии…");
        byte[] manifest = File.ReadAllBytes(Path.Combine(source, "release-manifest.json"));
        string manifestHash = HashBytes(manifest);
        var entries = ManifestEntries(manifest);
        string identity = HashBytes(Encoding.UTF8.GetBytes(source.ToUpperInvariant() + "\n" + share.ToUpperInvariant()));
        string home = Path.Combine(LocalBase(), identity);
        Directory.CreateDirectory(home);
        RequireLocal(home);
        string versions = Path.Combine(home, "versions");
        Directory.CreateDirectory(versions);
        RequireLocal(versions);
        string client = Path.Combine(versions, manifestHash);
        using (var cacheLock = Acquire(Path.Combine(home, "prepare.lock"))) {
            bool verified = Verified(client, entries, manifestHash);
            if (!verified) {
                // A previously repaired immutable cache is reusable. Partial
                // staging directories never enter this exact UUID inventory.
                foreach (string alternative in Directory.GetDirectories(versions, manifestHash + "-*", SearchOption.TopDirectoryOnly).OrderBy(p => p, StringComparer.OrdinalIgnoreCase)) {
                    string suffix = Path.GetFileName(alternative).Substring(manifestHash.Length + 1);
                    Guid cacheId;
                    if (!Guid.TryParseExact(suffix, "N", out cacheId)) continue;
                    if (Verified(alternative, entries, manifestHash)) { client = alternative; verified = true; break; }
                }
            }
            if (!verified) {
                // A damaged/in-use directory is never replaced: activate a separate immutable copy.
                if (Directory.Exists(client)) client = client + "-" + Guid.NewGuid().ToString("N");
                string staging = client + ".staging-" + Guid.NewGuid().ToString("N");
                Directory.CreateDirectory(staging);
                foreach (var entry in entries) {
                    CheckTime();
                    string from = SafePath(source, Convert.ToString(entry["path"]));
                    if ((File.GetAttributes(from) & FileAttributes.ReparsePoint) != 0) throw new IOException("Ссылка вместо файла клиента.");
                    string to = SafePath(staging, Convert.ToString(entry["path"]));
                    Directory.CreateDirectory(Path.GetDirectoryName(to));
                    using (var input = File.OpenRead(from)) using (var output = new FileStream(to, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { input.CopyTo(output); output.Flush(true); }
                }
                File.WriteAllBytes(Path.Combine(staging, "release-manifest.json"), manifest);
                if (!Verified(staging, entries, manifestHash)) throw new IOException("Локальная копия не прошла проверку. Повторите запуск.");
                Directory.Move(staging, client);
            }
        }
        string state = Path.Combine(home, "state");
        Directory.CreateDirectory(state);
        RequireLocal(client);
        RequireLocal(state);
        string ready = Path.Combine(state, ".ready-" + Guid.NewGuid().ToString("N"));
        string token = Guid.NewGuid().ToString("N");
        Status("Открытие локального клиента и общей версии данных…");
        var args = new List<string>(Arguments);
        args.AddRange(new[] { "--network-share-root", share, "--network-client-data", state, "--network-lock-timeout", lockTimeout.ToString(), "--network-ready-file", ready, "--network-ready-token", token });
        lock (ChildGate) { CheckTime(); Child = Process.Start(new ProcessStartInfo(Path.Combine(client, "ReportingSystem.exe"), String.Join(" ", args.Select(Quote))) { WorkingDirectory = client, UseShellExecute = false }); }
        while (true) {
            CheckTime();
            if (TestMode && Child.HasExited) { if (Child.ExitCode != 0) throw new IOException("Сетевая проверка клиента завершилась ошибкой."); break; }
            if (!TestMode && File.Exists(ready) && File.ReadAllText(ready, Encoding.UTF8).Trim() == token) break;
            if (Child.HasExited) throw new IOException("Клиент завершился до открытия рабочего окна. Проверьте доступ и целостность комплекта.");
            Thread.Sleep(100);
        }
        HandedOff = true;
        if (File.Exists(ready)) File.Delete(ready);
        if (ReportPath != null) {
            Directory.CreateDirectory(Path.GetDirectoryName(ReportPath));
            File.WriteAllText(ReportPath, Json.Serialize(new { result = "ok", source = source, share = share, executable = Path.Combine(client, "ReportingSystem.exe"), state = state, manifest_sha256 = manifestHash, child_pid = Child.Id, native_preparation_window = true }), Encoding.UTF8);
        }
    }
}
