"""The tools Jarvis uses to act on the machine."""

from __future__ import annotations

import fnmatch
import getpass
import html
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from .learning import Learnings
from .memory import Memory
from .skills import SKILL_PREFIX, Skills

MAX_OUTPUT = 12000
MAX_READ_BYTES = 5_000_000
MAX_FETCH_BYTES = 2_000_000
# DuckDuckGo answers a bot-looking User-Agent with an empty "anomaly" page and
# HTTP 202, so every request goes out looking like a phone browser instead.
BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)
PAGE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
APPROVAL_REQUIRED = {
    "write_file",
    "move_path",
    "delete_path",
    "run_command",
    "open_target",
    "launch_app",
    "write_skill",
    "delete_skill",
    "restart_self",
}


def _tool(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOLS: List[Dict[str, Any]] = [
    _tool(
        "list_files",
        "List files and folders at a path. Use recursive=true to walk the whole tree.",
        {
            "path": {"type": "string", "description": "Directory or file path. Defaults to the current directory."},
            "recursive": {"type": "boolean", "description": "Walk subfolders too."},
            "limit": {"type": "integer", "description": "Maximum entries to return (default 200)."},
        },
    ),
    _tool(
        "read_file",
        "Read a text file with line numbers. Use offset and limit for large files.",
        {
            "path": {"type": "string", "description": "File to read."},
            "offset": {"type": "integer", "description": "First line to read (1-based, default 1)."},
            "limit": {"type": "integer", "description": "How many lines to read (default 400)."},
        },
        ["path"],
    ),
    _tool(
        "write_file",
        "Create or overwrite a text file, or append to it. Creates parent folders as needed.",
        {
            "path": {"type": "string", "description": "File to write."},
            "content": {"type": "string", "description": "Full text to write."},
            "append": {"type": "boolean", "description": "Append instead of overwriting."},
        },
        ["path", "content"],
    ),
    _tool(
        "make_dir",
        "Create a folder (and any missing parents).",
        {"path": {"type": "string", "description": "Folder to create."}},
        ["path"],
    ),
    _tool(
        "move_path",
        "Move or rename a file or folder.",
        {
            "source": {"type": "string", "description": "Existing path."},
            "destination": {"type": "string", "description": "New path or target folder."},
        },
        ["source", "destination"],
    ),
    _tool(
        "delete_path",
        "Delete a file, or a folder with recursive=true. This is permanent.",
        {
            "path": {"type": "string", "description": "File or folder to delete."},
            "recursive": {"type": "boolean", "description": "Delete a folder and everything inside it."},
        },
        ["path"],
    ),
    _tool(
        "search_files",
        "Find files by name or search inside file contents.",
        {
            "query": {"type": "string", "description": "Text to look for."},
            "path": {"type": "string", "description": "Folder to search in (default current directory)."},
            "mode": {"type": "string", "enum": ["content", "name"], "description": "Search inside files or only file names."},
            "glob": {"type": "string", "description": "Optional filename filter such as *.py."},
            "limit": {"type": "integer", "description": "Maximum matches (default 50)."},
        },
        ["query"],
    ),
    _tool(
        "run_command",
        "Run a shell command on this computer and return its output. Use for apps, tools and scripts.",
        {
            "command": {"type": "string", "description": "Command line to run."},
            "cwd": {"type": "string", "description": "Working directory (default current directory)."},
            "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120)."},
        },
        ["command"],
    ),
    _tool(
        "open_target",
        "Open a URL, file or folder in its default application.",
        {"target": {"type": "string", "description": "URL, file or folder to open."}},
        ["target"],
    ),
    _tool(
        "launch_app",
        "Launch an installed application by name (for example Spotify, notepad or Calculator).",
        {"name": {"type": "string", "description": "Application name."}},
        ["name"],
    ),
    _tool(
        "system_info",
        "Report the operating system, user, folders, disk space and local time.",
        {},
    ),
    _tool(
        "get_datetime",
        "Get the current local date and time.",
        {},
    ),
    _tool(
        "fetch_url",
        "Fetch a web page and return its readable text.",
        {
            "url": {"type": "string", "description": "http or https URL."},
            "max_chars": {"type": "integer", "description": "Maximum characters to return (default 8000)."},
        },
        ["url"],
    ),
    _tool(
        "web_search",
        "Search the web and return result titles, URLs and snippets. Read a promising result with fetch_url.",
        {
            "query": {"type": "string", "description": "What to search for."},
            "count": {"type": "integer", "description": "How many results (default 5, max 10)."},
        },
        ["query"],
    ),
    _tool(
        "write_skill",
        "Write yourself a new permanent tool in Python. It loads immediately as skill_<name> and is available in every later session. Use it when you need a capability you do not have.",
        {
            "name": {"type": "string", "description": "Short lowercase name, e.g. pdf_size_report."},
            "description": {"type": "string", "description": "One sentence on what the tool does, written for yourself next time."},
            "parameters": {
                "type": "object",
                "description": "Argument schema as properties, e.g. {\"path\": {\"type\": \"string\", \"description\": \"Folder to inspect\", \"required\": true}}. Names become keyword arguments to run().",
            },
            "code": {
                "type": "string",
                "description": "Python source that defines def run(...) and returns a short string. Standard library only. Call it once on real input before relying on it.",
            },
        },
        ["name", "description", "code"],
    ),
    _tool(
        "delete_skill",
        "Delete a skill you wrote earlier.",
        {"name": {"type": "string", "description": "Skill name without the skill_ prefix."}},
        ["name"],
    ),
    _tool(
        "restart_self",
        "Restart the Jarvis server so changes you made to your own code take effect. Your reply is sent "
        "first. The current code is committed to a local-only history and rolled back if the new "
        "version will not start.",
        {},
    ),
    _tool(
        "learn",
        "Record one reusable lesson after finishing a task: a command that worked, a fix, a pattern. It is shown to you in later sessions when relevant.",
        {"text": {"type": "string", "description": "The lesson, in one or two sentences."}},
        ["text"],
    ),
    _tool(
        "recall",
        "Search the lessons you learned earlier.",
        {"query": {"type": "string", "description": "What you are trying to do."}},
        ["query"],
    ),
    _tool(
        "update_plan",
        "Replace your task plan. Shown to you every turn, so you can see what is done and what is left.",
        {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered, concrete steps. Keep it to 3-7.",
            }
        },
        ["steps"],
    ),
    _tool(
        "complete_step",
        "Mark one step of the current plan as done.",
        {"step": {"type": "integer", "description": "1-based step number."}},
        ["step"],
    ),
    _tool(
        "remember",
        "Store a durable fact or preference about the user in long-term memory.",
        {"fact": {"type": "string", "description": "Short fact to remember."}},
        ["fact"],
    ),
    _tool(
        "forget",
        "Remove a fact from long-term memory.",
        {"fact": {"type": "string", "description": "Text of the fact to remove (a distinctive fragment is enough)."}},
        ["fact"],
    ),
]


