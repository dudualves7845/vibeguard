"""Dependency verification for Python (pip/virtualenv) and JS/TS (package.json).

Detects AI-hallucinated packages that do not exist in the environment or manifest.
"""

import json
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Set

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

# Known Python standard library module names (Python 3.8 - 3.14)
PYTHON_STDLIB = {
    "abc", "argparse", "array", "ast", "asyncio", "atexit", "base64", "bdb", "binascii",
    "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd",
    "code", "codecs", "codeop", "collections", "colorsys", "compileall", "concurrent",
    "configparser", "contextlib", "contextvars", "copy", "copyreg", "cProfile", "crypt",
    "csv", "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
    "dis", "distutils", "doctest", "email", "encodings", "ensurepip", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions", "ftplib",
    "functools", "gc", "getopt", "getpass", "gettext", "glob", "graphlib", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword", "lib2to3",
    "linecache", "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math",
    "mimetypes", "mmap", "modulefinder", "msilib", "msvcrt", "multiprocessing", "netrc",
    "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib", "poplib",
    "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
    "pyclbr", "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr",
    "socket", "socketserver", "spwd", "sqlite3", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sunau", "symbol", "symtable", "sys",
    "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios",
    "test", "textwrap", "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo", "types",
    "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv", "warnings",
    "wave", "weakref", "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib",
    "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo"
}

# Common standard JS/Node builtins
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "domain",
    "events", "fs", "fs/promises", "http", "http2", "https", "net", "os", "path",
    "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "timers", "tls", "trace_events", "tty", "url", "util", "v8",
    "vm", "wasi", "worker_threads", "zlib"
}

# Go standard library packages
GO_STDLIB = {
    "archive", "archive/tar", "archive/zip",
    "bufio", "bytes", "compress", "compress/bzip2", "compress/flate", "compress/gzip",
    "compress/lzw", "compress/zlib", "container", "container/heap", "container/list",
    "container/ring", "context", "crypto", "crypto/aes", "crypto/cipher", "crypto/des",
    "crypto/dsa", "crypto/ecdsa", "crypto/ed25519", "crypto/elliptic", "crypto/hmac",
    "crypto/md5", "crypto/rand", "crypto/rsa", "crypto/sha1", "crypto/sha256",
    "crypto/sha512", "crypto/subtle", "crypto/tls", "crypto/x509", "database",
    "database/sql", "database/sql/driver", "debug", "embed", "encoding", "encoding/ascii85",
    "encoding/asn1", "encoding/base32", "encoding/base64", "encoding/binary", "encoding/csv",
    "encoding/gob", "encoding/hex", "encoding/json", "encoding/xml", "errors", "expvar",
    "flag", "fmt", "go", "go/ast", "go/build", "go/constant", "go/doc", "go/format",
    "go/parser", "go/printer", "go/scanner", "go/token", "go/types", "hash", "hash/adler32",
    "hash/crc32", "hash/crc64", "hash/fnv", "hash/maphash", "html", "html/template",
    "image", "image/color", "image/draw", "image/gif", "image/jpeg", "image/png",
    "index", "io", "io/fs", "io/ioutil", "log", "log/slog", "log/syslog", "math",
    "math/big", "math/bits", "math/cmplx", "math/rand", "mime", "mime/multipart",
    "mime/quotedprintable", "net", "net/http", "net/http/cgi", "net/http/cookiejar",
    "net/http/fcgi", "net/http/httptest", "net/http/httptrace", "net/http/httputil",
    "net/http/pprof", "net/mail", "net/netip", "net/rpc", "net/smtp", "net/url",
    "os", "os/exec", "os/signal", "os/user", "path", "path/filepath", "plugin",
    "reflect", "regexp", "regexp/syntax", "runtime", "runtime/cgo", "runtime/debug",
    "runtime/metrics", "runtime/pprof", "runtime/trace", "sort", "strconv", "strings",
    "sync", "sync/atomic", "syscall", "testing", "testing/fstest", "testing/iotest",
    "testing/quick", "text", "text/scanner", "text/tabwriter", "text/template",
    "time", "unicode", "unicode/utf16", "unicode/utf8", "unsafe"
}

# Rust standard library crates/modules
RUST_STDLIB = {
    "std", "core", "alloc", "proc_macro", "test"
}

