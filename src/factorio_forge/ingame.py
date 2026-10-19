"""Hand a blueprint over only after the game itself has placed it.

``factorio-forge deliver plan.json`` builds a plan like ``build`` does and, when
the build has no problems, pastes the blueprint into the real game without a
window (the companion mod's ``tools/headless-run.ps1 -Blueprint``, which runs
``/forge-verify`` on a scratch surface and writes ``verify.json``). The quick
checks use a model of the game; the game is the judge.

* everything placed      -> ``<name>.txt`` is written, exit code 0;
* the game refused some  -> ``<name>.unverified.txt`` is written instead, exit
  code 1, and ``<name>.game-refused-test.py`` holds the case as a test stub, so
  that the model learns what the game just taught;
* no way to ask the game at all (not Windows, no PowerShell, no Factorio, no
  companion set up) -> delivered as ``build`` would, with the line
  ``not checked in the game: <reason>``;
* a check that was set up but could not run (the game is open, the save or the
  runner is missing, the run hung or died) -> withheld like a refusal, but
  without a test stub.

Everything the game sends back (entity names above all, mods choose them) is
data. It is validated, shown through ``repr`` and put into code only as a
Python literal.

Where the companion lives is not guessed. ``FACTORIO_FORGE_COMPANION`` (or the
``companion`` key of ``config.json`` in the forge home) names its repository
folder, or a built ``.zip`` of the mod; with a zip, the runner script comes from
``FACTORIO_FORGE_RUNNER`` (or the ``runner`` key). A repository folder is copied
into a temporary one and built there with its own ``tools/build.ps1``.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ENV_COMPANION = "FACTORIO_FORGE_COMPANION"
ENV_RUNNER = "FACTORIO_FORGE_RUNNER"
RUNNER_RELATIVE = Path("tools") / "headless-run.ps1"
DEFAULT_TIMEOUT_S = 300
# The runner waits for the game to start and quit on its own; this is the slack
# for the process around it.
_SLACK_S = 60
# How many kinds of one list are shown in the verdict.
_SHOWN = 12
# Building the companion zip from its own script.
BUILD_TIMEOUT_S = 120
# /forge-verify over RCON without a player needs this companion or newer.
MIN_COMPANION = (0, 7, 1)


class NotChecked(Exception):
    """The game cannot be asked on this machine; the blueprint is delivered unchecked."""


class CheckFailed(Exception):
    """The check was set up but cannot run; the blueprint is withheld."""


# --------------------------------------------------------------------------
# text from outside is data
# --------------------------------------------------------------------------


def safe(text: object, limit: int = 200) -> str:
    """One printable line out of anything: control characters and newlines are escaped."""
    shown = "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(text))
    return shown if len(shown) <= limit else shown[: limit - 1] + "..."


def _counts(value: object) -> dict[str, int]:
    """A name-to-count table from the game's JSON; anything else is empty.

    The Lua side writes an empty table as ``[]``, so a list means "none".
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for name, count in value.items():
        if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool) and count > 0:
            out[name] = count
    return out


@dataclass
class Verdict:
    """What the game said about one blueprint."""

    status: str  # "verified" | "refused" | "not-checked" | "failed"
    reason: str = ""
    placed: int | None = None
    expected: int | None = None
    missing: dict[str, int] = field(default_factory=dict)
    refused: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def delivered(self) -> bool:
        return self.status in ("verified", "not-checked")

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "placed": self.placed,
                "expected": self.expected, "missing": self.missing, "refused": self.refused,
                "notes": self.notes}


