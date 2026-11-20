"""Tools Jarvis writes for itself.

Each skill is a Python file in ``~/.jarvis/skills`` that declares NAME,
DESCRIPTION, PARAMETERS and a ``run`` function. Loaded skills become real
tools (``skill_<name>``) in the same session they were written, and in every
later session, so Jarvis can extend its own abilities when a task needs
something it cannot do yet.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
SKILL_PREFIX = "skill_"
MAX_CODE = 60_000


class SkillError(RuntimeError):
    """A skill file could not be loaded."""


class Skills:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.modules: Dict[str, Any] = {}
        self.errors: Dict[str, str] = {}
        self.refresh()

    # ------------------------------------------------------------------ loading

    def refresh(self) -> None:
        """Re-read the skills folder; broken skills are reported, not fatal."""
        self.modules.clear()
        self.errors.clear()
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module = self._import(path)
            except Exception as exc:  # a broken skill must never stop startup
                self.errors[path.stem] = "%s: %s" % (type(exc).__name__, exc)
                continue
            name = str(getattr(module, "NAME", "") or path.stem).strip().lower()
            if not NAME_RE.match(name) or not callable(getattr(module, "run", None)):
                self.errors[path.stem] = "no NAME or no callable run()"
                continue
            self.modules[name] = module

    def _import(self, path: Path) -> Any:
        spec = importlib.util.spec_from_file_location("jarvis_skill_" + path.stem, path)
        if spec is None or spec.loader is None:
            raise SkillError("cannot load %s" % path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    # ------------------------------------------------------------------- schema

    def schemas(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for name, module in sorted(self.modules.items()):
            raw = getattr(module, "PARAMETERS", None)
            properties: Dict[str, Any] = {}
            required: List[str] = []
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if not isinstance(key, str) or not key.isidentifier():
                        continue
                    if isinstance(value, dict):
                        cleaned = {k: v for k, v in value.items() if k != "required"}
                        properties[key] = cleaned
                        if value.get("required"):
                            required.append(key)
                    else:
                        properties[key] = {"type": "string", "description": str(value)}
            description = str(getattr(module, "DESCRIPTION", "") or name)
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": SKILL_PREFIX + name,
                        "description": "[skill you wrote] " + description[:400],
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return tools

    def describe(self) -> List[str]:
        rows = []
        for name, module in sorted(self.modules.items()):
            rows.append("%-14s %s" % (SKILL_PREFIX + name, str(getattr(module, "DESCRIPTION", ""))[:100]))
        for name, error in sorted(self.errors.items()):
            rows.append("%-14s BROKEN (%s)" % (name, error))
        return rows

    def names(self) -> List[str]:
        return sorted(self.modules)

    # --------------------------------------------------------------------- call

    def call(self, tool_name: str, args: Dict[str, Any]) -> str:
        name = tool_name[len(SKILL_PREFIX):] if tool_name.startswith(SKILL_PREFIX) else tool_name
        module = self.modules.get(name)
        if module is None:
            return "ERROR: there is no skill called %r. Skills: %s" % (
                name,
                ", ".join(self.names()) or "none yet",
            )
        try:
            result = module.run(**args)
        except TypeError as exc:
            return "ERROR: bad arguments for skill %s: %s" % (name, exc)
        except (Exception, SystemExit):
            return "ERROR: skill %s failed:\n%s" % (name, traceback.format_exc(limit=4))
        if result is None:
            return "Skill %s ran but returned nothing." % name
        return str(result)

    # --------------------------------------------------------------------- save

    def save(
        self,
        name: str,
        description: str,
        parameters: Any,
        code: str,
    ) -> str:
        name = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not NAME_RE.match(name):
            return (
                "ERROR: skill names must be 2-41 characters of lowercase letters, digits "
                "and underscores, starting with a letter (got %r)." % name
            )
        if not str(code or "").strip():
            return "ERROR: code is required."
        if len(str(code)) > MAX_CODE:
            return "ERROR: code is too long (limit %d characters)." % MAX_CODE
        if not isinstance(parameters, dict):
            parameters = {}

        header = "NAME = %r\nDESCRIPTION = %r\nPARAMETERS = %r\n\n" % (
            name,
            " ".join(str(description or name).split())[:300],
            parameters,
        )
        source = header + str(code).rstrip() + "\n"

        try:
            compile(source, name + ".py", "exec")
        except SyntaxError as exc:
            return "ERROR: syntax error on line %s: %s. Fix it and call write_skill again." % (
                exc.lineno,
                exc.msg,
            )

        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (name + ".py")
        path.write_text(source, encoding="utf-8")
        self.refresh()

        if name not in self.modules:
            detail = self.errors.get(name, "it does not define a callable run()")
            return (
                "ERROR: wrote %s but it does not load: %s. Fix the code and call "
                "write_skill again with the same name." % (path, detail)
            )
        return "Skill %r saved to %s and loaded as tool %s. You can call it now." % (
            name,
            path,
            SKILL_PREFIX + name,
        )

    def delete(self, name: str) -> str:
        name = str(name or "").strip().lower()
        path = self.directory / (name + ".py")
        if not path.exists():
            return "ERROR: no skill file for %r." % name
        try:
            path.unlink()
        except OSError as exc:
            return "ERROR: could not delete %s: %s" % (path, exc)
        self.refresh()
        return "Deleted skill %r." % name
