with open('desktop/mainform.cs', 'r', encoding='utf-8') as f:
    content = f.read()

old_choices = """internal static readonly int[] CoreChoices = { 1, 2, 3, 4, 6, 8 };"""
new_choices = """internal static readonly int[] CoreChoices = { 0, 1, 2, 3, 4, 6, 8 };"""

old_combo1 = """CoreChoices.Select(c => (object)(c + (c == 1 ? " Core" : " Cores"))).ToArray()"""
new_combo1 = """CoreChoices.Select(c => (object)(c == 0 ? "Auto (Shared)" : (c + (c == 1 ? " Core" : " Cores")))).ToArray()"""

old_parse = """        internal static int ParseFirstInt(object val, int fallback)
        {
            if (val == null) return fallback;
            Match m = Regex.Match(val.ToString(), @"\d+");
            return m.Success ? int.Parse(m.Value) : fallback;
        }"""

new_parse = """        internal static int ParseFirstInt(object val, int fallback)
        {
            if (val == null) return fallback;
            string s = val.ToString();
            if (s.StartsWith("Auto", StringComparison.OrdinalIgnoreCase) || s.Contains("Shared")) return 0;
            Match m = Regex.Match(s, @"\d+");
            return m.Success ? int.Parse(m.Value) : fallback;
        }"""

old_grid_core = """inst.Cores + (inst.Cores == 1 ? " Core" : " Cores"),"""
new_grid_core = """inst.Cores > 0 ? (inst.Cores + (inst.Cores == 1 ? " Core" : " Cores")) : "Shared","""

content_norm = content.replace('\r\n', '\n')

for name, old_s, new_s in [
    ('choices', old_choices, new_choices),
    ('combo1', old_combo1, new_combo1),
    ('parse', old_parse, new_parse),
    ('grid_core', old_grid_core, new_grid_core),
]:
    old_norm = old_s.replace('\r\n', '\n')
    new_norm = new_s.replace('\r\n', '\n')
    assert old_norm in content_norm, f'Pattern {name} not found!'
    content_norm = content_norm.replace(old_norm, new_norm)

with open('desktop/mainform.cs', 'w', encoding='utf-8', newline='\r\n') as f:
    f.write(content_norm.replace('\n', '\r\n'))
print('MAINFORM_CORES_PATCHED_OK')