def judge(report: object, entities: int | None = None) -> Verdict:
    """The verdict for a parsed ``verify.json``.

    Verified only when the report is well-formed, everything forge put in the
    blueprint was placed, and the game named nothing as missing or refused. A
    report that cannot be read is a failed run, never a pass.
    """
    if not isinstance(report, dict):
        return Verdict("failed", "verify.json is not an object")
    placed, expected = report.get("placed"), report.get("expected")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (placed, expected)):
        return Verdict("failed", "verify.json has no usable counts")
    missing, refused = _counts(report.get("missing")), _counts(report.get("refused"))
    verdict = Verdict("verified", placed=placed, expected=expected, missing=missing, refused=refused)
    unknown = entities is not None and expected != entities
    if unknown:
        # An entity the game does not know is dropped when it reads the string,
        # so it never even shows up as missing: only the count gives it away.
        verdict.notes.append(f"the game read {expected} entities from the string, forge wrote {entities}")
    if missing or refused or placed != expected or unknown:
        verdict.status = "refused"
        verdict.reason = "the game did not place everything"
    return verdict


def describe(verdict: Verdict) -> list[str]:
    """The lines printed for a verdict. Game-chosen names go through ``repr``."""
    if verdict.status == "verified":
        return [f"checked in the game: all {verdict.placed} of {verdict.expected} entities placed"]
    if verdict.status == "refused":
        lines = [f"REFUSED by the game: {verdict.placed} of {verdict.expected} entities placed"]
        for heading, table in (("not in this game at all", verdict.missing),
                               ("would not stand where forge put them", verdict.refused)):
            if table:
                items = sorted(table.items())
                shown = ", ".join(f"{safe(repr(name))} x{count}" for name, count in items[:_SHOWN])
                more = f" and {len(items) - _SHOWN} more kinds" if len(items) > _SHOWN else ""
                lines.append(f"  {heading}: {shown}{more}")
        if not verdict.missing and not verdict.refused:
            lines.append("  the game named no entity; compare the counts below")
        return lines + [f"  note: {safe(n)}" for n in verdict.notes]
    if verdict.status == "failed":
        return [f"the game check failed (not delivered): {safe(verdict.reason, 500)}"]
    return [f"not checked in the game: {safe(verdict.reason, 500)}"]


# --------------------------------------------------------------------------
# the failing case as a test
# --------------------------------------------------------------------------


def _literal(value: object) -> str:
    """A Python literal for JSON-shaped data, built from ``repr`` alone."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_literal(k)}: {_literal(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    return repr(str(value))


def _strings(value: object) -> set[str]:
    """Every string anywhere inside JSON-shaped data."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {s for k, v in value.items() for s in _strings(k) | _strings(v)}
    if isinstance(value, (list, tuple)):
        return {s for v in value for s in _strings(v)}
    return set()


def needed_names(plan: dict, verdict: Verdict) -> tuple[list[str], list[str]]:
    """The entity and recipe names of this plan that the active data knows.

    The stub skips itself where the data lacks one of them (other mods, other
    profile), the way the tests that read recorded game runs do.
    """
    try:
        from draftsman.data import entities as entity_data
        from draftsman.data import recipes as recipe_data
    except ImportError:
        return [], []
    words = _strings(plan) | set(verdict.refused)
    return (sorted(w for w in words if w in entity_data.raw),
            sorted(w for w in words if w in recipe_data.raw))


