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
        private readonly int _port;
        private TcpListener _listener;
        private Thread _thread;
        private volatile bool _running;

        public AgentDirectiveBridge(WebView2Backend backend, Func<int, string> instanceBySlot,
            Action<string> log, int port)
        {
            _backend = backend;
            _instanceBySlot = instanceBySlot;
            _log = log;
            _port = port;
        }

        public bool IsRunning { get { return _running; } }

        public void Start()
        {
            if (_running) return;
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port);
                _listener.Start();
                _running = true;
                _thread = new Thread(Loop);
                _thread.IsBackground = true;
                _thread.Name = "AgentDirectiveBridge";
                _thread.Start();
                Log("[AGENT] directive bridge listening on http://127.0.0.1:" + _port + "/directive");
            }
            catch (Exception ex)
            {
                _running = false;
                Log("[AGENT] directive bridge failed to start on port " + _port + ": " + ex.Message);
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

                if (string.IsNullOrEmpty(target) && slot >= 1 && _instanceBySlot != null)
                    target = _instanceBySlot(slot);
                if (string.IsNullOrEmpty(target))
                    target = FirstActiveInstance();
                if (string.IsNullOrEmpty(target))
                {
                    WriteResponse(stream, 200, FailureJson(id, "no client instance is running (start one in DofusFarm first)"));
                    return;
                }

                WebView2ClientForm form = _backend != null ? _backend.GetForm(target) : null;
                if (form == null || form.IsDisposed)
                {
                    WriteResponse(stream, 200, FailureJson(id, "instance '" + target + "' is not an active client window"));
                    return;
                }

                string label = string.IsNullOrEmpty(tool) ? directive : tool;
                string script = "window.__agentDirective ? window.__agentDirective(" + body + ") "
                    + ": JSON.stringify({ok:false,error:'__agentDirective missing (mobile-disguise.js not loaded in this page)'})";

                string raw = RunOnForm(form, script, 8000);
                if (raw == null)
                {
                    WriteResponse(stream, 200, FailureJson(id, "directive '" + label + "' timed out or the client page is not ready"));
                    Log("[AGENT] " + label + " -> " + target + " TIMEOUT");
                    return;
                }

                string decoded = DecodeJsResult(raw);
                // Piggyback the game state on the response. The Python recorder
                // needs the state the action was taken against, and this is the
                // only moment it is provably fresh (age 0). A separate poll
                // would be a second clock and a second source of truth.
                string snapshot = CaptureSnapshot(form);
                string response = "{\"ok\":true,\"id\":" + JsonQuote(id) + ",\"target\":" + JsonQuote(target)
                    + ",\"result\":" + (LooksLikeJson(decoded) ? decoded : JsonQuote(decoded))
                    + ",\"snapshot\":" + snapshot + "}";
                WriteResponse(stream, 200, response);
                Log("[AGENT] " + label + " -> " + target + " ok");
            }
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
            MemoryStream headerBuffer = new MemoryStream();
            byte[] single = new byte[1];
            int total = 0;
            while (total < 16384)
            {
                int n;
                try { n = stream.Read(single, 0, 1); }
                catch { return false; }
                if (n <= 0) return false;
                headerBuffer.WriteByte(single[0]);
                total++;
                byte[] arr = headerBuffer.GetBuffer();
                if (total >= 4 && arr[total - 4] == 13 && arr[total - 3] == 10
                    && arr[total - 2] == 13 && arr[total - 1] == 10)
                    break;
            }
            string headerText = Encoding.UTF8.GetString(headerBuffer.ToArray());
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
                int read = 0;
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
