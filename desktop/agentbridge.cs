using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

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
    // http://127.0.0.1:8893/ needs an http.sys URL ACL (netsh http add urlacl,
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
        // if anything on the machine is holding 8893 — a leaked listening socket owned
        // by System is the case that actually happened — the bridge dies silently, the
        // app keeps running with no bridge, and every directive fails while the UI
        // looks normal. Falling through to the next port costs nothing and turns a
        // dead session into a working one.
        private static readonly int[] CANDIDATE_PORTS = { 8893, 8894, 8895, 8896, 8897, 8898, 8899 };

        public AgentDirectiveBridge(WebView2Backend backend, Func<int, string> instanceBySlot,
            Action<string> log, int port)
        {
            _backend = backend;
            _instanceBySlot = instanceBySlot;
            _log = log;
            _port = port;
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
                    try { Handle((TcpClient)state); }
                    catch (Exception ex) { Log("[AGENT] request error: " + ex.Message); }
                }, client);
            }
        }

        private void Handle(TcpClient client)
        {
            using (client)
            using (NetworkStream stream = client.GetStream())
            {
                client.ReceiveTimeout = 10000;
                string requestLine, body;
                if (!ReadRequest(stream, out requestLine, out body))
                {
                    WriteResponse(stream, 400, "{\"ok\":false,\"error\":\"malformed request\"}");
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

                string id = ExtractJsonString(body, "id");
                string tool = ExtractJsonString(body, "tool");
                string directive = ExtractJsonString(body, "directive");
                string target = ExtractJsonString(body, "target_instance");
                int slot = ExtractJsonInt(body, "target_slot", -1);

                List<string> targets = ResolveTargets(target, slot);
                if (targets.Count == 0)
                {
                    WriteResponse(stream, 200, FailureJson(id, "no client instance is running (start one in DofusFarm first)"));
                    return;
                }

                string label = string.IsNullOrEmpty(tool) ? directive : tool;
                if (targets.Count == 1)
                {
                    WriteResponse(stream, 200, RunOne(id, body, label, targets[0], true));
                    return;
                }

                // Several instances were addressed. Each page answers with its own
                // envelope and the caller gets them all, in order: one client being
                // down or slow must not erase the answers of the others, and a
                // "success" that only reached the first window would be a lie.
                var answers = new List<string>();
                foreach (string one in targets) answers.Add(RunOne(id, body, label, one, false));
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
        private string RunOne(string id, string body, string label, string target, bool allowFallback)
        {
            WebView2ClientForm form = _backend != null ? _backend.GetForm(target) : null;
            if (form == null || form.IsDisposed)
            {
                // The caller's idea of the instance name can be stale — a slot was
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
            string raw = RunOnForm(form, combined, 10000);
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
                raw = RunOnForm(form, script, 8000);
                if (raw == null)
                {
                    Log("[AGENT] " + label + " -> " + target + " TIMEOUT");
                    return FailureJson(id, "directive '" + label + "' timed out or the client page is not ready");
                }
                decoded = DecodeJsResult(raw);
                string snapshot = CaptureSnapshot(form);
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
            catch { }
            return null;
        }

        // -- script execution ---------------------------------------------------

        // Game state immediately after the directive ran, as a JSON object or
        // the literal `null`. 2 s budget: building the snapshot is a pure object
        // read in the page, so a timeout means the page is wedged, not that the
        // snapshot is expensive.
        private string CaptureSnapshot(WebView2ClientForm form)
        {
            try
            {
                string raw = RunOnForm(form,
                    "window.__agentSnapshot ? window.__agentSnapshot() : null", 2000);
                if (raw == null) return "null";
                string decoded = DecodeJsResult(raw);
                return LooksLikeJson(decoded) ? decoded : "null";
            }
            catch
            {
                return "null";
            }
        }

        private string RunOnForm(WebView2ClientForm form, string script, int timeoutMs)
        {
            TaskCompletionSource<string> tcs = new TaskCompletionSource<string>();
            try
            {
                Action kick = delegate { KickScript(form, script, tcs); };
                if (form.InvokeRequired && form.IsHandleCreated)
                    form.BeginInvoke(kick);
                else
                    kick();
            }
            catch
            {
                return null;
            }
            if (!tcs.Task.Wait(timeoutMs)) return null;
            return tcs.Task.Result;
        }

        private static async void KickScript(WebView2ClientForm form, string script, TaskCompletionSource<string> tcs)
        {
            try
            {
                string result = await form.ExecuteScriptAsync(script);
                tcs.TrySetResult(result);
            }
            catch (Exception ex)
            {
                tcs.TrySetResult("{\"ok\":false,\"error\":" + JsonQuote(ex.Message) + "}");
            }
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

        private static bool ReadRequest(NetworkStream stream, out string requestLine, out string body)
        {
            requestLine = null;
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
                if (string.Equals(name, "Content-Length", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(lines[i].Substring(colon + 1).Trim(), out contentLength);
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
            catch { }
        }

        private string HealthJson()
        {
            int count = 0;
            try { if (_backend != null) count = _backend.GetActiveInstanceIds().Count; }
            catch { }
            return "{\"status\":\"ok\",\"service\":\"dofus-agent-bridge\",\"port\":" + _port
                + ",\"instances\":" + count + ",\"time\":" + DateTimeOffset.UtcNow.ToUnixTimeSeconds() + "}";
        }

        private static string FailureJson(string id, string error)
        {
            return "{\"ok\":false,\"id\":" + JsonQuote(id) + ",\"error\":" + JsonQuote(error) + "}";
        }

        // -- minimal JSON helpers (payloads are machine-generated) ---------------

        private static string ExtractJsonString(string json, string key)
        {
            if (string.IsNullOrEmpty(json)) return "";
            Match m = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"");
            if (!m.Success) return "";
            return UnescapeJson(m.Groups[1].Value);
        }

        private static int ExtractJsonInt(string json, string key, int fallback)
        {
            if (string.IsNullOrEmpty(json)) return fallback;
            Match m = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*(-?\\d+)");
            if (!m.Success) return fallback;
            int value;
            return int.TryParse(m.Groups[1].Value, out value) ? value : fallback;
        }

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
                catch { }
            }
        }

        public void Dispose()
        {
            _running = false;
            try { if (_listener != null) _listener.Stop(); }
            catch { }
            _listener = null;
        }
    }
}
