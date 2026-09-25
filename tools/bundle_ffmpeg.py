#!/usr/bin/env python3
"""Make ffmpeg/ffprobe self-contained, and prove that they are.

A Homebrew ffmpeg is a 400 KB stub that loads ~17 dylibs by absolute path out
of /opt/homebrew/Cellar/<version>/. Copying the stub into the bundle, which is
what the build used to do, produces an app that launches fine on the machine
that built it and fails every file on any Mac without that exact Homebrew
version — and the build's own smoke test cannot tell the difference, because
the build machine has the libraries.

``bundle`` copies the two executables plus every non-system dylib they reach,
transitively, into ``<dest>/lib``, rewrites each load command to
``@loader_path``, and re-signs ad hoc (a rewritten Mach-O has a broken
signature, and arm64 kills unsigned code on load).

``verify`` is the check the smoke test cannot make: every dependency of every
Mach-O under the directory, followed transitively, must resolve to the OS or
to a file inside the bundle. It also
reports the highest ``minos`` found, since a library built for macOS 26 cannot
load on macOS 15 no matter where it sits; build.sh writes that value into
LSMinimumSystemVersion so an older Mac refuses the app up front instead of
failing every file.

Stdlib only; it runs before the build venv matters.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
from collections import deque
from typing import Dict, List, Optional, Tuple

EXECUTABLES = ("ffmpeg", "ffprobe")
LIB_DIR = "lib"
SYSTEM_PREFIXES = ("/System/", "/usr/lib/")


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


def is_macho(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return False
    return magic in (
        b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",   # thin, little-endian
        b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf",   # fat / fat64
    )


def load_commands(path: str) -> List[str]:
    """Dependencies from ``otool -L``, minus the file's own install id."""
    lines = _run(["otool", "-L", path]).splitlines()[1:]
    deps = [ln.strip().split(" (compatibility")[0] for ln in lines if ln.strip()]
    own_id = _install_id(path)
    return [d for d in deps if d != own_id]


def _install_id(path: str) -> Optional[str]:
    out = _run(["otool", "-D", path]).splitlines()
    return out[1].strip() if len(out) > 1 else None


def rpaths(path: str) -> List[str]:
    found, lines = [], _run(["otool", "-l", path]).splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "cmd LC_RPATH":
            for follow in lines[i + 1:i + 4]:
                follow = follow.strip()
                if follow.startswith("path "):
                    found.append(follow[5:].split(" (offset")[0])
    return found


def min_os(path: str) -> Optional[Tuple[int, ...]]:
    """Highest deployment target across the file's slices."""
    best, lines = None, _run(["otool", "-l", path]).splitlines()
    for i, ln in enumerate(lines):
        cmd = ln.strip()
        if cmd in ("cmd LC_BUILD_VERSION", "cmd LC_VERSION_MIN_MACOSX"):
            for follow in lines[i + 1:i + 6]:
                parts = follow.split()
                if parts and parts[0] in ("minos", "version"):
                    ver = tuple(int(x) for x in parts[1].split("."))
                    best = ver if best is None or ver > best else best
                    break
    return best


def is_system(dep: str) -> bool:
    return dep.startswith(SYSTEM_PREFIXES)


def resolve(dep: str, referrer: str, exe_dir: str) -> Optional[str]:
    """Where dyld would find ``dep`` when loaded from ``referrer``."""
    ref_dir = os.path.dirname(os.path.realpath(referrer))
    if dep.startswith("@loader_path/"):
        candidates = [os.path.join(ref_dir, dep[len("@loader_path/"):])]
    elif dep.startswith("@executable_path/"):
        candidates = [os.path.join(exe_dir, dep[len("@executable_path/"):])]
    elif dep.startswith("@rpath/"):
        tail = dep[len("@rpath/"):]
        candidates = []
        for rp in rpaths(referrer):
            rp = rp.replace("@loader_path", ref_dir).replace("@executable_path", exe_dir)
            candidates.append(os.path.join(rp, tail))
    else:
        candidates = [dep]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.realpath(c)
    return None


def _writable_copy(src: str, dst: str):
    shutil.copy2(src, dst)
    os.chmod(dst, os.stat(dst).st_mode | stat.S_IWUSR)


