// jsontext.cs - a small, strict JSON reader/writer for the host.
//
// Why this exists
// ---------------
// AgentDirectiveBridge used to pull fields out of the request body with
// `Regex.Match(json, "\"id\"\\s*:\\s*\"...\"")`. A regex has no idea where a nested
// object begins, so `{"id":"outer","args":{"id":"inner"}}` matched whichever `"id"`
// came first, and a tool argument literally named `id` or `tool` could be returned
// as the envelope's own value. The bridge then ran a directive on the wrong window
// or reported the wrong id, with a success envelope around it.
//
// Parsing the document once removes the guessing: `JsonText` builds the tree, the
// generated contract readers pick fields from the *top-level* object, and nesting is
// just nesting. It stays small on purpose - no LINQ, no reflection, no culture-
// sensitive number parsing - so it is cheap enough to run per directive (a few
// microseconds for the ~1 KB payloads the agent sends).
//
// Values map to: null, string, bool, long, double, List<object>,
// Dictionary<string, object>. Objects are ordinal-keyed, never case-folded: JSON
// keys are case-sensitive and silently accepting `Tool` for `tool` is how a typo
// becomes a mystery.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace DofusFarm.Json
{
    public sealed class JsonFormatException : Exception
    {
        public JsonFormatException(string message) : base(message) { }
    }

    public static class JsonText
    {
        private const int MaxDepth = 64;

        /// <summary>Parse a document. Throws <see cref="JsonFormatException"/> on malformed input.</summary>
        public static object Parse(string text)
        {
            if (text == null) throw new JsonFormatException("null input");
            int index = 0;
            SkipWhitespace(text, ref index);
            if (index >= text.Length) throw new JsonFormatException("empty document");
            object value = ParseValue(text, ref index, 0);
            SkipWhitespace(text, ref index);
            if (index != text.Length)
                throw new JsonFormatException("trailing characters at offset " + index.ToString(CultureInfo.InvariantCulture));
            return value;
        }

        public static bool TryParse(string text, out object value)
        {
            try
            {
                value = Parse(text);
                return true;
            }
            catch (JsonFormatException)
            {
                value = null;
                return false;
            }
        }

        /// <summary>Top-level object, or null when the document is not an object.</summary>
        public static Dictionary<string, object> ParseObject(string text)
        {
            object value = Parse(text);
            return value as Dictionary<string, object>;
        }

        /// <summary>Top-level object, or null when the text is malformed or not an object.</summary>
        public static Dictionary<string, object> TryParseObject(string text)
        {
            object value;
            if (!TryParse(text, out value)) return null;
            return value as Dictionary<string, object>;
        }

        public static string String(Dictionary<string, object> obj, string key, string fallback)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return fallback;
            string text = value as string;
            return text == null ? fallback : text;
        }

        public static long Long(Dictionary<string, object> obj, string key, long fallback)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return fallback;
            if (value is long) return (long)value;
            if (value is double) return (long)(double)value;
            if (value is bool) return ((bool)value) ? 1 : 0;
            string text = value as string;
            long parsed;
            if (text != null && long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed))
                return parsed;
            return fallback;
        }

        public static double Double(Dictionary<string, object> obj, string key, double fallback)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return fallback;
            if (value is double) return (double)value;
            if (value is long) return (double)(long)value;
            string text = value as string;
            double parsed;
            if (text != null && double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out parsed))
                return parsed;
            return fallback;
        }

        public static bool Bool(Dictionary<string, object> obj, string key, bool fallback)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return fallback;
            if (value is bool) return (bool)value;
            string text = value as string;
            if (text == null) return fallback;
            if (string.Equals(text, "true", StringComparison.OrdinalIgnoreCase)) return true;
            if (string.Equals(text, "false", StringComparison.OrdinalIgnoreCase)) return false;
            return fallback;
        }

        public static Dictionary<string, object> Object(Dictionary<string, object> obj, string key)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return null;
            return value as Dictionary<string, object>;
        }

        public static List<object> Array(Dictionary<string, object> obj, string key)
        {
            object value;
            if (obj == null || !obj.TryGetValue(key, out value)) return null;
            return value as List<object>;
        }

        /// <summary>JSON string literal, quotes included.</summary>
        public static string Quote(string text)
        {
            if (text == null) return "null";
            StringBuilder sb = new StringBuilder(text.Length + 2);
            sb.Append('"');
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (c == '"' || c == '\\') { sb.Append('\\'); sb.Append(c); }
                else if (c == '\n') sb.Append("\\n");
                else if (c == '\r') sb.Append("\\r");
                else if (c == '\t') sb.Append("\\t");
                else if (c == '\b') sb.Append("\\b");
                else if (c == '\f') sb.Append("\\f");
                else if (c < 0x20 || c == '\u2028' || c == '\u2029')
                    sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                else sb.Append(c);
            }
            sb.Append('"');
            return sb.ToString();
        }

        /// <summary>Serialize a tree built by <see cref="Parse"/> (or plain CLR values).</summary>
        public static string Serialize(object value)
        {
            StringBuilder sb = new StringBuilder(256);
            Write(sb, value, 0);
            return sb.ToString();
        }

        private static void Write(StringBuilder sb, object value, int depth)
        {
            if (depth > MaxDepth) throw new JsonFormatException("document nests deeper than " + MaxDepth.ToString(CultureInfo.InvariantCulture));
            if (value == null) { sb.Append("null"); return; }
            string text = value as string;
            if (text != null) { sb.Append(Quote(text)); return; }
            if (value is bool) { sb.Append(((bool)value) ? "true" : "false"); return; }
            if (value is long) { sb.Append(((long)value).ToString(CultureInfo.InvariantCulture)); return; }
            if (value is int) { sb.Append(((int)value).ToString(CultureInfo.InvariantCulture)); return; }
            if (value is double)
            {
                double d = (double)value;
                if (double.IsNaN(d) || double.IsInfinity(d)) { sb.Append("null"); return; }
                sb.Append(d.ToString("R", CultureInfo.InvariantCulture));
                return;
            }
            IDictionary<string, object> map = value as IDictionary<string, object>;
            if (map != null)
            {
                sb.Append('{');
                bool first = true;
                foreach (KeyValuePair<string, object> pair in map)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    sb.Append(Quote(pair.Key)).Append(':');
                    Write(sb, pair.Value, depth + 1);
                }
                sb.Append('}');
                return;
            }
            System.Collections.IEnumerable list = value as System.Collections.IEnumerable;
            if (list != null)
            {
                sb.Append('[');
                bool first = true;
                foreach (object item in list)
                {
                    if (!first) sb.Append(',');
                    first = false;
                    Write(sb, item, depth + 1);
                }
                sb.Append(']');
                return;
            }
            throw new JsonFormatException("cannot serialize " + value.GetType().Name);
        }

        // -- reading ------------------------------------------------------------

        private static object ParseValue(string text, ref int index, int depth)
        {
            if (depth > MaxDepth) throw new JsonFormatException("nesting deeper than " + MaxDepth.ToString(CultureInfo.InvariantCulture));
            if (index >= text.Length) throw new JsonFormatException("unexpected end of document");
            char c = text[index];
            switch (c)
            {
                case '{': return ParseObjectValue(text, ref index, depth);
                case '[': return ParseArrayValue(text, ref index, depth);
                case '"': return ParseStringValue(text, ref index);
                case 't': Expect(text, ref index, "true"); return true;
                case 'f': Expect(text, ref index, "false"); return false;
                case 'n': Expect(text, ref index, "null"); return null;
                default:
                    if (c == '-' || (c >= '0' && c <= '9')) return ParseNumberValue(text, ref index);
                    throw new JsonFormatException("unexpected character '" + c + "' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static Dictionary<string, object> ParseObjectValue(string text, ref int index, int depth)
        {
            Dictionary<string, object> map = new Dictionary<string, object>(StringComparer.Ordinal);
            index++;                                   // '{'
            SkipWhitespace(text, ref index);
            if (index < text.Length && text[index] == '}') { index++; return map; }
            while (true)
            {
                SkipWhitespace(text, ref index);
                if (index >= text.Length || text[index] != '"')
                    throw new JsonFormatException("expected an object key at offset " + index.ToString(CultureInfo.InvariantCulture));
                string key = ParseStringValue(text, ref index);
                SkipWhitespace(text, ref index);
                if (index >= text.Length || text[index] != ':')
                    throw new JsonFormatException("expected ':' at offset " + index.ToString(CultureInfo.InvariantCulture));
                index++;
                SkipWhitespace(text, ref index);
                map[key] = ParseValue(text, ref index, depth + 1);
                SkipWhitespace(text, ref index);
                if (index >= text.Length) throw new JsonFormatException("unterminated object");
                if (text[index] == ',') { index++; continue; }
                if (text[index] == '}') { index++; return map; }
                throw new JsonFormatException("expected ',' or '}' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static List<object> ParseArrayValue(string text, ref int index, int depth)
        {
            List<object> list = new List<object>();
            index++;                                   // '['
            SkipWhitespace(text, ref index);
            if (index < text.Length && text[index] == ']') { index++; return list; }
            while (true)
            {
                SkipWhitespace(text, ref index);
                list.Add(ParseValue(text, ref index, depth + 1));
                SkipWhitespace(text, ref index);
                if (index >= text.Length) throw new JsonFormatException("unterminated array");
                if (text[index] == ',') { index++; continue; }
                if (text[index] == ']') { index++; return list; }
                throw new JsonFormatException("expected ',' or ']' at offset " + index.ToString(CultureInfo.InvariantCulture));
            }
        }

        private static string ParseStringValue(string text, ref int index)
        {
            index++;                                   // opening quote
            StringBuilder sb = new StringBuilder(32);
            while (true)
            {
                if (index >= text.Length) throw new JsonFormatException("unterminated string");
                char c = text[index++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }
                if (index >= text.Length) throw new JsonFormatException("unterminated escape");
                char esc = text[index++];
                switch (esc)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (index + 4 > text.Length) throw new JsonFormatException("truncated \\u escape");
                        int code;
                        if (!int.TryParse(text.Substring(index, 4), NumberStyles.HexNumber,
                                          CultureInfo.InvariantCulture, out code))
                            throw new JsonFormatException("bad \\u escape at offset " + index.ToString(CultureInfo.InvariantCulture));
                        sb.Append((char)code);
                        index += 4;
                        break;
                    default:
                        throw new JsonFormatException("unknown escape '\\" + esc + "'");
                }
            }
        }

        private static object ParseNumberValue(string text, ref int index)
        {
            int start = index;
            if (text[index] == '-') index++;
            while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
            bool fractional = false;
            if (index < text.Length && text[index] == '.')
            {
                fractional = true;
                index++;
                while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
            }
            if (index < text.Length && (text[index] == 'e' || text[index] == 'E'))
            {
                fractional = true;
                index++;
                if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++;
                while (index < text.Length && text[index] >= '0' && text[index] <= '9') index++;
            }
            string literal = text.Substring(start, index - start);
            if (!fractional)
            {
                long asLong;
                if (long.TryParse(literal, NumberStyles.Integer, CultureInfo.InvariantCulture, out asLong))
                    return asLong;
            }
            double asDouble;
            if (double.TryParse(literal, NumberStyles.Float, CultureInfo.InvariantCulture, out asDouble))
                return asDouble;
            throw new JsonFormatException("bad number '" + literal + "'");
        }

        private static void Expect(string text, ref int index, string literal)
        {
            if (index + literal.Length > text.Length
                || string.CompareOrdinal(text, index, literal, 0, literal.Length) != 0)
                throw new JsonFormatException("expected '" + literal + "' at offset " + index.ToString(CultureInfo.InvariantCulture));
            index += literal.Length;
        }

        private static void SkipWhitespace(string text, ref int index)
        {
            while (index < text.Length)
            {
                char c = text[index];
                if (c != ' ' && c != '\t' && c != '\r' && c != '\n') return;
                index++;
            }
        }
    }
}
