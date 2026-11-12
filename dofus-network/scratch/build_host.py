"""Build DofusFarm.exe from this tree, with the builder's own compiler and source list.

Why this exists: a fix that is not in the binary is not a fix (G116 -- the host on disk
was from 15:27 while the source carried five later fixes). `builder/build.cs` is the
authority for both the compiler and the file list, so this script asks the gate for the
same two things instead of keeping a second copy: `_find_csharp_compiler()` and
`_builder_host_sources()`. It writes `DofusFarm.new.exe`; installing it is a separate,
deliberate step.

Run from `dofus-network/`:  python scratch/build_host.py
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import regression as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
desktop = ROOT / "desktop"

REFS = ["mscorlib.dll", "System.dll", "System.Core.dll", "System.Drawing.dll",
        "System.Windows.Forms.dll", "System.Management.dll",
        "System.IO.Compression.dll", "System.IO.Compression.FileSystem.dll"]


def main() -> int:
    compiler = R._find_csharp_compiler()
    if compiler is None:
        print("no C# compiler found")
        return 1
    names = R._builder_host_sources(ROOT / "builder" / "build.cs")
    sources = [desktop / n for n in names]
    missing = [str(p) for p in sources if not p.exists()]
    if missing:
        print("missing sources:", missing)
        return 1
    print("compiler: %s" % compiler[0])
    print("sources : %d files" % len(names))

    args = ["/nologo", "/target:winexe", "/optimize+"]
    if not compiler[0].lower().endswith(".exe".replace(".exe", "c.exe")):
        pass  # unreachable; kept out of the way on purpose
    if compiler[0].lower().endswith("csc.exe"):
        args.extend("/r:%s" % name for name in REFS)
    else:
        windir = Path(os.environ.get("windir", "C:\\" + "Windows"))
        framework = None
        for flavour in ("Framework64", "Framework"):
            cand = windir / "Microsoft.NET" / flavour / "v4.0.30319"
            if all((cand / name).exists() for name in REFS):
                framework = cand
                break
        if framework is not None:
            args.append("/nostdlib+")
            args.extend("/r:%s" % (framework / name) for name in REFS)
        else:
            args.extend("/r:%s" % name for name in REFS)

    for dll in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
        path = desktop / "lib" / "webview2" / dll
        if path.exists():
            args.append("/r:%s" % path)
    icon = desktop / "app_icon.ico"
    if icon.exists():
        args.append("/win32icon:%s" % icon)

    out = ROOT / "DofusFarm.new.exe"
    if out.exists():
        out.unlink()
    args.append("/out:%s" % out)
    args.extend(str(path) for path in sources)

    proc = subprocess.run([compiler[0], *compiler[1:], *args], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300)
    print("exit: %d" % proc.returncode)
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if tail:
        print(tail[-1200:])
    if proc.returncode == 0 and out.exists():
        print("built: %s (%d bytes)" % (out.name, out.stat().st_size))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