def bundle(sources: Dict[str, str], dest: str) -> None:
    """Copy executables and their dylib closure into ``dest``, relinked."""
    lib_dir = os.path.join(dest, LIB_DIR)
    if os.path.isdir(lib_dir):
        shutil.rmtree(lib_dir)
    os.makedirs(lib_dir)

    # original real path -> bundled path; and bundled path -> its rewrites
    placed: Dict[str, str] = {}
    rewrites: Dict[str, List[Tuple[str, str]]] = {}
    queue = deque()

    for name, src in sources.items():
        real = os.path.realpath(src)
        if not os.path.isfile(real):
            raise RuntimeError(f"{name} not found at {src}")
        out = os.path.join(dest, name)
        _writable_copy(real, out)
        placed[real] = out
        queue.append((real, out, os.path.dirname(real)))

    while queue:
        original, bundled, exe_dir = queue.popleft()
        changes = []
        for dep in load_commands(original):
            if is_system(dep):
                continue
            real = resolve(dep, original, exe_dir)
            if real is None:
                raise RuntimeError(f"{os.path.basename(original)} needs {dep}, "
                                   "which cannot be found on this machine")
            libname = os.path.basename(dep)
            target = os.path.join(lib_dir, libname)
            if real not in placed:
                if os.path.exists(target):
                    raise RuntimeError(f"two different libraries are both named {libname}")
                _writable_copy(real, target)
                placed[real] = target
                queue.append((real, target, exe_dir))
            # Executables sit one level above lib/, libraries inside it.
            prefix = "@loader_path/lib/" if bundled in (
                os.path.join(dest, n) for n in sources) else "@loader_path/"
            changes.append((dep, prefix + os.path.basename(placed[real])))
        rewrites[bundled] = changes

    for bundled, changes in rewrites.items():
        cmd = ["install_name_tool"]
        if os.path.dirname(bundled) == lib_dir:
            cmd += ["-id", "@loader_path/" + os.path.basename(bundled)]
        for old, new in changes:
            cmd += ["-change", old, new]
        if len(cmd) > 1:
            _run(cmd + [bundled])
        # install_name_tool leaves the old signature invalid; arm64 refuses it.
        _run(["codesign", "--force", "--sign", "-", bundled])

    print(f"Bundled {len(sources)} executables and "
          f"{len(placed) - len(sources)} libraries into {dest}")


def verify(root: str, require: Tuple[str, ...] = (),
           boundary: Optional[str] = None) -> Tuple[List[str], Optional[Tuple[int, ...]]]:
    """Return (problems, highest minos) for the Mach-O files under ``root``.

    Every dependency is resolved the way dyld would — @loader_path, @rpath via
    the referrer's LC_RPATH, @executable_path — and followed transitively. It
    must land inside ``boundary`` (default ``root``). The boundary exists for
    the finished .app: PyInstaller moves the libraries up into
    Contents/Frameworks and relinks them to @rpath, so the check there is "inside
    the app", not "inside the ffmpeg folder".
    """
    boundary = os.path.realpath(boundary or root)
    problems, highest = [], None
    for name in require:
        if not os.path.isfile(os.path.join(root, name)):
            problems.append(f"{name}: missing")

    queue = deque()
    for dirpath, _, files in os.walk(root):
        for fn in files:
            path = os.path.join(dirpath, fn)
            if not os.path.islink(path) and is_macho(path):
                queue.append((path, os.path.dirname(os.path.realpath(path))))
    seen = set()
    while queue:
        path, exe_dir = queue.popleft()
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        mo = min_os(real)
        if mo and (highest is None or mo > highest):
            highest = mo
        label = os.path.relpath(real, boundary)
        for dep in load_commands(real):
            if is_system(dep):
                continue
            target = resolve(dep, real, exe_dir)
            if target is None:
                problems.append(f"{label}: {dep} cannot be resolved")
            elif os.path.commonpath([target, boundary]) != boundary:
                problems.append(f"{label}: loads {dep} from outside the bundle ({target})")
            else:
                queue.append((target, exe_dir))
    return problems, highest


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundle", help="copy and relink ffmpeg + ffprobe")
    b.add_argument("--ffmpeg", required=True)
    b.add_argument("--ffprobe", required=True)
    b.add_argument("--dest", required=True)
    v = sub.add_parser("verify", help="check a directory is self-contained")
    v.add_argument("dir")
    v.add_argument("--boundary", help="every dependency must resolve inside "
                   "this directory (default: DIR)")
    v.add_argument("--require-executables", action="store_true",
                   help="also fail if ffmpeg/ffprobe are missing from DIR")
    v.add_argument("--print-minos", action="store_true",
                   help="print only the highest deployment target (for build.sh)")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "bundle":
            bundle({"ffmpeg": args.ffmpeg, "ffprobe": args.ffprobe}, args.dest)
            args = argparse.Namespace(dir=args.dest, require_executables=True,
                                      print_minos=False, boundary=None)
        problems, highest = verify(
            args.dir, EXECUTABLES if args.require_executables else (), args.boundary)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    minos = ".".join(str(x) for x in highest) if highest else ""
    if problems:
        print("Not self-contained:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    if args.print_minos:
        print(minos)
    else:
        print(f"Self-contained: {args.dir} (highest minos {minos or 'unknown'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