def test_stub(plan: dict, verdict: Verdict, planned: dict | None = None) -> str:
    """Python source of a test that turns the game's refusal into a regression.

    The test builds the same plan and expects the model to see a problem too.
    It is marked as an expected failure (strict), so the suite stays green until
    the model learns the rule and then flags the mark for removal; it skips
    itself where the active data lacks a name the plan uses. The plan and the
    names the game gave are written with ``repr`` only: no game-chosen text
    reaches a comment, an identifier or a string by concatenation.
    """
    entities, recipes = needed_names(plan, verdict)
    return (
        "# The game refused this blueprint although forge's own checks passed.\n"
        "# Teach the model what the game knows, then remove the xfail mark.\n"
        "import pytest\n"
        "from draftsman.data import entities as entity_data\n"
        "from draftsman.data import recipes as recipe_data\n"
        "\n"
        "from factorio_forge import plan as plan_module\n"
        "\n"
        f"PLAN = {_literal(plan)}\n"
        f"PLANNED = {_literal(planned)}\n"
        f"GAME_MISSING = {_literal(dict(sorted(verdict.missing.items())))}\n"
        f"GAME_REFUSED = {_literal(dict(sorted(verdict.refused.items())))}\n"
        f"NEEDS_ENTITIES = {_literal(entities)}\n"
        f"NEEDS_RECIPES = {_literal(recipes)}\n"
        "\n"
        "_absent = (sorted(set(NEEDS_ENTITIES) - set(entity_data.raw))\n"
        "           + sorted(set(NEEDS_RECIPES) - set(recipe_data.raw)))\n"
        "if _absent:\n"
        "    pytest.skip('the active game data does not know ' + repr(_absent[0]), allow_module_level=True)\n"
        "\n"
        "\n"
        "@pytest.mark.xfail(strict=True, raises=AssertionError,\n"
        "                   reason='the model does not know this rule yet; remove this mark once it is taught')\n"
        "def test_the_model_sees_what_the_game_refused():\n"
        "    result = plan_module.build(PLAN, PLANNED or None)\n"
        "    assert result.problems, (\n"
        "        'the game refused part of this blueprint, but the model finds no problem: '\n"
        "        + repr((GAME_MISSING, GAME_REFUSED))\n"
        "    )\n"
    )


# pytest would collect this helper as a test because of its name.
test_stub.__test__ = False  # type: ignore[attr-defined]