# ------------------------------------------------------------------- helpers


def _path(value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("a path is required")
    return Path(os.path.expandvars(os.path.expanduser(text))).absolute()


def _int(value: Any, default: int, minimum: int = 1, maximum: int = 100000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text.rstrip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (output truncated)"


def _human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return "%.0f %s" % (value, unit) if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024
    return "%d B" % size


def _walk(base: Path, include_dirs: bool = False) -> Iterator[Path]:
    if base.is_file():
        yield base
        return
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if include_dirs:
            for name in dirs:
                yield Path(root) / name
        for name in sorted(files):
            yield Path(root) / name


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _commit_source(root: Path, note: str = "") -> str:
    """Commit the running source to its local-only history and return the revision.

    There is deliberately no remote and no credential on this machine: the
    history exists so a change to Jarvis's own code can be undone, and so a bad
    version can be rolled back automatically. Returns "" when there is nothing
    to commit or git is unavailable.
    """
    if not (root / "pyproject.toml").is_file() or shutil.which("git") is None:
        return ""
    who = {"GIT_AUTHOR_NAME": "Jarvis", "GIT_AUTHOR_EMAIL": "jarvis@localhost"}
    who.update({"GIT_COMMITTER_NAME": who["GIT_AUTHOR_NAME"], "GIT_COMMITTER_EMAIL": who["GIT_AUTHOR_EMAIL"]})
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", **who)

    def git(*args: str):
        return subprocess.run(
            ["git", "-C", str(root)] + list(args),
            capture_output=True,
            text=True,
            env=env,
        )

    try:
        if not (root / ".git").is_dir():
            git("init", "-q")
            git("config", "user.email", "jarvis@localhost")
            git("config", "user.name", "Jarvis")
        paths = [name for name in ("jarvis", "contrib", "deploy", "pyproject.toml", "README.md") if (root / name).exists()]
        git("add", "-A", "--", *paths)
        if not (git("status", "--porcelain").stdout or "").strip():
            return ""
        message = "jarvis: %s" % (note or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if git("commit", "-qm", message).returncode != 0:
            return ""
        return (git("rev-parse", "--short", "HEAD").stdout or "").strip()
    except OSError:
        return ""


def _is_url(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _result_url(href: str) -> str:
    href = html.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query).get("uddg")
        if target:
            return target[0]
    return href


# The three engines the search tool falls back through, in order. Each marks
# its results differently, so there is one pattern per markup.
_SEARCH_LINK_PATTERNS = (
    re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S),
    re.compile(r'href="([^"]+)"[^>]*class="result__a"[^>]*>(.*?)</a>', re.S),
    re.compile(r'<a[^>]*class=["\']result-link["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S),
    re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*class=["\']result-link["\'][^>]*>(.*?)</a>', re.S),
    re.compile(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S),
)
_SEARCH_SNIPPET_PATTERNS = (
    re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S),
    re.compile(r'class=["\']result-snippet["\'][^>]*>(.*?)</td>', re.S),
    re.compile(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', re.S),
)
_SEARCH_SKIP = ("duckduckgo.com", "bing.com", "microsoft.com", "google.com/search")
# Words too common to tell a relevant result from an unrelated one.
_SEARCH_STOPWORDS = {
    "the", "and", "for", "with", "from", "about", "into", "that", "this", "these",
    "those", "best", "good", "great", "top", "review", "reviews", "cheap", "buy",
    "what", "which", "who", "how", "why", "when", "where", "does", "can", "you",
    "your", "have", "has", "was", "were", "are", "its", "their", "there", "than",
}


def _relevant(results: List[Any], query: str) -> bool:
    """An engine that answers with unrelated pages has effectively failed."""
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", query.lower())
        if len(word) > 2 and word not in _SEARCH_STOPWORDS
    ]
    if not words or not results:
        return bool(results)
    haystack = " ".join(("%s %s" % (title, snippet)).lower() for title, _, snippet in results)
    return any(word in haystack for word in words)


def _parse_bing(raw: str, limit: int) -> List[Any]:
    """Bing puts the link on an anchor that wraps the heading, so work per block."""
    results: List[Any] = []
    for block in re.split(r'<li class="b_algo"', raw)[1:]:
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        if not heading:
            continue
        name = _strip_tags(heading.group(1))
        link = ""
        for match in re.finditer(r'<a[^>]+href="(https?://[^"]+)"', block):
            candidate = _result_url(match.group(1))
            if any(host in candidate for host in _SEARCH_SKIP):
                continue
            link = candidate
            break
        if not name or not link:
            continue
        caption = re.search(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', block, re.S)
        snippet = _strip_tags(caption.group(1)) if caption else ""
        results.append((name, link, snippet[:300]))
        if len(results) >= limit:
            break
    return results


def _parse_wikipedia(raw: str, limit: int) -> List[Any]:
    try:
        pages = json.loads(raw)["query"]["search"]
    except Exception:
        return []
    results = []
    for page in pages[:limit]:
        title = page.get("title") or ""
        snippet = re.sub(r"<[^>]+>", " ", page.get("snippet") or "")
        if title:
            results.append(
                (title, "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), html.unescape(snippet)[:300])
            )
    return results


def _parse_search_results(raw: str, limit: int) -> List[Any]:
    links: List[Any] = []
    seen = set()
    for pattern in _SEARCH_LINK_PATTERNS:
        for match in pattern.finditer(raw):
            url = _result_url(match.group(1))
            name = _strip_tags(match.group(2))
            if not url.startswith("http") or not name:
                continue
            if any(host in url for host in _SEARCH_SKIP):
                continue
            key = url.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            links.append((name, url))
            if len(links) >= limit:
                break
        if len(links) >= limit:
            break
    snippets: List[str] = []
    for pattern in _SEARCH_SNIPPET_PATTERNS:
        found = [_strip_tags(chunk) for chunk in pattern.findall(raw)]
        if found:
            snippets = found
            break
    results = []
    for index, (name, url) in enumerate(links[:limit]):
        snippet = snippets[index] if index < len(snippets) else ""
        results.append((name, url, snippet[:300]))
    return results


# Exa answers from a normal HTTPS API with real pages, which is what a server
# needs: DuckDuckGo and Bing both treat a datacenter address as a bot, so the
# scrapers return an anomaly page or "Best Buy, Merriam-Webster" for a query
# about mice. Without a key the scrapers are still tried, just not trusted.
EXA_URL = "https://api.exa.ai/search"

# An engine that just failed is rested, so five searches in one turn do not each
# pay fifteen seconds for the same block.
_ENGINE_COOLDOWN: Dict[str, float] = {}
_ENGINE_COOLDOWN_SECONDS = 600
_SEARCH_BUDGET_SECONDS = 30.0
_ENGINE_JUNK: Dict[str, int] = {}

_SHOPPING_QUERY = re.compile(
    r"\b(buy|buying|price|prices|cheap|cheapest|deal|deals|discount|coupon|shop|shopping|"
    r"amazon|ebay|argos|currys|aliexpress|etsy|store|under\s+\$?\d+)\b",
    re.IGNORECASE,
)
_ENCYCLOPEDIC_QUERY = re.compile(
    r"\b(who|what|when|where|why|how|history|meaning|definition|biography|born|died|founded|"
    r"invented|capital|population|species|wikipedia|explain)\b",
    re.IGNORECASE,
)


def _encyclopedic(query: str) -> bool:
    """True when Wikipedia could actually answer this.

    It is the one engine that always works from a server, so it used to answer
    everything: "best cheap gaming mouse on Amazon" came back as articles about
    Amazon Luna, a video game and a TV series, and the model then reported that
    the search results were irrelevant. It now only steps in for questions it can
    answer, and never for anything you can buy.
    """
    return bool(_ENCYCLOPEDIC_QUERY.search(query)) and not _SHOPPING_QUERY.search(query)


def _cooling(name: str) -> bool:
    return _ENGINE_COOLDOWN.get(name, 0.0) > time.monotonic()


def _cool(name: str) -> None:
    _ENGINE_COOLDOWN[name] = time.monotonic() + _ENGINE_COOLDOWN_SECONDS


def _fetch_search_page(url: str) -> str:
    request = urllib.request.Request(url, headers=PAGE_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read(MAX_FETCH_BYTES).decode("utf-8", "replace")


def _exa_results(query: str, limit: int) -> List[Any]:
    """Exa's search API, used whenever EXA_API_KEY is set."""
    key = os.environ.get("EXA_API_KEY", "").strip()
    if not key:
        return []
    payload = json.dumps(
        {
            "query": query,
            "numResults": max(1, min(limit, 10)),
            "type": "auto",
            "contents": {"text": {"maxCharacters": 400}},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        EXA_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "x-api-key": key},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read(MAX_FETCH_BYTES).decode("utf-8", "replace"))
    results: List[Any] = []
    for item in (data.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        url = str(item.get("url") or "").strip()
        if not title or not url.startswith("http"):
            continue
        snippet = " ".join(str(item.get("text") or item.get("summary") or "").split())[:300]
        results.append((title, url, snippet))
    return results


def search_web(query: str, limit: int = 5) -> Any:
    """Search the web, trying each engine in turn until one answers.

    Returns (engine, results, problems): the engine that answered, its
    (title, url, snippet) results, and one line per engine that failed.
    """
    quoted = urllib.parse.quote_plus(query)
    engines: List[Any] = []
    if os.environ.get("EXA_API_KEY", "").strip():
        engines.append(("exa", lambda: _exa_results(query, limit)))
    engines += [
        (
            "duckduckgo",
            lambda: _parse_search_results(
                _fetch_search_page("https://html.duckduckgo.com/html/?q=" + quoted), limit
            ),
        ),
        (
            "duckduckgo lite",
            lambda: _parse_search_results(
                _fetch_search_page("https://lite.duckduckgo.com/lite/?q=" + quoted), limit
            ),
        ),
        ("bing", lambda: _parse_bing(_fetch_search_page("https://www.bing.com/search?q=" + quoted), limit)),
    ]
    if _encyclopedic(query):
        wikipedia_url = (
            "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
            "&srlimit=%d&srsearch=%s" % (limit, quoted)
        )
        engines.append(("wikipedia", lambda: _parse_wikipedia(_fetch_search_page(wikipedia_url), limit)))

    problems: List[str] = []
    deadline = time.monotonic() + _SEARCH_BUDGET_SECONDS
    for name, fetch in engines:
        if _cooling(name):
            problems.append("%s: resting after a recent failure" % name)
            continue
        if time.monotonic() >= deadline:
            problems.append("%s: no time left" % name)
            break
        try:
            results = fetch()
        except Exception as exc:
            problems.append("%s: %s" % (name, exc))
            _cool(name)
            continue
        if results and _relevant(results, query):
            _ENGINE_JUNK.pop(name, None)
            return name, results, problems
        problems.append("%s: %s" % (name, "only unrelated results" if results else "no results"))
        if results:
            # A page of unrelated hits is a bot wall, but one odd query can look
            # the same, so the engine is only rested if it happens twice running.
            _ENGINE_JUNK[name] = _ENGINE_JUNK.get(name, 0) + 1
            if _ENGINE_JUNK[name] >= 2:
                _cool(name)
        else:
            _cool(name)
    return "", [], problems


# -------------------------------------------------------------------- toolbox


class Toolbox:
    def __init__(
        self,
        memory: Memory,
        approve: Optional[Callable[[str, Dict[str, Any]], str]] = None,
        auto_approve: bool = False,
        skills: Optional[Skills] = None,
        learnings: Optional[Learnings] = None,
    ) -> None:
        self.memory = memory
        self.approve = approve
        self.auto_approve = auto_approve
        self.allowed: set = set()
        self.skills = skills
        self.learnings = learnings
        self.plan: List[Dict[str, Any]] = []

    def schemas(self) -> List[Dict[str, Any]]:
        """Built-in tools plus every skill Jarvis has written for itself."""
        return list(TOOLS) + (self.skills.schemas() if self.skills is not None else [])

    # ---------------------------------------------------------------- dispatch

    def run(self, name: str, args: Dict[str, Any]) -> str:
        available = {spec["function"]["name"] for spec in self.schemas()}
        if name not in available:
            return "ERROR: unknown tool '%s'" % name
        if not isinstance(args, dict):
            return "ERROR: tool arguments must be a JSON object"
        if not name.startswith(SKILL_PREFIX) and (getattr(self, name, None) is None or name.startswith("_")):
            return "ERROR: unknown tool '%s'" % name
        if name in APPROVAL_REQUIRED and not self.auto_approve and name not in self.allowed:
            if self.approve is None:
                return "DENIED: no one can approve actions in this mode."
            decision = self.approve(name, args)
            if decision == "always":
                self.allowed.add(name)
            elif decision != "yes":
                return "DENIED: the user declined this action."
        try:
            if name.startswith(SKILL_PREFIX):
                if self.skills is None:
                    return "ERROR: skills are not available in this session."
                return self.skills.call(name, args)
            return getattr(self, name)(**args)
        except TypeError as exc:
            return "ERROR: bad arguments for %s: %s" % (name, exc)
        except Exception as exc:  # tools must never crash the assistant
            return "ERROR: %s: %s" % (type(exc).__name__, exc)

    # ------------------------------------------------------------------- tools

    def list_files(self, path: str = ".", recursive: bool = False, limit: int = 200, **_: Any) -> str:
        target = _path(path)
        if not target.exists():
            return "ERROR: no such path: %s" % target
        if target.is_file():
            stat = target.stat()
            return "%s\nfile, %s, modified %s" % (
                target,
                _human_size(stat.st_size),
                datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            )
        limit = _int(limit, 200, 1, 2000)
        if recursive:
            items = list(_walk(target, include_dirs=True))
        else:
            items = sorted(target.iterdir(), key=lambda entry: (entry.is_file(), entry.name.lower()))
        lines = []
        for entry in items[:limit]:
            if entry.is_dir():
                lines.append("[dir]  %s/" % entry)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append("[file] %s  (%s)" % (entry, _human_size(size)))
        header = "%s - %d item(s)%s" % (target, len(items), " (recursive)" if recursive else "")
        if len(items) > limit:
            lines.append("... (showing %d of %d)" % (limit, len(items)))
        return header + "\n" + "\n".join(lines)

    def read_file(self, path: str, offset: int = 1, limit: int = 400, **_: Any) -> str:
        target = _path(path)
        if not target.exists():
            return "ERROR: no such file: %s" % target
        if target.is_dir():
            return "ERROR: %s is a folder - use list_files." % target
        try:
            size = target.stat().st_size
        except OSError as exc:
            return "ERROR: cannot read %s: %s" % (target, exc)
        if size > MAX_READ_BYTES:
            return "ERROR: %s is too large to read at once (%s). Use search_files or a script." % (target, _human_size(size))
        data = target.read_bytes()
        if b"\x00" in data[:8192]:
            return "ERROR: %s looks like a binary file." % target
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        start = _int(offset, 1)
        count = _int(limit, 400, 1, 4000)
        if start > len(lines):
            return "%s has %d lines; offset %d is past the end." % (target, len(lines), start)
        end = min(len(lines), start - 1 + count)
        body = "\n".join("%5d| %s" % (number, lines[number - 1]) for number in range(start, end + 1))
        note = ""
        if end < len(lines):
            note = "\n... (%d lines total, showing %d-%d; use offset=%d to continue)" % (
                len(lines),
                start,
                end,
                end + 1,
            )
        return "%s (%d lines)%s\n%s" % (target, len(lines), note, body)

    def write_file(self, path: str, content: Any, append: bool = False, **_: Any) -> str:
        target = _path(path)
        if not isinstance(content, str):
            content = json.dumps(content, indent=2, ensure_ascii=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return "%s %d chars to %s" % ("Appended" if append else "Wrote", len(content), target)

    def make_dir(self, path: str, **_: Any) -> str:
        target = _path(path)
        target.mkdir(parents=True, exist_ok=True)
        return "Created %s" % target

    def move_path(self, source: str, destination: str, **_: Any) -> str:
        src = _path(source)
        dst = _path(destination)
        if not src.exists():
            return "ERROR: no such path: %s" % src
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return "Moved %s -> %s" % (src, dst)

    def delete_path(self, path: str, recursive: bool = False, **_: Any) -> str:
        target = _path(path)
        if not target.exists():
            return "ERROR: no such path: %s" % target
        resolved = target.resolve()
        if resolved == Path.home().resolve() or resolved.parent == resolved:
            return "ERROR: refusing to delete a home folder or filesystem root."
        if target.is_dir():
            if not recursive:
                return "ERROR: %s is a folder; pass recursive=true to delete it." % target
            shutil.rmtree(target)
        else:
            target.unlink()
        return "Deleted %s" % target

    def search_files(
        self,
        query: str,
        path: str = ".",
        mode: str = "content",
        glob: Optional[str] = None,
        limit: int = 50,
        **_: Any,
    ) -> str:
        needle = str(query or "").strip()
        if not needle:
            return "ERROR: query is required"
        base = _path(path)
        if not base.exists():
            return "ERROR: no such path: %s" % base
        limit = _int(limit, 50, 1, 500)
        pattern = glob or None
        matches: List[str] = []

        if str(mode).lower() == "name":
            name_pattern = pattern or "*%s*" % needle
            for entry in _walk(base, include_dirs=True):
                if fnmatch.fnmatch(entry.name.lower(), name_pattern.lower()):
                    matches.append(_relative(entry, base) + ("/" if entry.is_dir() else ""))
                    if len(matches) >= limit:
                        break
        else:
            lowered = needle.lower()
            for entry in _walk(base):
                if pattern and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                    continue
                try:
                    if entry.stat().st_size > 1_000_000:
                        continue
                    data = entry.read_bytes()
                except OSError:
                    continue
                if b"\x00" in data[:4096]:
                    continue
                try:
                    text = data.decode("utf-8", "replace")
                except Exception:
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if lowered in line.lower():
                        matches.append("%s:%d: %s" % (_relative(entry, base), number, line.strip()[:300]))
                        if len(matches) >= limit:
                            break
                if len(matches) >= limit:
                    break

        if not matches:
            return "No matches for %r in %s." % (needle, base)
        header = "%d match(es) for %r in %s%s" % (
            len(matches),
            needle,
            base,
            " (limited)" if len(matches) >= limit else "",
        )
        return header + "\n" + "\n".join(matches)

    def run_command(self, command: str, cwd: str = ".", timeout: int = 120, **_: Any) -> str:
        command = str(command or "").strip()
        if not command:
            return "ERROR: command is required"
        directory = _path(cwd or ".")
        if not directory.is_dir():
            directory = Path.cwd()
        seconds = _int(timeout, 120, 1, 600)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(directory),
                capture_output=True,
                text=True,
                timeout=seconds,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: command timed out after %d seconds" % seconds
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        if not output:
            output = "(no output)"
        return "exit=%d\n%s" % (completed.returncode, _truncate(output))

    def open_target(self, target: str, **_: Any) -> str:
        target = str(target or "").strip()
        if not target:
            return "ERROR: target is required"
        if _is_url(target):
            webbrowser.open(target)
            return "Opened %s in the default browser" % target
        path = _path(target)
        if path.exists():
            system = platform.system()
            if system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(
                    ["xdg-open", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            return "Opened %s" % path
        # Not a path and not a URL: treat it as an application name.
        return self.launch_app(target)

    def launch_app(self, name: str, **_: Any) -> str:
        name = str(name or "").strip()
        if not name:
            return "ERROR: name is required"
        if not re.fullmatch(r"[\w .+-]+", name):
            return "ERROR: app names may only contain letters, numbers, spaces, dots, plus and hyphen. Use open_target for a path."
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(
                ["cmd", "/c", "start", "", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif system == "Darwin":
            result = subprocess.run(["open", "-a", name], capture_output=True, text=True)
            if result.returncode != 0:
                return "ERROR: could not launch %r: %s" % (name, (result.stderr or "").strip())
        else:
            executable = shutil.which(name)
            if not executable:
                return "ERROR: no installed app called %r was found on PATH." % name
            subprocess.Popen(
                [executable],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return "Launched %s" % name

    def system_info(self, **_: Any) -> str:
        try:
            usage = shutil.disk_usage(str(Path.home()))
            disk = "%s free of %s" % (_human_size(usage.free), _human_size(usage.total))
        except OSError:
            disk = "unknown"
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        now = datetime.now().astimezone()
        return "\n".join(
            [
                "OS: %s %s" % (platform.system(), platform.release()),
                "Machine: %s" % platform.machine(),
                "User: %s" % user,
                "Home: %s" % Path.home(),
                "Working directory: %s" % Path.cwd(),
                "Python: %s (%s)" % (platform.python_version(), sys.executable),
                "CPU cores: %s" % os.cpu_count(),
                "Home disk: %s" % disk,
                "Local time: %s" % now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            ]
        )

    def get_datetime(self, **_: Any) -> str:
        now = datetime.now().astimezone()
        return now.strftime("%A, %d %B %Y, %H:%M:%S %Z")

    def fetch_url(self, url: str, max_chars: int = 8000, **_: Any) -> str:
        url = str(url or "").strip()
        if not _is_url(url):
            # Models often drop the scheme: amazon.com/s rather than https://...
            if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+([/?#]|$)", url, re.IGNORECASE):
                url = "https://" + url
        if not _is_url(url):
            return "ERROR: %r is not a web address. Give one like https://example.com/page" % url
        headers = dict(PAGE_HEADERS)
        headers["Accept"] = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                content_type = response.headers.get("Content-Type", "")
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read(MAX_FETCH_BYTES)
        except Exception as exc:
            return "ERROR: could not fetch %s: %s" % (url, exc)
        text = raw.decode(charset, "replace")
        if "html" in content_type.lower() or text.lstrip().lower().startswith("<!doctype html"):
            text = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text)
        text = text.strip()
        if not text:
            return "The page returned no readable text."
        limit = _int(max_chars, 8000, 500, 50000)
        if len(text) > limit:
            text = text[:limit] + "\n... (truncated)"
        return text

    def web_search(self, query: str = "", count: int = 5, **_: Any) -> str:
        query = " ".join(str(query or "").split())
        if not query:
            return "ERROR: query is required"
        limit = _int(count, 5, 1, 10)
        engine, results, problems = search_web(query, limit)
        if results:
            lines = [
                "Results for %r from %s. These addresses are real: give them to the user as links."
                % (query, engine)
            ]
            for number, (title, link, snippet) in enumerate(results, 1):
                lines.append("%d. %s\n   %s" % (number, title, link))
                if snippet:
                    lines.append("   " + snippet)
            return "\n".join(lines)
        return (
            "Search is unavailable right now: no engine returned results for %r (%s). "
            "Do not search again this turn - every engine is blocked, so retrying only wastes steps. "
            "Tell the user search is down in one line and answer from what you already know."
            % (query, "; ".join(problems))
        )

    # ----------------------------------------------------------- self-extension

    def write_skill(
        self,
        name: str = "",
        description: str = "",
        parameters: Any = None,
        code: str = "",
        **_: Any,
    ) -> str:
        if self.skills is None:
            return "ERROR: skill storage is not available in this session."
        return self.skills.save(name, description, parameters, code)

    def delete_skill(self, name: str = "", **_: Any) -> str:
        if self.skills is None:
            return "ERROR: skill storage is not available in this session."
        return self.skills.delete(name)

    def restart_self(self, **_: Any) -> str:
        """Commit the source, restart the service, and roll back if it will not start."""
        if not Path("/run/systemd/system").is_dir():
            return "ERROR: not running under systemd - restart it however you started me."
        root = Path(__file__).resolve().parent.parent
        commit = _commit_source(root, "before restart")
        start = "sudo -n systemctl restart jarvis-serve 2>/dev/null || systemctl restart jarvis-serve"
        rollback = "git -C %s reset --hard HEAD~1" % shlex.quote(str(root))
        script = (
            "sleep 2; " + start + "; "
            # Restart=always keeps retrying a broken import, so give it a window
            # to come up, then put the previous commit back.
            "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do sleep 2; "
            "systemctl is-active --quiet jarvis-serve && exit 0; done; "
            + rollback + " >/dev/null 2>&1 && " + start
        )
        try:
            subprocess.Popen(
                ["sh", "-c", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            return "ERROR: could not schedule the restart: %s" % exc
        kept = " Code committed as %s." % commit if commit else ""
        return (
            "Restarting in 2 seconds.%s If the new version will not start, I roll back one commit "
            "and restart the previous one. Check with: journalctl -u jarvis-serve -n 30" % kept
        )

    def learn(self, text: str = "", **_: Any) -> str:
        if self.learnings is None:
            return "ERROR: lesson storage is not available in this session."
        lesson = " ".join(str(text or "").split())
        if self.learnings.add(lesson):
            return "Learned: %s" % lesson[:200]
        return "Nothing new to learn from that (too short, or already known)."

    def recall(self, query: str = "", **_: Any) -> str:
        if self.learnings is None:
            return "ERROR: lesson storage is not available in this session."
        hits = self.learnings.relevant(str(query or ""), limit=8)
        if not hits:
            return "No lesson yet matches %r." % query
        return "Lessons that may help:\n" + "\n".join("- " + hit for hit in hits)

    # ------------------------------------------------------------------- plan

    def update_plan(self, steps: Any = None, **_: Any) -> str:
        if isinstance(steps, str):
            steps = [line.strip(" -\u2022\t") for line in steps.splitlines() if line.strip()]
        if isinstance(steps, dict):
            steps = list(steps.values())
        if not isinstance(steps, (list, tuple)):
            return "ERROR: steps must be a list of strings"
        self.plan = [
            {"step": " ".join(str(step).split())[:200], "done": False}
            for step in steps
            if str(step).strip()
        ][:20]
        if not self.plan:
            return "ERROR: give me at least one step"
        return "Plan set.\n" + self.plan_block()

    def complete_step(self, step: int = 0, **_: Any) -> str:
        if not self.plan:
            return "ERROR: there is no plan yet - call update_plan first"
        index = _int(step, 1) - 1
        if index < 0 or index >= len(self.plan):
            return "ERROR: step %s is not in the plan (1-%d)" % (step, len(self.plan))
        self.plan[index]["done"] = True
        remaining = sum(1 for item in self.plan if not item["done"])
        return "Step done (%d left).\n%s" % (remaining, self.plan_block())

    def plan_block(self) -> str:
        if not self.plan:
            return "(no plan yet)"
        lines = []
        for number, item in enumerate(self.plan, 1):
            lines.append("%s %d. %s" % ("[x]" if item.get("done") else "[ ]", number, item["step"]))
        return "\n".join(lines)

    def remember(self, fact: str, **_: Any) -> str:
        fact = " ".join(str(fact or "").split()).strip()
        if not fact:
            return "ERROR: fact is required"
        if self.memory.add(fact):
            return "Remembered: %s" % fact
        return "Already in memory."

    def forget(self, fact: str, **_: Any) -> str:
        removed = self.memory.remove(str(fact or ""))
        if not removed:
            return "Nothing in memory matched %r." % fact
        return "Removed %d memory item(s)." % removed
