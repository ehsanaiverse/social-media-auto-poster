"""Every monkeypatch on our own package must reach the code it is meant to change.

A test that patches ``module.name`` only affects code that looks ``name`` up *in that module*. Since the
code was split out of main.py into operations/, a patch left on the old module can apply without error and
still change nothing; a "must never be called" check then passes whatever the code does. This test reads
every ``setattr(<our module>, "<name>", ...)`` in tests/ and fails if:

1. the patched module does not itself use the name (it neither defines it nor looks it up in its own code:
   for example a name main.py only re-exports for other callers), or
2. another module (src/, app/, scripts/) imported the name *by value* from the patched module and uses it,
   so its calls go around the patch (call it through the module, ``module.name(...)``, instead).

A patch whose name is not a literal string (``setattr(nodes, node_name, ...)``) cannot be checked here.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = "social_media_auto_poster"
BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src" / PACKAGE


@dataclass(frozen=True)
class Patch:
    where: str  # "file.py:line", for the failure message.
    module: str  # Dotted module name, e.g. "social_media_auto_poster.operations.run".
    name: str


def module_file(dotted: str, src: Path = SRC, package: str = PACKAGE) -> Path | None:
    """The source file of one of our modules, or None if ``dotted`` is not one (a class, another package)."""
    if dotted != package and not dotted.startswith(package + "."):
        return None
    parts = dotted.split(".")[1:]
    path = src.joinpath(*parts)
    for candidate in (path.with_suffix(".py"), path / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def find_patches(source: str, filename: str, is_module) -> list[Patch]:
    """Every ``x.setattr(<alias of one of our modules>, "<name>", ...)`` (or ``x.setattr("<module>.<name>", ...)``)."""
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                if is_module(dotted):
                    aliases[alias.asname or alias.name] = dotted
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and is_module(alias.name):
                    aliases[alias.asname] = alias.name
    patches = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"):
            continue
        args = node.args
        where = f"{filename}:{node.lineno}"
        if (len(args) >= 3 and isinstance(args[0], ast.Name) and args[0].id in aliases
                and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str)):
            patches.append(Patch(where, aliases[args[0].id], args[1].value))
        elif len(args) >= 2 and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            dotted, _, name = args[0].value.rpartition(".")
            if is_module(dotted):
                patches.append(Patch(where, dotted, name))
    return patches


def problems(patch: Patch, sources: dict[str, str]) -> list[str]:
    """Why ``patch`` might not reach the code it means to change (empty if it does). ``sources``: module -> code."""
    found = []
    tree = ast.parse(sources[patch.module])
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined.update(t.id for t in targets if isinstance(t, ast.Name))
    looked_up = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    if patch.name not in defined and patch.name not in looked_up:
        found.append(
            f"{patch.where}: patches {patch.module}.{patch.name}, but that module's own code never uses "
            f"{patch.name} (it only imports or re-exports it), so the patch changes nothing. Patch the module "
            f"whose code looks {patch.name} up."
        )
    for other, code in sources.items():
        if other == patch.module:
            continue
        other_tree = ast.parse(code)
        used = {n.id for n in ast.walk(other_tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        for node in ast.walk(other_tree):
            if isinstance(node, ast.ImportFrom) and node.module == patch.module and node.level == 0:
                for alias in node.names:
                    if alias.name == patch.name and (alias.asname or alias.name) in used:
                        found.append(
                            f"{patch.where}: patches {patch.module}.{patch.name}, but {other} imports "
                            f"{patch.name} by value and calls its own copy, which the patch does not reach. "
                            f"Call it through the module ({patch.module.rsplit('.', 1)[-1]}.{patch.name}) there."
                        )
    return found


def _all_sources() -> dict[str, str]:
    """Our package's modules, plus the API (app/) and the sandbox tester (scripts/), by dotted name."""
    sources = {}
    for path in SRC.rglob("*.py"):
        parts = path.relative_to(SRC.parent).with_suffix("").parts
        dotted = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        sources[dotted] = path.read_text(encoding="utf-8")
    for folder in ("app", "scripts"):
        for path in (BACKEND / folder).rglob("*.py"):
            sources[f"{folder}:{path.relative_to(BACKEND).as_posix()}"] = path.read_text(encoding="utf-8")
    return sources


def _all_patches() -> list[Patch]:
    patches = []
    for path in sorted((BACKEND / "tests").rglob("test_*.py")):
        patches += find_patches(path.read_text(encoding="utf-8"), path.relative_to(BACKEND).as_posix(),
                                lambda dotted: module_file(dotted) is not None)
    return patches


def test_every_patch_on_our_package_reaches_its_code():
    sources = _all_sources()
    patches = _all_patches()

    assert len(patches) >= 60, f"only {len(patches)} patches found: the scan itself may be broken"
    found = [message for patch in patches for message in problems(patch, sources)]
    assert found == [], "\n".join(found)


# The checker itself, on small made-up modules (so it cannot pass by checking nothing).

FAKE = {
    "pkg.tools": "def helper():\n    return 1\n",
    "pkg.reexport": "from pkg.tools import helper\n",  # Only re-exports helper; its code never calls it.
    "pkg.user": "from pkg.tools import helper\n\ndef work():\n    return helper()\n",  # Calls its own copy.
    "pkg.through": "from pkg import tools\n\ndef work():\n    return tools.helper()\n",  # Through the module.
}


def _fake_patch(module: str, name: str = "helper") -> Patch:
    return Patch("test_x.py:1", module, name)


def test_checker_flags_a_patch_on_a_module_that_only_reexports():
    messages = problems(_fake_patch("pkg.reexport"), FAKE)

    assert any("never uses helper" in m for m in messages)


def test_checker_flags_a_patch_that_another_module_calls_around():
    messages = problems(_fake_patch("pkg.tools"), FAKE)

    assert any("pkg.user imports helper by value" in m for m in messages)


def test_checker_accepts_a_patch_where_the_name_is_looked_up():
    assert problems(_fake_patch("pkg.user"), FAKE) == []  # pkg.user's own code looks helper up there.
    assert problems(_fake_patch("pkg.tools"), {k: v for k, v in FAKE.items() if k != "pkg.user"}) == []


@pytest.mark.parametrize(("line", "expected"), [
    ('monkeypatch.setattr(cli, "run_job", spy)', [("social_media_auto_poster.main", "run_job")]),
    ('patch.setattr(run_ops, "run_job", spy)', [("social_media_auto_poster.operations.run", "run_job")]),
    ('monkeypatch.setattr("social_media_auto_poster.operations.clock.now", fake)',
     [("social_media_auto_poster.operations.clock", "now")]),
    ('monkeypatch.setattr(SomeClass, "publish", fake)', []),  # A class, not one of our modules: not checked here.
    ('monkeypatch.setattr(nodes, node_name, fake)', []),  # Not a literal name: cannot be checked statically.
])
def test_checker_finds_patches_in_test_code(line, expected):
    source = (
        "from social_media_auto_poster import main as cli\n"
        "from social_media_auto_poster.operations import run as run_ops\n"
        "from social_media_auto_poster.graph import nodes\n"
        "from social_media_auto_poster.platforms.simulator import Simulator as SomeClass\n"
        f"def test_x(monkeypatch, patch):\n    {line}\n"
    )
    found = find_patches(source, "test_x.py", lambda dotted: module_file(dotted) is not None)

    assert [(p.module, p.name) for p in found] == expected