def stub_reads_back(source: str, plan: dict, verdict: Verdict, planned: dict | None = None) -> bool:
    """True when the stub parses and its literals read back as what was meant."""
    try:
        tree = ast.parse(source)
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    found[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    pass  # `_absent = ...` is code, not a literal
    except SyntaxError:
        return False
    entities, recipes = needed_names(plan, verdict)
    return (found.get("PLAN") == json.loads(json.dumps(plan))
            and found.get("PLANNED") == (json.loads(json.dumps(planned)) if planned is not None else None)
            and found.get("GAME_MISSING") == dict(verdict.missing)
            and found.get("GAME_REFUSED") == dict(verdict.refused)
            and found.get("NEEDS_ENTITIES") == entities
            and found.get("NEEDS_RECIPES") == recipes
            and "pytest.mark.xfail(strict=True" in source
            and "allow_module_level=True" in source)


# --------------------------------------------------------------------------
# running a process, and ending all of it
# --------------------------------------------------------------------------


def system_tool(*parts: str) -> Path | None:
    """A Windows tool by its full path under %SystemRoot%, never looked up on PATH.

    Looking a bare name up would run whatever file of that name sits in the
    current folder or early on PATH; the system folder is the only one trusted.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    tool = Path(root, *parts)
    return tool if tool.is_file() else None


def kill_tree(pid: int) -> None:
    """End a process and everything it started (the game is a child of the runner)."""
    taskkill = system_tool("System32", "taskkill.exe")
    if taskkill is None:
        return
    try:
        subprocess.run([str(taskkill), "/T", "/F", "/PID", str(int(pid))], capture_output=True,
                       check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def run_tree(cmd: list[str], *, timeout: float, **_ignored) -> subprocess.CompletedProcess:
    """Run an argument list; on a timeout the whole process tree is ended, then it raises.

    ``subprocess.run`` would end only the shell, leaving the game server it
    started running (and the runner's own clean-up unrun).
    """
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            proc.kill()
            proc.communicate(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _tail(done: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (done.stderr or done.stdout or "").splitlines() if ln.strip()][-3:]
    return ": " + safe(" | ".join(lines), 400) if lines else ""


# --------------------------------------------------------------------------
# finding the runner, the mod and the save
# --------------------------------------------------------------------------


def _version(text: object) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def _check_info(raw: bytes | str, where: str) -> tuple[str, str]:
    """Name and version out of a companion's info.json; CheckFailed when odd or too old."""
    try:
        info = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
        name, version = str(info["name"]), str(info["version"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CheckFailed(f"the companion's info.json in {safe(where)} cannot be read ({safe(exc)})") from exc
    parsed = _version(version)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or parsed is None:
        raise CheckFailed(f"the companion's info.json in {safe(where)} has an odd name or version")
    if parsed < MIN_COMPANION:
        need = ".".join(map(str, MIN_COMPANION))
        raise CheckFailed(f"the companion is version {version}; /forge-verify needs {need} or newer")
    return name, version


def build_companion(source: Path, work: Path, shell: str, run) -> Path:
    """The companion's mod zip, made by a copy of its own tools/build.ps1.

    The repository is copied into `work` first, so the script's ``dist/`` lands
    there and the repository is never written to; the file list stays the
    companion's own.
    """
    try:
        name, version = _check_info((source / "info.json").read_bytes(), str(source))
    except OSError as exc:
        raise CheckFailed(f"the companion folder has no readable info.json ({safe(exc)})") from exc
    if not (source / "tools" / "build.ps1").is_file():
        raise CheckFailed(f"the companion folder {safe(source)} has no tools/build.ps1")
    copy = work / "companion"
    try:
        shutil.copytree(source, copy, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "dist", "node_modules", "__pycache__"))
    except (OSError, shutil.Error) as exc:
        raise CheckFailed(f"the companion folder could not be copied ({safe(exc)})") from exc
    cmd = [shell, "-NoProfile", "-NonInteractive", "-File", str(copy / "tools" / "build.ps1")]
    try:
        done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=BUILD_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CheckFailed(f"building the companion took longer than {BUILD_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise CheckFailed(f"the companion's build script could not be started ({safe(exc)})") from exc
    archive = copy / "dist" / f"{name}_{version}.zip"
    if done.returncode != 0 or not archive.is_file():
        raise CheckFailed(f"building the companion failed (code {done.returncode}){_tail(done)}")
    return archive


def _zip_info(archive: Path) -> None:
    """A built companion zip must be a mod of an accepted version."""
    try:
        with zipfile.ZipFile(archive) as z:
            hits = [n for n in z.namelist() if re.fullmatch(r"[^/]+/info\.json", n)]
            if len(hits) != 1:
                raise CheckFailed(f"{safe(archive)} does not hold one <mod>/info.json")
            raw = z.read(hits[0])
    except (OSError, zipfile.BadZipFile) as exc:
        raise CheckFailed(f"{safe(archive)} cannot be read as a zip ({safe(exc)})") from exc
    _check_info(raw, str(archive))


@dataclass
class Setup:
    shell: str
    runner: Path
    mod_zip: Path
    save: Path
    factorio: Path
    data_dir: Path | None


def _setting(env: str, key: str) -> str | None:
    value = os.environ.get(env)
    if value:
        return value
    from . import paths

    stored = paths.load_config().get(key)
    return stored if isinstance(stored, str) and stored else None


def locate(profile_save: str | None, *, save: str | None = None, companion: str | None = None,
           work: Path, run=run_tree) -> Setup:
    """Everything the check needs.

    NotChecked when this machine cannot ask the game at all (not Windows, no
    PowerShell, no Factorio, no companion named); CheckFailed when the player
    set it up but something named is wrong or missing.
    """
    from . import paths

    if not sys.platform.startswith("win"):
        raise NotChecked("the in-game check runs through PowerShell on Windows only")
    shell = system_tool("System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if shell is None:
        raise NotChecked("Windows PowerShell was not found in the system folder")
    shell = str(shell)
    binary = paths.factorio_binary()
    if binary is None:
        raise NotChecked("Factorio is not installed here, or its folder was not found")
    where = companion or _setting(ENV_COMPANION, "companion")
    if not where:
        raise NotChecked(f"no companion mod is set up (set {ENV_COMPANION} to its folder or a built zip; see docs/checking.md)")

    where_path = Path(where).expanduser()
    runner_text = _setting(ENV_RUNNER, "runner")
    if where_path.is_dir():
        runner = Path(runner_text).expanduser() if runner_text else where_path / RUNNER_RELATIVE
    elif where_path.is_file() and where_path.suffix.lower() == ".zip":
        if not runner_text:
            raise CheckFailed(f"{ENV_COMPANION} is a zip; {ENV_RUNNER} must name headless-run.ps1")
        runner = Path(runner_text).expanduser()
    else:
        raise CheckFailed(f"{ENV_COMPANION} names neither a folder nor a .zip that exists")
    if not runner.is_file():
        raise CheckFailed(f"the runner script was not found at {safe(runner)}")
    if where_path.is_dir():
        mod_zip = build_companion(where_path, work, shell, run)
    else:
        _zip_info(where_path)
        mod_zip = where_path

    chosen = save or profile_save
    if not chosen:
        raise CheckFailed("the active profile does not name its save; pass --save")
    candidate = Path(chosen).expanduser()
    if not candidate.is_file():
        saves = paths.saves_dir()
        name = chosen if chosen.lower().endswith(".zip") else chosen + ".zip"
        candidate = (saves / name) if saves else Path(name)
    if not candidate.is_file():
        raise CheckFailed(f"the save {safe(chosen)} was not found")
    return Setup(shell, runner, mod_zip, candidate, binary, paths.factorio_user_dir())


def command(setup: Setup, blueprint_file: Path, out: Path, timeout_s: int) -> list[str]:
    """The runner's command line, as an argument list (never a shell string)."""
    cmd = [setup.shell, "-NoProfile", "-NonInteractive", "-File", str(setup.runner),
           "-Save", str(setup.save), "-Out", str(out), "-Blueprint", str(blueprint_file),
           "-ModZip", str(setup.mod_zip), "-Factorio", str(setup.factorio),
           "-ForgePython", sys.executable, "-TimeoutSec", str(timeout_s)]
    if setup.data_dir is not None:
        cmd += ["-DataDir", str(setup.data_dir)]
    return cmd


def check_in_game(blueprint: str, *, entities: int | None, profile_save: str | None,
                  save: str | None = None, companion: str | None = None,
                  timeout_s: int = DEFAULT_TIMEOUT_S, run=run_tree) -> Verdict:
    """Ask the game whether `blueprint` places. Never raises for a missing setup."""
    with tempfile.TemporaryDirectory(prefix="forge-deliver-") as tmp:
        work = Path(tmp)
        try:
            setup = locate(profile_save, save=save, companion=companion, work=work, run=run)
        except NotChecked as exc:
            return Verdict("not-checked", str(exc))
        except CheckFailed as exc:
            return Verdict("failed", str(exc))
        out = work / "out"
        out.mkdir()
        bp = work / "blueprint.txt"
        bp.write_text(blueprint, encoding="utf-8")
        try:
            done = run(command(setup, bp, out, timeout_s), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout_s + _SLACK_S, check=False)
        except subprocess.TimeoutExpired:
            return Verdict("failed", f"the game did not answer within {timeout_s + _SLACK_S} s; the process tree was ended")
        except OSError as exc:
            return Verdict("failed", f"the runner could not be started ({safe(exc)})")
        if done.returncode == 2:
            return Verdict("failed", "Factorio is already running; close it and deliver again")
        result = out / "verify.json"
        if done.returncode != 0 or not result.is_file():
            return Verdict("failed", f"the runner stopped with code {done.returncode}{_tail(done)}")
        try:
            report = json.loads(result.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return Verdict("failed", f"verify.json could not be read ({safe(exc)})")
        return judge(report, entities)


# --------------------------------------------------------------------------
# the command
# --------------------------------------------------------------------------


def _profile_save() -> str | None:
    try:
        from .profile import Profile

        name = Profile.active_profile_name()
        return Profile.load(name).source_save if name else None
    except Exception:  # noqa: BLE001 - no profile means "ask for --save"
        return None


def deliver(args: argparse.Namespace, check=None) -> int:
    """Build a plan; write the blueprint only if the game placed all of it."""
    from . import plan as plan_module
    from . import render

    plan_path = Path(args.plan)
    try:
        data = plan_module.load(plan_path)
        planned, warned = None, []
        if data.get("request"):
            planned, warned = plan_module.planned_from_request(data, plan_path)
        result = plan_module.build(data, planned)
        result.warnings.extend(warned)
    except plan_module.PlanError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {plan_path}: {exc.strerror or exc}", file=sys.stderr)
        return 2

    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", result.label).strip("-") or "layout"
    out = Path(args.output) if args.output else plan_path.resolve().parent
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: {out}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    entities = len(result.blueprint.entities)
    print(f"{result.label}: {entities} entities, {result.power_networks} power network(s)")
    for warning in warned:
        print(f"warning: {safe(warning, 400)}")

    failed_routes = [r for r in result.routes if not r.ok]
    problems = result.problems
    if problems or failed_routes:
        for finding in problems:
            print(f"problem: {safe(finding.summary, 400)}")
        for r in failed_routes:
            print(f"problem: route {safe(r.id)} failed: {safe(r.reason, 400)}")
        print("not delivered: fix the problems above first (the game was not asked)")
        return 1

    blueprint = result.blueprint.to_string()
    if args.no_game_check:
        verdict = Verdict("not-checked", "skipped with --no-game-check")
    else:
        verdict = (check or check_in_game)(blueprint, entities=entities, profile_save=_profile_save(), save=args.save,
                        companion=args.companion, timeout_s=args.timeout)
    for line in describe(verdict):
        print(line)

    name = f"{slug}.txt" if verdict.delivered else f"{slug}.unverified.txt"
    blueprint_path = out / name
    blueprint_path.write_text(blueprint, encoding="utf-8")
    if verdict.delivered:
        (out / f"{slug}.unverified.txt").unlink(missing_ok=True)  # this tool's own earlier refusal; stale now
    else:
        earlier = out / f"{slug}.txt"
        if earlier.exists():
            # Never leave a blueprint of an older plan beside the refusal of this one.
            stale = out / f"{slug}.stale.txt"
            os.replace(earlier, stale)
            print(f"note: the earlier {earlier.name} was from another build; moved to {stale.name}")
    report = result.to_dict()
    report["game_check"] = verdict.to_dict()
    report_path = out / f"{slug}.report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.no_render:
        render.write_html(result.blueprint, out / f"{slug}.html", title=result.label, findings=result.findings)

    if verdict.status == "refused":
        stub = test_stub(data, verdict, planned)
        if stub_reads_back(stub, data, verdict, planned):
            stub_path = out / f"{slug}.game-refused-test.py"
            stub_path.write_text(stub, encoding="utf-8")
            print(f"test stub: {stub_path.resolve()} (copy it into tests/ and teach the model what the game refused)")
        else:
            print("test stub: not written, it did not read back as the case it describes")
    print(f"{'blueprint' if verdict.delivered else 'NOT DELIVERED, withheld as'}: {blueprint_path.resolve()}")
    print(f"report:    {report_path.resolve()}")
    return 0 if verdict.delivered else 1


def register(sub) -> None:
    """Add the `deliver` command to the CLI's subparsers."""
    cmd = sub.add_parser("deliver", help="build a plan and hand the blueprint over only if the game places all of it")
    cmd.add_argument("plan", help="a plan JSON file (see factorio_forge/plan.py)")
    cmd.add_argument("-o", "--output", help="directory to write into (default: beside the plan)")
    cmd.add_argument("--no-render", action="store_true")
    cmd.add_argument("--save", help="the save to run on (default: the active profile's own)")
    cmd.add_argument("--companion", help=f"the companion mod's folder or built zip (default: ${ENV_COMPANION})")
    cmd.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="seconds the game may take (default 300)")
    cmd.add_argument("--no-game-check", action="store_true", help="deliver without asking the game, and say so")
    cmd.set_defaults(func=deliver)
