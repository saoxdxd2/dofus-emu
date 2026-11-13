using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using DofusFarm.Contracts;
using DofusFarm.UI;

namespace DofusFarm.Engine
{
    // Loopback HTTP bridge for the Python agent (dofus-network/agent).
    //
    //   POST /directive  {"id","ts","directive","tool","args","source","target_instance"?}
    //   GET  /agent/health
    //
    // The payload is forwarded verbatim to the injected client script as
    // window.__agentDirective(<payload>) and the client's JSON string result is
    // returned. Implemented on a raw TcpListener on purpose: HttpListener on
    // the bridge URL needs an http.sys URL ACL (netsh http add urlacl,
    // admin) for a standard user account, a plain socket does not.
    public class AgentDirectiveBridge : IDisposable
    {
        private readonly WebView2Backend _backend;
        private readonly Func<int, string> _instanceBySlot;
        private readonly Action<string> _log;
        private int _port;
        private TcpListener _listener;
        private Thread _thread;
        private volatile bool _running;

        // Ports to try, in order. A single fixed port is a single point of failure:
        // if anything on the machine is holding the primary bridge port — a leaked listening socket owned
        // by System is the case that actually happened — the bridge dies silently, the
        // app keeps running with no bridge, and every directive fails while the UI
        // looks normal. Falling through to the next port costs nothing and turns a
        // dead session into a working one.
        private static readonly int[] CANDIDATE_PORTS = GetCandidatePorts();
        private static int[] GetCandidatePorts()
        {
            string envPorts = Environment.GetEnvironmentVariable("DOFUS_BRIDGE_PORTS");
            if (!string.IsNullOrEmpty(envPorts))
            {
                var list = new List<int>();
                foreach (string s in envPorts.Split(','))
                {
                    int p;
                    if (int.TryParse(s.Trim(), out p) && p > 0) list.Add(p);
                }
                if (list.Count > 0) return list.ToArray();
            }
            int baseP = int.TryParse(Environment.GetEnvironmentVariable("DOFUS_BRIDGE_BASE_PORT") ?? "", out var _bp) && _bp > 0 ? _bp : (8800 + 93);
            return new int[] { baseP, baseP + 1, baseP + 2, baseP + 3, baseP + 4, baseP + 5, baseP + 6 };
        }

        public static int DefaultPort
        {
            get
            {
                int bp;
                return int.TryParse(Environment.GetEnvironmentVariable("DOFUS_BRIDGE_BASE_PORT") ?? "", out bp) && bp > 0 ? bp : (8800 + 93);
            }
        }

        public AgentDirectiveBridge(WebView2Backend backend, Func<int, string> instanceBySlot,
            Action<string> log, int port = 0)
        {
            _backend = backend;
            _instanceBySlot = instanceBySlot;
            _log = log;
            _port = port > 0 ? port : DefaultPort;
        }

        public bool IsRunning { get { return _running; } }

        /// <summary>The port actually bound, which is not necessarily the one asked for.</summary>
        public int Port { get { return _port; } }