# Canonical PyPI distribution name (lowercase) to import module name(s) mapping
PYPI_DISTRIBUTION_TO_IMPORT: Dict[str, List[str]] = {
    "pyyaml": ["yaml"],
    "pillow": ["pil"],
    "opencv-python": ["cv2"],
    "opencv-python-headless": ["cv2"],
    "opencv-contrib-python": ["cv2"],
    "opencv-contrib-python-headless": ["cv2"],
    "beautifulsoup4": ["bs4"],
    "psycopg2": ["psycopg2"],
    "psycopg2-binary": ["psycopg2"],
    "pydantic-settings": ["pydantic_settings"],
    "scikit-learn": ["sklearn"],
    "scikit-image": ["skimage"],
    "protobuf": ["google.protobuf"],
    "google-api-python-client": ["googleapiclient"],
    "python-dateutil": ["dateutil"],
    "python-dotenv": ["dotenv"],
    "python-jose": ["jose"],
    "python-multipart": ["multipart"],
    "pymongo": ["pymongo", "bson"],
    "attrs": ["attr"],
    "msgpack-python": ["msgpack"],
    "pika": ["pika"],
    "sqlalchemy": ["sqlalchemy"],
}


class DependencyChecker:
    """Detects hallucinated or uninstalled dependencies."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.npm_dependencies = self._load_npm_packages()
        self.installed_python_modules = self._load_python_packages()
        self.go_modules = self._load_go_modules()
        self.rust_crates = self._load_rust_crates()

    def _load_go_modules(self) -> Set[str]:
        mods: Set[str] = set()
        for mod_file in self.project_root.glob("**/go.mod"):
            try:
                with open(mod_file, "r", encoding="utf-8") as f:
                    in_require = False
                    for line in f:
                        line = line.strip()
                        if line.startswith("module "):
                            mods.add(line.split()[1].strip())
                        elif line.startswith("require ("):
                            in_require = True
                            continue
                        elif in_require and line == ")":
                            in_require = False
                            continue
                        elif line.startswith("require "):
                            parts = line.split()
                            if len(parts) >= 2:
                                mods.add(parts[1].strip())
                        elif in_require and line:
                            parts = line.split()
                            if parts:
                                mods.add(parts[0].strip())
            except Exception:
                pass
        return mods

    def _load_rust_crates(self) -> Set[str]:
        crates: Set[str] = set()
        for cargo_file in self.project_root.glob("**/Cargo.toml"):
            try:
                if tomllib is not None:
                    with open(cargo_file, "rb") as f:
                        data = tomllib.load(f)
                    for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
                        crates.update(data.get(sec, {}).keys())
                        for target in data.get("target", {}).values():
                            crates.update(target.get(sec, {}).keys())
                    crates.update(data.get("workspace", {}).get("dependencies", {}).keys())
                else:
                    raw = cargo_file.read_text(encoding="utf-8")
                    in_dependencies = False
                    for line in raw.splitlines():
                        line = line.strip()
                        if line.startswith("["):
                            section = line.strip("[] ")
                            in_dependencies = bool(re.search(r"(?:^|\.)(?:dependencies|dev-dependencies|build-dependencies)$", section))
                            table = re.search(r"(?:^|\.)(?:dependencies|dev-dependencies|build-dependencies)\.([\w-]+)$", section)
                            if table:
                                crates.add(table.group(1))
                            continue
                        if in_dependencies and "=" in line and not line.startswith("#"):
                            k = line.split("=")[0].strip()
                            if k:
                                crates.add(k)
            except Exception:
                pass
        return crates

    def _load_npm_packages(self) -> Set[str]:
        deps: Set[str] = set()
        # Look for root package.json as well as nested package.json (monorepos)
        pkg_files = list(self.project_root.glob("**/package.json"))
        for pkg_file in pkg_files:
            if "node_modules" in pkg_file.parts:
                continue
            try:
                with open(pkg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                deps.update(data.get("dependencies", {}).keys())
                deps.update(data.get("devDependencies", {}).keys())
                deps.update(data.get("peerDependencies", {}).keys())
            except Exception:
                continue
        return deps

    def _load_python_packages(self) -> Set[str]:
        # Get installed packages via standard library pkgutil/sys.modules/importlib.metadata
        mods = set(PYTHON_STDLIB)
        def add_pkg(name: str):
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", name)
            if not match:
                return
            lowered = match.group(1).lower()
            mods.add(lowered.replace("-", "_"))
            mods.add(lowered)
            if lowered in PYPI_DISTRIBUTION_TO_IMPORT:
                for imp in PYPI_DISTRIBUTION_TO_IMPORT[lowered]:
                    mods.add(imp.lower())
                    mods.add(imp.lower().replace("-", "_"))

        try:
            import importlib.metadata
            for dist in importlib.metadata.distributions():
                name = dist.metadata.get("Name")
                if name:
                    add_pkg(name)
                read_text = getattr(dist, "read_text", None)
                top_level = read_text("top_level.txt") if read_text else None
                if top_level:
                    mods.update(module.strip().lower() for module in top_level.splitlines() if module.strip())
        except Exception:
            pass

        # Inspect requirements.txt (root and subdirectories)
        for req_file in self.project_root.glob("**/requirements*.txt"):
            if ".venv" in req_file.parts or "venv" in req_file.parts:
                continue
            try:
                with open(req_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            add_pkg(line)
            except Exception:
                pass

        # Inspect pyproject.toml if present
        pyproject_file = self.project_root / "pyproject.toml"
        if pyproject_file.exists():
            try:
                if tomllib is not None:
                    with open(pyproject_file, "rb") as f:
                        toml_data = tomllib.load(f)
                    for dep in toml_data.get("project", {}).get("dependencies", []):
                        add_pkg(dep)
                    for group in toml_data.get("project", {}).get("optional-dependencies", {}).values():
                        for dep in group:
                            add_pkg(dep)
                    for pkg in toml_data.get("tool", {}).get("poetry", {}).get("dependencies", {}).keys():
                        add_pkg(pkg)
                else:
                    raw = pyproject_file.read_text(encoding="utf-8")
                    for m in re.finditer(r'["\']([a-zA-Z0-9_\-]+)(?:[>=<~;].*?)?["\']', raw):
                        add_pkg(m.group(1))
            except Exception:
                pass

        return mods

    def check_python_import(self, imp_name: str, local_packages: Set[str]) -> bool:
        """Returns True if import is legitimate, False if potentially hallucinated."""
        if not imp_name:
            return True
        root_mod = imp_name.split(".")[0].strip()
        if not root_mod:
            return True

        # Check stdlib
        if root_mod in PYTHON_STDLIB:
            return True

        # Check local repo packages/modules
        if root_mod in local_packages:
            return True

        # Check installed or declared modules
        if root_mod.lower() in self.installed_python_modules or root_mod.lower().replace("-", "_") in self.installed_python_modules:
            return True
        qualified = imp_name.lower()
        if any(qualified == mod or qualified.startswith(mod + ".") or mod.startswith(qualified + ".")
               for mod in self.installed_python_modules if "." in mod):
            return True

        # Check if file/directory exists relative to project root or src directory
        if (self.project_root / f"{root_mod}.py").exists() or (self.project_root / root_mod).is_dir():
            return True

        src_dir = self.project_root / "src"
        if src_dir.is_dir():
            if (src_dir / f"{root_mod}.py").exists() or (src_dir / root_mod).is_dir():
                return True

        return False

    def check_js_import(self, imp_name: str, local_packages: Set[str]) -> bool:
        """Returns True if JS/TS import is legitimate, False if potentially hallucinated."""
        if not imp_name:
            return True

        # Relative or absolute file imports
        if imp_name.startswith((".", "/", "@/", "~/")):
            return True

        # Node builtins
        if imp_name.startswith("node:"):
            return True
        if imp_name in NODE_BUILTINS or imp_name.split("/")[0] in NODE_BUILTINS:
            return True

        # Scope package: e.g. @types/node or @org/pkg
        pkg_key = imp_name
        if imp_name.startswith("@"):
            parts = imp_name.split("/")
            if len(parts) >= 2:
                pkg_key = f"{parts[0]}/{parts[1]}"
        else:
            pkg_key = imp_name.split("/")[0]

        if pkg_key in self.npm_dependencies or f"@types/{pkg_key}" in self.npm_dependencies:
            return True

        if pkg_key in local_packages:
            return True

        return False

    def check_go_import(self, imp_name: str, local_packages: Set[str]) -> bool:
        """Returns True if Go package import is legitimate (stdlib, go.mod, or local)."""
        if not imp_name:
            return True

        # Go standard library
        if imp_name in GO_STDLIB:
            return True

        # Relative local imports
        if imp_name.startswith(("./", "../", "/")):
            return True

        # Matches declared module or dependency in go.mod
        for mod in self.go_modules:
            if imp_name == mod or imp_name.startswith(f"{mod}/"):
                return True

        # Matches local directory/package
        first_segment = imp_name.split("/")[0]
        if first_segment in local_packages:
            return True
        if (self.project_root / imp_name).is_dir():
            return True

        return False

    def check_rust_import(self, crate_name: str, local_packages: Set[str]) -> bool:
        """Returns True if Rust crate import is legitimate (stdlib, Cargo.toml, or local)."""
        if not crate_name:
            return True

        # Rust standard library / keywords
        if crate_name in RUST_STDLIB or crate_name in ("crate", "super", "self"):
            return True

        # Matches declared dependency in Cargo.toml (handling '-' vs '_')
        crate_normalized = crate_name.lower().replace("_", "-")
        for dep in self.rust_crates:
            if dep.lower().replace("_", "-") == crate_normalized:
                return True

        # Matches local workspace or directory
        if crate_name in local_packages or crate_normalized in local_packages:
            return True
        if (self.project_root / crate_name).is_dir():
            return True

        return False
