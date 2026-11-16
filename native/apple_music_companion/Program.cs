using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32.SafeHandles;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace GluSpot
{
    // Windows companion. Apple's own music.apple.com page owns login, DRM and
    // playback. This process only forwards one JSON line at a time.
    internal static class CompanionLog
    {
        private static readonly Regex SecretAssignment = new Regex(
            "(?i)\\b(?:token|cookie|password|authorization|secret)s?\\b\\s*[=:]\\s*\\S+",
            RegexOptions.Compiled);
        private static readonly Regex SecretWord = new Regex(
            "(?i)\\b(?:token|cookie|password|authorization|secret)s?\\b",
            RegexOptions.Compiled);

        public static string Safe(string value)
        {
            if (string.IsNullOrEmpty(value))
                return "";
            var chars = new char[value.Length];
            var count = 0;
            foreach (var ch in value)
            {
                if (ch == '\r' || ch == '\n' || ch == '\t' || ch == '"' || ch == '\\' || ch < 32)
                    chars[count++] = ' ';
                else
                    chars[count++] = ch;
            }
            var text = SecretAssignment.Replace(new string(chars, 0, count), "(redacted)");
            text = SecretWord.Replace(text, "(redacted)");
            text = Regex.Replace(text, " {2,}", " ").Trim();
            if (text.Length > 400)
                text = text.Substring(0, 400);
            return text;
        }

        public static void Write(string line)
        {
            var safe = Safe(line);
            try
            {
                var root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (string.IsNullOrEmpty(root))
                    root = Path.GetTempPath();
                var dir = Path.Combine(root, "GluSpot");
                Directory.CreateDirectory(dir);
                var path = Path.Combine(dir, "apple-music-companion.log");
                File.AppendAllText(path, DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ") + " " + safe + Environment.NewLine);
            }
            catch (Exception) {}
            try
            {
                Console.Error.WriteLine(safe);
                Console.Error.Flush();
            }
            catch (Exception) {}
        }
    }

    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            // The previous build only created this file inside the init-failure
            // catch. A process that started and then returned runtime never
            // left apple-music-companion.log on disk.
            CompanionLog.Write("started");
            try
            {
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new HostForm());
            }
            catch (Exception ex)
            {
                CompanionLog.Write("start-failed " + ex.GetType().FullName + " " + ex.Message);
                Environment.Exit(1);
            }
        }
    }

    internal sealed class HostForm : Form
    {
        private readonly WebView2 view = new WebView2();
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();
        private readonly object gate = new object();
        private readonly Queue<string> queued = new Queue<string>();
        // Every delivered, unanswered request by id. The parent polls status
        // every second while a lyric or playlist read is still running; one
        // shared slot let the poll overwrite the slower read, whose answer was
        // then dropped, timed out and reloaded the page under the music.
        private readonly Dictionary<string, PendingQuery> pending = new Dictionary<string, PendingQuery>();
        private readonly List<string> pendingOrder = new List<string>();

        private sealed class PendingQuery
        {
            public string Op = "";
            public string Line = "";
        }

        // Only a stuck control query means the page itself is wedged. A slow
        // data read times out on its own and leaves playback alone.
        private static readonly HashSet<string> ReloadOnTimeout = new HashSet<string> { "account", "transport", "now_playing" };
        private bool navigationReady;
        private bool scriptReady;
        private bool loginVisible;
        private bool startupHidden;
        private int navigationFailures;
        // A hidden window does not keep a WinForms timer running if the UI
        // thread is inside WebView. This clock does not need window messages.
        private readonly System.Threading.Timer queryClock;
        private readonly Dictionary<string, DateTime> queryDeadline = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, DateTime> queryStarted = new Dictionary<string, DateTime>();
        private readonly Dictionary<string, string> queryOp = new Dictionary<string, string>();

        public HostForm()
        {
            Text = "GluSpot Apple Music";
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            var screen = Screen.PrimaryScreen;
            var area = screen == null ? new System.Drawing.Rectangle(0, 0, 800, 600) : screen.WorkingArea;
            // Keep the first show on a real monitor. CreateAsync fails when the
            // window is created far off the desktop. Playback moves off-screen
            // only after the view exists.
            Location = new System.Drawing.Point(area.Left + 40, area.Top + 40);
            ClientSize = new System.Drawing.Size(320, 180);
            Controls.Add(view);
            view.Dock = DockStyle.Fill;
            // A library or account read has 20 seconds. The page posts the
            // result. Login waits for the person and is not on this clock.
            queryClock = new System.Threading.Timer(OnQueryClock, null, 250, 250);
            Load += OnLoad;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetStdHandle(int nStdHandle);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool WriteFile(IntPtr handle, byte[] buffer, int count, out int written, IntPtr overlapped);

        private const int StdInputHandle = -10;
        private const int StdOutputHandle = -11;

        private async void OnLoad(object sender, EventArgs e)
        {
            var profile = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "GluSpot", "apple-music-profile");
            var reader = new Thread(ReadInput) { IsBackground = true };
            reader.Start();
            try
            {
                EnsureWritableProfile(profile);
                var mismatch = LoaderMismatch();
                if (mismatch != null)
                    throw new InvalidOperationException(mismatch);
                // Hidden or off-screen pages must still load and play media. Chromium
                // otherwise marks the page occluded and defers media loading.
                var options = new CoreWebView2EnvironmentOptions(
                    "--disable-features=CalculateNativeWinOcclusion " +
                    "--disable-background-media-suspend " +
                    "--autoplay-policy=no-user-gesture-required");
                var environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
                await view.EnsureCoreWebView2Async(environment);
                view.CoreWebView2.Settings.AreDevToolsEnabled = false;
                view.CoreWebView2.Settings.IsStatusBarEnabled = false;
                view.CoreWebView2.NavigationStarting += OnNavigationStarting;
                view.CoreWebView2.NavigationCompleted += OnNavigationCompleted;
                view.CoreWebView2.ProcessFailed += OnProcessFailed;
                view.CoreWebView2.WebMessageReceived += OnWebMessage;
                var scriptPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "page_bridge.js");
                var script = File.ReadAllText(scriptPath);
                await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(script);
                CompanionLog.Write("trace stage=inject");
                view.CoreWebView2.Navigate("https://music.apple.com/");
            }
            catch (Exception ex)
            {
                ReportInitFailure(ex, profile);
            }
        }

        private void ReportInitFailure(Exception ex, string profile)
        {
            var detail = Describe(ex, profile);
            CompanionLog.Write("init-failed " + detail);
            try
            {
                Write(new Dictionary<string, object> {
                    {"v", 1},
                    {"id", ""},
                    {"ok", false},
                    {"error", "runtime"},
                    {"exception", ex.GetType().FullName ?? "Exception"},
                    {"message", detail}
                });
            }
            catch (Exception) {}
            // Application.Run would keep the window alive after this catch.
            // Exit so the next launch can retry instead of hanging.
            Environment.Exit(1);
        }

        private static void EnsureWritableProfile(string profile)
        {
            Directory.CreateDirectory(profile);
            var probe = Path.Combine(profile, "write-probe.tmp");
            File.WriteAllText(probe, "ok");
            File.Delete(probe);
        }

        private static string LoaderMismatch()
        {
            var loader = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "WebView2Loader.dll");
            if (!File.Exists(loader))
                return "WebView2Loader.dll is missing beside the executable";
            var machine = PeMachine(loader);
            var process64 = Environment.Is64BitProcess;
            if (machine == 0x8664 && !process64)
                return "process is x86 but WebView2Loader.dll is x64. Build with PlatformTarget x64 and Prefer32Bit false.";
            if (machine == 0x014c && process64)
                return "process is x64 but WebView2Loader.dll is x86.";
            if (machine != 0x8664 && machine != 0x014c)
                return "WebView2Loader.dll has an unknown machine type.";
            return null;
        }

        private static ushort PeMachine(string path)
        {
            using (var stream = File.OpenRead(path))
            using (var reader = new BinaryReader(stream))
            {
                if (stream.Length < 64)
                    return 0;
                stream.Position = 0x3C;
                var pe = reader.ReadInt32();
                if (pe < 0 || pe + 6L > stream.Length)
                    return 0;
                stream.Position = pe;
                if (reader.ReadUInt32() != 0x00004550)
                    return 0;
                return reader.ReadUInt16();
            }
        }

        private static string Describe(Exception ex, string profile)
        {
            var parts = new List<string>();
            var depth = 0;
            for (var current = ex; current != null && depth < 4; current = current.InnerException, depth++)
                parts.Add(current.GetType().FullName + ": " + current.Message);
            var text = string.Join(" | ", parts.ToArray());
            text += " process=" + (Environment.Is64BitProcess ? "x64" : "x86");
            text += " profile=" + profile;
            text += Hint(ex);
            return CompanionLog.Safe(text);
        }

        private static string Hint(Exception ex)
        {
            for (var current = ex; current != null; current = current.InnerException)
            {
                var name = current.GetType().Name;
                if (name.IndexOf("WebView2RuntimeNotFound", StringComparison.Ordinal) >= 0)
                    return " hint=install-webview2-evergreen";
                if (current is UnauthorizedAccessException)
                    return " hint=profile-folder-not-writable";
                if (current is BadImageFormatException || current is DllNotFoundException)
                    return " hint=webview2loader-bitness";
            }
            return "";
        }

        private void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            Uri uri;
            if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out uri) || uri.Scheme != Uri.UriSchemeHttps)
            {
                e.Cancel = true;
                return;
            }
            var host = uri.Host;
            if (host != "apple.com" && !host.EndsWith(".apple.com", StringComparison.OrdinalIgnoreCase))
            {
                e.Cancel = true;
                return;
            }
            if (string.Equals(host, "music.apple.com", StringComparison.OrdinalIgnoreCase))
            {
                navigationReady = false;
                scriptReady = false;
                RequeuePending();
            }
        }

        private void OnNavigationCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
        {
            if (!e.IsSuccess)
            {
                navigationFailures += 1;
                CompanionLog.Write("navigation-failed status=" + e.WebErrorStatus);
                if (navigationFailures <= 2 && view.CoreWebView2 != null)
                    view.CoreWebView2.Navigate("https://music.apple.com/");
                return;
            }
            navigationFailures = 0;
            navigationReady = true;
            TryFlush();
        }

        private void OnProcessFailed(object sender, CoreWebView2ProcessFailedEventArgs e)
        {
            CompanionLog.Write("process-failed kind=" + e.ProcessFailedKind);
        }

        private bool PageReady()
        {
            return navigationReady && scriptReady && view.CoreWebView2 != null;
        }

        private bool HasQueuedLogin()
        {
            lock (gate)
            {
                foreach (var line in queued)
                {
                    if (IsLoginShow(line))
                        return true;
                }
            }
            return false;
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                ShowInTaskbar = false;
                if (loginVisible)
                {
                    loginVisible = false;
                    CompanionLog.Write("event=login-window-closed");
                    SettleLoginClosed();
                }
                return;
            }
            base.OnFormClosing(e);
        }

        private void ReadInput()
        {
            // WinExe has no console. Console.OpenStandardInput misses the
            // pipe the parent redirected, so read that handle directly.
            var handle = GetStdHandle(StdInputHandle);
            if (handle == IntPtr.Zero || handle == new IntPtr(-1))
            {
                CompanionLog.Write("trace stage=stdin error=missing");
                return;
            }
            var owned = new SafeFileHandle(handle, false);
            var stream = new FileStream(owned, FileAccess.Read, 4096, false);
            var input = new StreamReader(stream, Encoding.UTF8);
            var readerJson = new JavaScriptSerializer();
            string line;
            while ((line = input.ReadLine()) != null)
            {
                var copy = line;
                string id = "";
                string op = "";
                try
                {
                    var request = readerJson.Deserialize<Dictionary<string, object>>(line);
                    if (request != null)
                    {
                        id = request.ContainsKey("id") ? Convert.ToString(request["id"]) : "";
                        op = request.ContainsKey("op") ? Convert.ToString(request["op"]) : "";
                    }
                }
                catch (Exception) {}
                CompanionLog.Write("trace stage=stdin id=" + SafeToken(id) + " op=" + SafeToken(op));
                try
                {
                    if (!IsHandleCreated)
                    {
                        lock (gate)
                            queued.Enqueue(copy);
                        continue;
                    }
                    BeginInvoke(new Action(() => Dispatch(copy)));
                }
                catch (Exception)
                {
                    CompanionLog.Write("trace stage=dispatch error=invoke id=" + SafeToken(id) + " op=" + SafeToken(op));
                    lock (gate)
                        queued.Enqueue(copy);
                }
            }
            // The parent closed its end of the pipe. Leave on our own.
            try { BeginInvoke(new Action(ExitAfterParent)); }
            catch (Exception) { Environment.Exit(0); }
        }

        private void ExitAfterParent()
        {
            try { queryClock.Dispose(); } catch (Exception) {}
            CompanionLog.Write("parent-closed");
            Environment.Exit(0);
        }

        private void ArmQuery(string id, string op)
        {
            if (op == "login_window" || string.IsNullOrEmpty(id))
                return;
            lock (gate)
            {
                var now = DateTime.UtcNow;
                queryStarted[id] = now;
                queryDeadline[id] = now.AddSeconds(20);
                queryOp[id] = op;
            }
        }

        private void OnQueryClock(object state)
        {
            var due = new List<string>();
            var elapsed = new Dictionary<string, int>();
            var ops = new Dictionary<string, string>();
            lock (gate)
            {
                var now = DateTime.UtcNow;
                foreach (var pair in queryDeadline)
                {
                    if (pair.Value > now)
                        continue;
                    due.Add(pair.Key);
                    DateTime started;
                    elapsed[pair.Key] = queryStarted.TryGetValue(pair.Key, out started)
                        ? (int)Math.Max(0, (now - started).TotalMilliseconds) : 20000;
                    string op;
                    ops[pair.Key] = queryOp.TryGetValue(pair.Key, out op) ? op : "";
                }
                foreach (var id in due)
                {
                    queryDeadline.Remove(id);
                    queryStarted.Remove(id);
                    queryOp.Remove(id);
                }
            }
            foreach (var id in due)
                ExpireQuery(id, ops[id], elapsed[id]);
        }

        private void ExpireQuery(string id, string op, int ms)
        {
            DropQueued(id);
            lock (gate)
                RemovePending(id);
            var reload = ReloadOnTimeout.Contains(op ?? "");
            CompanionLog.Write(reload ? "query-timeout reload" : "query-timeout op=" + SafeToken(op));
            var message = Error(id, "timeout");
            message["op"] = op ?? "";
            Write(message, ms);
            if (!reload)
                return;
            try
            {
                if (!IsHandleCreated)
                    return;
                BeginInvoke(new Action(ReloadAfterTimeout));
            }
            catch (Exception)
            {
                CompanionLog.Write("trace stage=reload error=invoke");
            }
        }

        private void ReloadAfterTimeout()
        {
            navigationReady = false;
            scriptReady = false;
            try
            {
                if (view.CoreWebView2 != null)
                    view.CoreWebView2.Navigate("https://music.apple.com/");
            }
            catch (Exception) {}
        }

        private void DropQueued(string id)
        {
            lock (gate)
            {
                var kept = new Queue<string>();
                while (queued.Count > 0)
                {
                    var line = queued.Dequeue();
                    if (ReadId(line) != id)
                        kept.Enqueue(line);
                }
                while (kept.Count > 0)
                    queued.Enqueue(kept.Dequeue());
            }
        }

        private void Dispatch(string line)
        {
            var id = ReadId(line);
            var op = ReadOp(line);
            CompanionLog.Write("trace stage=dispatch id=" + SafeToken(id) + " op=" + SafeToken(op));
            ArmQuery(id, op);
            if (!PageReady())
            {
                lock (gate)
                    queued.Enqueue(line);
                return;
            }
            Deliver(line);
        }

        private void TryFlush()
        {
            if (!PageReady())
                return;
            if (!startupHidden && !loginVisible && !HasQueuedLogin())
            {
                startupHidden = true;
                Hide();
            }
            while (true)
            {
                string line;
                lock (gate)
                {
                    if (queued.Count == 0)
                        return;
                    line = queued.Dequeue();
                }
                Deliver(line);
            }
        }

        private void Deliver(string line)
        {
            if (view.CoreWebView2 == null)
            {
                Write(Error(ReadId(line), "runtime"));
                return;
            }
            var op = ReadOp(line);
            var id = ReadId(line);
            lock (gate)
            {
                if (!string.IsNullOrEmpty(id))
                {
                    if (!pending.ContainsKey(id))
                        pendingOrder.Add(id);
                    pending[id] = new PendingQuery { Op = op, Line = line };
                }
            }
            if (op == "login_window" && !IsLoginHide(line))
                ShowLogin();
            else if (op == "transport")
                ShowPlayback();
            else if (op == "sign_out")
                Hide();
            // The page listener posts ready only after it is installed.
            // PostWebMessage before that is dropped, so this stays behind PageReady.
            view.CoreWebView2.PostWebMessageAsJson(line);
            CompanionLog.Write("trace stage=deliver id=" + SafeToken(id) + " op=" + SafeToken(op));
        }

        private void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            Dictionary<string, object> message;
            try { message = json.Deserialize<Dictionary<string, object>>(e.WebMessageAsJson); }
            catch (Exception) { return; }
            var op = message.ContainsKey("op") ? Convert.ToString(message["op"]) : "";
            if (op == "ready")
            {
                scriptReady = true;
                CompanionLog.Write("page-ready");
                TryFlush();
                return;
            }
            if (op == "page_error")
            {
                var detail = message.ContainsKey("message") ? Convert.ToString(message["message"]) : "";
                CompanionLog.Write("page-error " + detail);
                return;
            }
            var id = message.ContainsKey("id") ? Convert.ToString(message["id"]) : "";
            PendingQuery answered;
            lock (gate)
            {
                if (string.IsNullOrEmpty(id) || !pending.TryGetValue(id, out answered))
                    return;
                RemovePending(id);
            }
            if (answered.Op == "sign_out")
            {
                try { view.CoreWebView2.Profile.ClearBrowsingDataAsync(); } catch (Exception) {}
            }
            var answeredOp = answered.Op;
            ClearQuery(id);
            var ok = message.ContainsKey("ok") && message["ok"] is bool && (bool)message["ok"];
            var error = message.ContainsKey("error") ? Convert.ToString(message["error"]) : "";
            CompanionLog.Write("trace stage=post id=" + SafeToken(id) + " op=" + SafeToken(answeredOp) + " ok=" + (ok ? "true" : "false") + " error=" + SafeToken(error));
            message["op"] = answeredOp;
            Write(message, 0);
        }

        private void RequeuePending()
        {
            // A redirect replaces the document before its script can answer.
            // Put the unanswered lines back at the front, in the order they
            // were sent, and wait for the next ready.
            lock (gate)
            {
                if (pendingOrder.Count == 0)
                    return;
                var kept = new Queue<string>();
                foreach (var id in pendingOrder)
                {
                    PendingQuery query;
                    if (pending.TryGetValue(id, out query) && !string.IsNullOrEmpty(query.Line))
                        kept.Enqueue(query.Line);
                }
                pending.Clear();
                pendingOrder.Clear();
                while (queued.Count > 0)
                    kept.Enqueue(queued.Dequeue());
                while (kept.Count > 0)
                    queued.Enqueue(kept.Dequeue());
            }
        }

        private void SettleLoginClosed()
        {
            var closing = new List<string>();
            lock (gate)
            {
                var kept = new Queue<string>();
                while (queued.Count > 0)
                {
                    var line = queued.Dequeue();
                    if (IsLoginShow(line))
                        closing.Add(line);
                    else
                        kept.Enqueue(line);
                }
                while (kept.Count > 0)
                    queued.Enqueue(kept.Dequeue());
            }
            foreach (var line in closing)
                ReplyClosed(ReadId(line));
            var open = new List<string>();
            lock (gate)
            {
                foreach (var id in pendingOrder)
                {
                    PendingQuery query;
                    if (pending.TryGetValue(id, out query) && IsLoginShow(query.Line))
                        open.Add(id);
                }
            }
            foreach (var id in open)
                ReplyClosed(id);
        }

        private void RemovePending(string id)
        {
            // Callers hold gate.
            pending.Remove(id);
            pendingOrder.Remove(id);
        }

        private void ReplyClosed(string id)
        {
            if (string.IsNullOrEmpty(id))
                return;
            lock (gate)
                RemovePending(id);
            ClearQuery(id);
            Write(new Dictionary<string, object> {
                {"v", 1},
                {"id", id},
                {"ok", true},
                {"body", new Dictionary<string, object> {{"shown", false}, {"closed", true}}}
            });
        }

        private string ReadId(string line)
        {
            return ReadField(line, "id");
        }

        private string ReadOp(string line)
        {
            return ReadField(line, "op");
        }

        private string ReadField(string line, string key)
        {
            try
            {
                var request = json.Deserialize<Dictionary<string, object>>(line);
                return request != null && request.ContainsKey(key) ? Convert.ToString(request[key]) : "";
            }
            catch (Exception)
            {
                return "";
            }
        }

        private bool IsLoginShow(string line)
        {
            return ReadOp(line) == "login_window" && !IsLoginHide(line);
        }

        private bool IsLoginHide(string line)
        {
            try
            {
                var request = json.Deserialize<Dictionary<string, object>>(line);
                if (request == null || !request.ContainsKey("args"))
                    return false;
                var args = request["args"] as Dictionary<string, object>;
                if (args == null || !args.ContainsKey("show"))
                    return false;
                return args["show"] is bool && (bool)args["show"] == false;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void ShowLogin()
        {
            loginVisible = true;
            ShowInTaskbar = true;
            ClientSize = new System.Drawing.Size(960, 720);
            CenterToScreen();
            Show();
            Activate();
        }

        private void ShowPlayback()
        {
            loginVisible = false;
            // A never-shown WebView can defer media. The window was on-screen
            // during startup. Move the already created view off-screen now.
            ShowInTaskbar = false;
            ClientSize = new System.Drawing.Size(320, 180);
            // -32000 is the minimized marker; Chromium treats it as hidden.
            // Park the window just left of the whole virtual desktop instead.
            var desktop = SystemInformation.VirtualScreen;
            Location = new System.Drawing.Point(desktop.Left - ClientSize.Width - 200, desktop.Top);
            Show();
        }

        private static Dictionary<string, object> Error(string id, string code)
        {
            return new Dictionary<string, object> {{"v", 1}, {"id", id}, {"ok", false}, {"error", code}};
        }

        private static string SafeToken(string value)
        {
            if (string.IsNullOrEmpty(value) || value.Length > 32)
                return "";
            foreach (var ch in value)
            {
                if (!(char.IsLetterOrDigit(ch) || ch == '_' || ch == '-'))
                    return "";
            }
            return value;
        }

        private void ClearQuery(string id)
        {
            lock (gate)
            {
                queryDeadline.Remove(id);
                queryStarted.Remove(id);
                queryOp.Remove(id);
            }
        }

        private void Write(Dictionary<string, object> message)
        {
            Write(message, 0);
        }

        private void Write(Dictionary<string, object> message, int ms)
        {
            var id = message.ContainsKey("id") ? Convert.ToString(message["id"]) : "";
            var op = message.ContainsKey("op") ? Convert.ToString(message["op"]) : "";
            var ok = message.ContainsKey("ok") && message["ok"] is bool && (bool)message["ok"] ? "true" : "false";
            var error = message.ContainsKey("error") ? Convert.ToString(message["error"]) : "";
            var line = json.Serialize(message);
            var bytes = Encoding.UTF8.GetBytes(line + "\n");
            var wrote = false;
            lock (gate)
            {
                // WinExe Console.Out is not the pipe Python redirected.
                var handle = GetStdHandle(StdOutputHandle);
                int written;
                if (handle != IntPtr.Zero && handle != new IntPtr(-1))
                    wrote = WriteFile(handle, bytes, bytes.Length, out written, IntPtr.Zero) && written == bytes.Length;
                if (!wrote)
                {
                    try
                    {
                        Console.Out.Write(line);
                        Console.Out.Write('\n');
                        Console.Out.Flush();
                        wrote = true;
                    }
                    catch (Exception) {}
                }
            }
            CompanionLog.Write("trace stage=stdout id=" + SafeToken(id) + " op=" + SafeToken(op) + " ok=" + ok + " error=" + SafeToken(error) + " ms=" + Math.Max(0, ms).ToString() + (wrote ? "" : " error=write"));
        }
    }
}