        /// <summary>Where the chosen port is published for the Python agent to read.</summary>
        public static string PortFile
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DofusFarm", "bridge_port.txt");
            }
        }

        public void Start()
        {
            if (_running) return;
            string firstError = null;
            int firstPort = _port;
            var candidates = new List<int>();
            foreach (int p in CANDIDATE_PORTS) candidates.Add(p);
            if (!candidates.Contains(_port)) candidates.Insert(0, _port);

            foreach (int port in candidates)
            {
                try
                {
                    var listener = new TcpListener(IPAddress.Loopback, port);
                    listener.Start();
                    _listener = listener;
                    _port = port;
                    _running = true;
                    _thread = new Thread(Loop);
                    _thread.IsBackground = true;
                    _thread.Name = "AgentDirectiveBridge";
                    _thread.Start();
                    PublishPort();
                    if (firstError == null)
                        Log("[AGENT] directive bridge listening on http://127.0.0.1:" + _port + "/directive");
                    else
                        Log("[AGENT] directive bridge listening on http://127.0.0.1:" + _port
                            + "/directive (port " + firstPort + " was unavailable: " + firstError + ")");
                    return;
                }
                catch (Exception ex)
                {
                    if (firstError == null) firstError = ex.Message;
                }
            }

            // Last resort: let the OS pick. A fixed port (or even a fixed list) is a
            // single point of failure, and the failure is invisible — the app keeps
            // running and every directive dies. Port 0 always binds, and the chosen
            // port is published, so the agent follows it instead of assuming.
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, 0);
                listener.Start();
                _listener = listener;
                _port = ((IPEndPoint)listener.LocalEndpoint).Port;
                _running = true;
                _thread = new Thread(Loop);
                _thread.IsBackground = true;
                _thread.Name = "AgentDirectiveBridge";
                _thread.Start();
                PublishPort();
                Log("[AGENT] directive bridge listening on http://127.0.0.1:" + _port
                    + "/directive (OS-assigned; " + string.Join(", ", candidates)
                    + " were unavailable: " + firstError + ")");
                return;
            }
            catch (Exception ex)
            {
                firstError = firstError + "; OS-assigned also failed: " + ex.Message;
            }

            _running = false;
            Log("[AGENT] directive bridge failed to start on every port "
                + string.Join(", ", candidates) + " and on an OS-assigned port: " + firstError
                + " — the agent cannot send directives until this is resolved");
        }

        private void PublishPort()
        {
            try
            {
                string path = PortFile;
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                File.WriteAllText(path, _port.ToString());
            }
            catch (Exception ex)
            {
                Log("[AGENT] could not publish the bridge port: " + ex.Message);
            }
        }

        private void Loop()
        {
            while (_running)
            {
                TcpClient client = null;
                try
                {
                    client = _listener.AcceptTcpClient();
                }
                catch
                {
                    if (!_running) return;
                    continue;
                }
                ThreadPool.QueueUserWorkItem(delegate(object state)
                {
                    // No blocking wait at the root of the chain: HandleAsync is fully
                    // ConfigureAwait(false), and Guard owns its exception, so a slow
                    // client window parks a task instead of a thread.
                    UiDispatcher.Instance.Guard("AgentDirectiveBridge.Handle",
                        delegate { return HandleAsync((TcpClient)state); });
                }, client);
            }
        }

        private async Task HandleAsync(TcpClient client)
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                client.ReceiveTimeout = 10000;
                string requestLine, body;
                Dictionary<string, string> headers;
                if (!ReadRequest(stream, out requestLine, out headers, out body))
                {
                    WriteResponse(stream, 400, "{\"ok\":false,\"error\":\"malformed request\"}");
                    return;
                }

                // A page on any origin can open a TCP socket to 127.0.0.1 and
                // send a request; the browser only hides the *response* unless
                // CORS allows it. The token is what actually stops the request
                // from reaching a client window. Host/Origin checks are the
                // second lock (DNS rebinding sends the attacker's name here).
                if (!HostAllowed(headers))
                {
                    WriteResponse(stream, 403, "{\"ok\":false,\"error\":\"forbidden host\"}");
                    return;
                }
                if (!OriginAllowed(headers))
                {
                    WriteResponse(stream, 403, "{\"ok\":false,\"error\":\"forbidden origin\"}");
                    return;
                }
                if (!TokenMatches(Header(headers, "Authorization")))
                {
                    WriteResponse(stream, 401, "{\"ok\":false,\"error\":\"unauthorized\"}");
                    return;
                }

                string[] parts = requestLine.Split(' ');
                string method = parts.Length > 0 ? parts[0].ToUpperInvariant() : "";
                string path = parts.Length > 1 ? parts[1] : "/";

                if (method == "GET" && path.StartsWith("/agent/health", StringComparison.OrdinalIgnoreCase))
                {
                    WriteResponse(stream, 200, HealthJson());
                    return;
                }
                if (!(method == "POST" && path.StartsWith("/directive", StringComparison.OrdinalIgnoreCase)))
                {
                    WriteResponse(stream, 404, "{\"ok\":false,\"error\":\"unknown route\"}");
                    return;
                }
                // JSON content type forces a CORS preflight in a browser, so a
                // drive-by form/text request cannot even reach the handler.
                string ctype = Header(headers, "Content-Type");
                int semi = ctype.IndexOf(';');
                if (semi >= 0) ctype = ctype.Substring(0, semi);
                if (!string.Equals(ctype.Trim(), "application/json", StringComparison.OrdinalIgnoreCase))
                {
                    WriteResponse(stream, 415, "{\"ok\":false,\"error\":\"Content-Type must be application/json\"}");
                    return;
                }

                // Parse the envelope once, structurally, against the canonical
                // schema. The old ExtractJsonString regexed the whole body, so a
                // tool argument named "id" or "tool" could be returned as the
                // envelope's own field -- a directive answered under another
                // window's name with a success envelope around it.
                BridgeDirective envelope;
                List<string> parseErrors;
                if (!BridgeDirective.TryParse(body, out envelope, out parseErrors))
                {
                    WriteResponse(stream, 400, FailureJson("",
                        "invalid directive: " + string.Join("; ", parseErrors.ToArray())));
                    return;
                }

                string id = envelope.Id ?? "";
                string tool = envelope.Tool ?? "";
                string directive = envelope.Directive ?? "";
                string target = envelope.TargetInstance ?? "";
                int slot = envelope.TargetSlot.HasValue ? (int)envelope.TargetSlot.Value : -1;

                List<string> targets = ResolveTargets(target, slot);
                if (targets.Count == 0)
                {
                    WriteResponse(stream, 200, FailureJson(id, "no client instance is running (start one in DofusFarm first)"));
                    return;
                }

                string label = string.IsNullOrEmpty(tool) ? directive : tool;
                if (targets.Count == 1)
                {
                    WriteResponse(stream, 200,
                        await RunOneAsync(id, body, label, targets[0], true).ConfigureAwait(false));
                    return;
                }

                // Several instances were addressed. Each page answers with its own
                // envelope and the caller gets them all, in order: one client being
                // down or slow must not erase the answers of the others, and a
                // "success" that only reached the first window would be a lie.
                // Dispatched concurrently across slots so one slow or hung WebView2
                // does not block the other slots behind it.
                var answers = new List<string>(targets.Count);
                var tasks = new Task<string>[targets.Count];
                for (int i = 0; i < targets.Count; i++)
                {
                    string one = targets[i];
                    tasks[i] = Task.Run(() => RunOneAsync(id, body, label, one, false));
                }
                string[] results = await Task.WhenAll(tasks).ConfigureAwait(false);
                for (int i = 0; i < results.Length; i++) answers.Add(results[i]);

                WriteResponse(stream, 200, "{\"ok\":true,\"id\":" + JsonQuote(id)
                    + ",\"targets\":" + JsonQuote(string.Join(",", targets.ToArray()))
                    + ",\"results\":[" + string.Join(",", answers.ToArray()) + "]}");
                Log("[AGENT] " + label + " -> " + string.Join(",", targets.ToArray())
                    + " (fanned out to " + targets.Count.ToString() + ")");
            }
        }

        // -- instance routing ---------------------------------------------------

        /// <summary>
        /// Which client windows a directive is for. `target_instance` may name one id,
        /// a comma-separated list, or `all` for every window that is actually open;
        /// `target_slot` stays the 1-based single-slot form; and neither present means
        /// the first active window, which is what an unaddressed call always meant.
        /// </summary>
        private List<string> ResolveTargets(string target, int slot)
        {
            var list = new List<string>();
            if (!string.IsNullOrEmpty(target))
            {
                if (target.Trim() == "all")
                {
                    if (_backend != null)
                    {
                        foreach (string active in _backend.GetActiveInstanceIds())
                            if (!string.IsNullOrEmpty(active) && !list.Contains(active)) list.Add(active);
                    }
                    return list;
                }
                foreach (string part in target.Split(','))
                {
                    string one = part.Trim();
                    if (one.Length > 0 && !list.Contains(one)) list.Add(one);
                }
                return list;
            }
            if (slot >= 1 && _instanceBySlot != null)
            {
                string bySlot = null;
                try { bySlot = _instanceBySlot(slot); } catch { bySlot = null; }
                if (!string.IsNullOrEmpty(bySlot)) { list.Add(bySlot); return list; }
            }
            string first = FirstActiveInstance();
            if (!string.IsNullOrEmpty(first)) list.Add(first);
            return list;
        }

        /// <summary>
        /// Run one directive on one instance and return the JSON envelope for it.
        /// `allowFallback` is only true when a single instance was addressed: with
        /// several named targets, quietly answering for a different window would put
        /// one character's result under another instance's name.
        /// </summary>
        private async Task<string> RunOneAsync(string id, string body, string label, string target, bool allowFallback)
        {
            WebView2ClientForm form = _backend != null ? _backend.GetForm(target) : null;
            if (form == null || form.IsDisposed)
            {
                // The caller's idea of the instance name can be stale -- a slot was
                // restarted, or the panel remembers "dofus-05" while the only window
                // open is "dofus-01". Refusing outright made that look like the
                // client was down. If exactly one window is active, it is
                // unambiguous which one was meant.
                string fallback = allowFallback ? FirstActiveInstance() : null;
                WebView2ClientForm alt = (fallback != null && _backend != null)
                    ? _backend.GetForm(fallback) : null;
                if (alt != null && !alt.IsDisposed)
                {
                    Log("[AGENT] '" + target + "' is not active; using '" + fallback + "'");
                    form = alt;
                    target = fallback;
                }
                else
                {
                    return FailureJson(id,
                        "instance '" + target + "' is not an active client window"
                        + (string.IsNullOrEmpty(FirstActiveInstance())
                           ? " (no client window is open - launch an instance in DofusFarm)"
                           : " (active: " + FirstActiveInstance() + ")"));
                }
            }

            // The directive and the post-action snapshot travel in one renderer
            // call. Two ExecuteScriptAsync calls paid the WebView2 IPC twice per
            // action and left a window in which the world could change between the
            // action and the state it was paired with. The page returns the final
            // response envelope itself.
            string payloadLiteral = string.IsNullOrEmpty(body) ? "{}" : body;
            string combined = "window.__agentDirectiveWithSnapshot ? window.__agentDirectiveWithSnapshot({payload:"
                + payloadLiteral + ",id:" + JsonQuote(id) + ",target:" + JsonQuote(target) + "}) "
                + ": JSON.stringify({ok:false,error:'__agentDirectiveWithSnapshot missing'})";
            bool legacy = false;
            string raw = await RunOnFormAsync(form, combined, 10000).ConfigureAwait(false);
            if (raw == null)
            {
                Log("[AGENT] " + label + " -> " + target + " TIMEOUT");
                return FailureJson(id, "directive '" + label + "' timed out or the client page is not ready");
            }
            string decoded = DecodeJsResult(raw);
            if (decoded.IndexOf("\"error\":\"__agentDirectiveWithSnapshot missing\"",
                    StringComparison.Ordinal) >= 0)
            {
                // A page injected by an older build has no combined call.
                legacy = true;
                string script = "window.__agentDirective ? window.__agentDirective(" + body + ") "
                    + ": JSON.stringify({ok:false,error:'__agentDirective missing (mobile-disguise.js not loaded in this page)'})";
                raw = await RunOnFormAsync(form, script, 8000).ConfigureAwait(false);
                if (raw == null)
                {
                    Log("[AGENT] " + label + " -> " + target + " TIMEOUT");
                    return FailureJson(id, "directive '" + label + "' timed out or the client page is not ready");
                }
                decoded = DecodeJsResult(raw);
                string snapshot = await CaptureSnapshotAsync(form).ConfigureAwait(false);
                decoded = "{\"ok\":true,\"id\":" + JsonQuote(id) + ",\"target\":" + JsonQuote(target)
                    + ",\"result\":" + (LooksLikeJson(decoded) ? decoded : JsonQuote(decoded))
                    + ",\"snapshot\":" + snapshot + "}";
            }
            Log("[AGENT] " + label + " -> " + target + (legacy ? " ok (legacy two-call path)" : " ok"));
            return decoded;
        }

        // -- instance routing ---------------------------------------------------

        private string FirstActiveInstance()
        {
            if (_backend == null) return null;
            try
            {
                foreach (string active in _backend.GetActiveInstanceIds())
                {
                    if (!string.IsNullOrEmpty(active)) return active;
                }
            }
            catch (Exception ex) { _log?.Invoke("[WARN] AgentBridge.FirstActiveInstance: " + ex.Message); }
            return null;
        }

        // -- script execution ---------------------------------------------------

        // Game state immediately after the directive ran, as a JSON object or
        // the literal `null`. 2 s budget: building the snapshot is a pure object
        // read in the page, so a timeout means the page is wedged, not that the
        // snapshot is expensive.
        private async Task<string> CaptureSnapshotAsync(WebView2ClientForm form)
        {
            try
            {
                string raw = await RunOnFormAsync(form,
                    "window.__agentSnapshot ? window.__agentSnapshot() : null", 2000).ConfigureAwait(false);
                if (raw == null) return "null";
                string decoded = DecodeJsResult(raw);
                return LooksLikeJson(decoded) ? decoded : "null";
            }
            catch
            {
                return "null";
            }
        }

        /// <summary>
        /// Run one script on the window's UI thread and wait at most `timeoutMs` for
        /// its answer. The wait is on a task, not on a thread: the caller's chain is
        /// ConfigureAwait(false) all the way down, so a wedged renderer leaves no
        /// blocked UI thread behind and no .Result to deadlock on.
        /// </summary>
        private async Task<string> RunOnFormAsync(WebView2ClientForm form, string script, int timeoutMs)
        {
            TaskCompletionSource<string> tcs = new TaskCompletionSource<string>();
            try
            {
                if (form == null || form.IsDisposed || !form.IsHandleCreated) return null;
                UiDispatcher.Instance.Post("agentbridge.script", delegate
                {
                    // ExecuteScriptAsync must *start* on the thread that owns CoreWebView2.
                    form.ExecuteScriptAsync(script).ContinueWith(delegate(Task<string> task)
                    {
                        if (task.Status == TaskStatus.RanToCompletion)
                        {
                            tcs.TrySetResult(task.Result);
                        }
                        else
                        {
                            Exception error = task.Exception != null ? task.Exception.GetBaseException() : null;
                            tcs.TrySetResult("{\"ok\":false,\"error\":"
                                + JsonQuote(error != null ? error.Message : "script failed") + "}");
                        }
                    }, TaskScheduler.Default);
                });
            }
            catch (Exception ex)
            {
                Log("[AGENT] could not dispatch the script: " + ex.Message);
                return null;
            }

            Task finished = await Task.WhenAny(tcs.Task, Task.Delay(timeoutMs)).ConfigureAwait(false);
            if (finished != tcs.Task) return null;
            return await tcs.Task.ConfigureAwait(false);
        }

        // ExecuteScriptAsync returns the JSON serialization of the script result.
        // The client bus returns a JSON *string*, so unwrap the outer quotes and
        // unescape one level to get back the payload the client produced.
        private static string DecodeJsResult(string raw)
        {
            if (string.IsNullOrEmpty(raw)) return "{}";
            string trimmed = raw.Trim();
            if (trimmed.Length >= 2 && trimmed[0] == '"' && trimmed[trimmed.Length - 1] == '"')
                trimmed = UnescapeJson(trimmed.Substring(1, trimmed.Length - 2));
            return string.IsNullOrEmpty(trimmed) ? "{}" : trimmed;
        }

        private static bool LooksLikeJson(string text)
        {
            if (string.IsNullOrEmpty(text)) return false;
            char first = text.TrimStart()[0];
            return first == '{' || first == '[';
        }

        // -- minimal HTTP -------------------------------------------------------

        private static bool ReadRequest(NetworkStream stream, out string requestLine,
            out Dictionary<string, string> headers, out string body)
        {
            requestLine = null;
            headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            body = "";
            // Read the header in blocks. This used to read one byte per socket
            // call, so a 300-byte directive header cost 300 loopback reads on
            // every agent request. A block can carry the first body bytes too,
            // so the terminator offset bounds the header text and the remainder
            // is preloaded into the body.
            const int maxHeader = 16384;
            byte[] chunk = new byte[4096];
            MemoryStream received = new MemoryStream(1024);
            int headerEnd = -1;
            while (received.Length < maxHeader)
            {
                int n;
                try { n = stream.Read(chunk, 0, chunk.Length); }
                catch { return false; }
                if (n <= 0) return false;
                int before = (int)received.Length;
                received.Write(chunk, 0, n);
                byte[] arr = received.GetBuffer();
                int len = before + n;
                int scanFrom = Math.Max(0, before - 3);
                for (int i = scanFrom; i + 3 < len; i++)
                {
                    if (arr[i] == 13 && arr[i + 1] == 10
                        && arr[i + 2] == 13 && arr[i + 3] == 10)
                    {
                        headerEnd = i + 4;
                        break;
                    }
                }
                if (headerEnd >= 0) break;
            }
            if (headerEnd < 0) return false;
            byte[] buffer = received.GetBuffer();
            string headerText = Encoding.UTF8.GetString(buffer, 0, headerEnd);
            string[] lines = headerText.Split(new string[] { "\r\n" }, StringSplitOptions.RemoveEmptyEntries);
            if (lines.Length == 0) return false;
            requestLine = lines[0];

            int contentLength = 0;
            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0) continue;
                string name = lines[i].Substring(0, colon).Trim();
                string value = lines[i].Substring(colon + 1).Trim();
                if (!headers.ContainsKey(name)) headers[name] = value;
                if (string.Equals(name, "Content-Length", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(value, out contentLength);
                }
            }

            if (contentLength > 0)
            {
                if (contentLength > 1048576) return false;
                byte[] bodyBuffer = new byte[contentLength];
                int read = Math.Min((int)received.Length - headerEnd, contentLength);
                if (read > 0) Buffer.BlockCopy(buffer, headerEnd, bodyBuffer, 0, read);
                while (read < contentLength)
                {
                    int n = stream.Read(bodyBuffer, read, contentLength - read);
                    if (n <= 0) return false;
                    read += n;
                }
                body = Encoding.UTF8.GetString(bodyBuffer);
            }
            return true;
        }

        private static string Header(Dictionary<string, string> headers, string name)
        {
            string value;
            return headers.TryGetValue(name, out value) ? value : "";
        }

        /// <summary>Only the loopback name this bridge actually bound may be used.</summary>
        private bool HostAllowed(Dictionary<string, string> headers)
        {
            string host = Header(headers, "Host").Trim();
            if (host.Length == 0) return false;
            string name = host;
            int port = -1;
            if (host[0] == '[')
            {
                int close = host.IndexOf(']');
                if (close > 0)
                {
                    name = host.Substring(1, close - 1);
                    if (close + 1 < host.Length && host[close + 1] == ':')
                        int.TryParse(host.Substring(close + 2), out port);
                }
            }
            else
            {
                int colon = host.LastIndexOf(':');
                if (colon > 0)
                {
                    name = host.Substring(0, colon);
                    int.TryParse(host.Substring(colon + 1), out port);
                }
            }
            if (port != _port) return false;
            return name == "127.0.0.1"
                || name.Equals("localhost", StringComparison.OrdinalIgnoreCase)
                || name == "::1";
        }

        private static bool OriginAllowed(Dictionary<string, string> headers)
        {
            string origin = Header(headers, "Origin").Trim();
            if (origin.Length == 0) return true;
            Uri uri;
            if (!Uri.TryCreate(origin, UriKind.Absolute, out uri)) return false;
            if (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps) return false;
            string host = uri.DnsSafeHost.Trim('[', ']');
            return host == "127.0.0.1"
                || host.Equals("localhost", StringComparison.OrdinalIgnoreCase)
                || host == "::1"
                || host.Equals("dofus.local", StringComparison.OrdinalIgnoreCase)
                || host.Equals("dofus.localhost", StringComparison.OrdinalIgnoreCase);
        }

        private static bool TokenMatches(string supplied)
        {
            string secret = LocalSecret.Value;
            if (string.IsNullOrEmpty(secret) || string.IsNullOrEmpty(supplied)) return false;
            string value = supplied.Trim();
            return FixedTimeEquals(value, "Bearer " + secret) || FixedTimeEquals(value, secret);
        }

        /// <summary>Compare through SHA-256 so the time does not reveal the matching prefix.</summary>
        private static bool FixedTimeEquals(string a, string b)
        {
            using (var sha = SHA256.Create())
            {
                byte[] ha = sha.ComputeHash(Encoding.UTF8.GetBytes(a ?? ""));
                byte[] hb = sha.ComputeHash(Encoding.UTF8.GetBytes(b ?? ""));
                int diff = 0;
                for (int i = 0; i < ha.Length; i++) diff |= ha[i] ^ hb[i];
                return diff == 0;
            }
        }

        private static void WriteResponse(NetworkStream stream, int status, string json)
        {
            byte[] payload = Encoding.UTF8.GetBytes(json);
            string header = "HTTP/1.1 " + status + " " + (status == 200 ? "OK" : "Error") + "\r\n"
                + "Content-Type: application/json; charset=utf-8\r\n"
                + "Content-Length: " + payload.Length + "\r\n"
                + "Connection: close\r\n\r\n";
            byte[] headerBytes = Encoding.ASCII.GetBytes(header);
            try
            {
                stream.Write(headerBytes, 0, headerBytes.Length);
                stream.Write(payload, 0, payload.Length);
                stream.Flush();
            }
            catch (Exception ex) { Debug.WriteLine("[WARN] AgentBridge.WriteResponse: " + ex.Message); }
        }

        private string HealthJson()
        {
            int count = 0;
            try { if (_backend != null) count = _backend.GetActiveInstanceIds().Count; }
            catch (Exception ex) { _log?.Invoke("[WARN] AgentBridge.HealthJson: " + ex.Message); }
            // Encoded from the same schema the Python agent validates against, so a
            // field rename cannot leave one side reading a document the other stopped
            // writing.
            BridgeHealth health = new BridgeHealth();
            health.Status = "ok";
            health.Service = "dofus-agent-bridge";
            health.Port = _port;
            health.Instances = count;
            health.Time = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
            return health.ToJson();
        }

        private static string FailureJson(string id, string error)
        {
            return "{\"ok\":false,\"id\":" + JsonQuote(id) + ",\"error\":" + JsonQuote(error) + "}";
        }

        // -- minimal JSON helpers (payloads are machine-generated) ---------------

        private static string UnescapeJson(string text)
        {
            if (text.IndexOf('\\') < 0) return text;
            StringBuilder sb = new StringBuilder(text.Length);
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c != '\\' || i + 1 >= text.Length) { sb.Append(c); continue; }
                i++;
                char esc = text[i];
                if (esc == 'n') sb.Append('\n');
                else if (esc == 'r') sb.Append('\r');
                else if (esc == 't') sb.Append('\t');
                else if (esc == 'b') sb.Append('\b');
                else if (esc == 'f') sb.Append('\f');
                else if (esc == 'u' && i + 4 < text.Length)
                {
                    int code;
                    if (int.TryParse(text.Substring(i + 1, 4), System.Globalization.NumberStyles.HexNumber,
                        System.Globalization.CultureInfo.InvariantCulture, out code))
                    {
                        sb.Append((char)code);
                        i += 4;
                    }
                }
                else sb.Append(esc);
            }
            return sb.ToString();
        }

        private static string JsonQuote(string text)
        {
            if (text == null) return "null";
            StringBuilder sb = new StringBuilder(text.Length + 2);
            sb.Append('"');
            foreach (char c in text)
            {
                if (c == '"' || c == '\\') { sb.Append('\\'); sb.Append(c); }
                else if (c == '\n') sb.Append("\\n");
                else if (c == '\r') sb.Append("\\r");
                else if (c == '\t') sb.Append("\\t");
                else if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                else sb.Append(c);
            }
            sb.Append('"');
            return sb.ToString();
        }

        private void Log(string message)
        {
            if (_log != null)
            {
                try { _log(message); }
                catch (Exception ex) { Console.Error.WriteLine("[WARN] AgentBridge.Log: " + ex.Message); }
            }
        }

        public void Dispose()
        {
            _running = false;
            try { if (_listener != null) _listener.Stop(); }
            catch (Exception ex) { _log?.Invoke("[WARN] AgentBridge.Dispose: " + ex.Message); }
            _listener = null;
        }
    }
}
