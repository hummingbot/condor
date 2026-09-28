"""An agent's own Hummingbot controller source — the folder model (FEAT-126).

An agent can carry the controllers it trades with inside its own folder::

    agents/<slug>/controllers/<name>/
        <name>.py                 # the controller source (single file)
        CONTROLLER.md             # optional: description, `type:` override
        sample_configs/<style>.yml

and ``agents/_shared/controllers/`` holds the same shape, read by every agent
*under* its own library (its own shadows a shared one by name) — the routines
rule (:func:`routines.base.assistant_routines`) applied to a new kind of file.

This module is **pure filesystem**: discovery across the stock/local layers,
type inference, source normalisation, sample validation and the local writes.
Everything that talks to a Hummingbot API server lives in
:mod:`condor.agent_controllers_sync`, which runs in the main process; this one
also runs inside the Condor MCP subprocess (``list``/``read``/``write``/
``delete``), which holds no API client.

The filesystem is read on every call. An agent owns a handful of small files,
so there is no cache and therefore no invalidation bug.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

CONTROLLERS_DIRNAME = "controllers"
SAMPLES_DIRNAME = "sample_configs"
CONTROLLER_MD = "CONTROLLER.md"
#: Where ``sync(overwrite=True)`` keeps the server copy it replaced. Excluded
#: from :func:`condor.layering.publishable_files`, so it never leaves the install.
BACKUPS_DIRNAME = ".server_backups"

CONTROLLER_TYPES: tuple[str, ...] = ("directional_trading", "market_making", "generic")

#: A sample named ``config`` would be dropped on publish: every path part named
#: ``config.yml`` is a runtime file to :func:`condor.layering.publishable_files`.
RESERVED_SAMPLE = "config"

SHARED_ORIGIN = "shared"

# A controller name is the Python module name every saved config references and
# the API resolves, so it is a bare identifier — never a path. ``\Z`` rather
# than ``$`` so a trailing newline is refused too.
_CONTROLLER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
# A sample stem / saved-config id: one segment, no dots, no separators.
_SAMPLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")

# Base class → controller type. The specific bases win over ``ControllerBase``,
# which both of them extend.
_BASE_TYPES = {
    "DirectionalTradingControllerBase": "directional_trading",
    "MarketMakingControllerBase": "market_making",
}
_GENERIC_BASE = "ControllerBase"

_TYPE_HINT = (
    "add `type: directional_trading | market_making | generic` to its CONTROLLER.md"
)


class ControllerError(ValueError):
    """A controller or sample that cannot be used as asked — the message says why."""


@dataclass(frozen=True)
class ControllerSource:
    """One controller an agent can see, resolved across the layers."""

    #: Folder name == module name == ``controller_name`` in every config.
    name: str
    #: ``None`` when neither CONTROLLER.md nor the source says; see ``type_error``.
    controller_type: str | None
    source_path: Path
    #: :func:`source_digest` of the normalised source.
    digest: str
    description: str
    #: sample stem -> yml path, sorted by stem.
    samples: dict[str, Path] = field(default_factory=dict)
    #: ``"agent:<slug>"`` or ``"shared"``.
    origin: str = ""
    #: Lives in the stock layer — a write forks it down first.
    stock: bool = False

    @property
    def shared(self) -> bool:
        return self.origin == SHARED_ORIGIN

    @property
    def type_error(self) -> str:
        """Why the type is unknown, or ``""``."""
        if self.controller_type:
            return ""
        return (
            f"Controller '{self.name}': its type cannot be inferred from the "
            f"source — {_TYPE_HINT}."
        )

    @property
    def directory(self) -> Path:
        return self.source_path.parent

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape the MCP tool and the web routes return."""
        out: dict[str, Any] = {
            "name": self.name,
            "controller_type": self.controller_type,
            "description": self.description,
            "styles": list(self.samples),
            "origin": self.origin,
            "shared": self.shared,
            "stock": self.stock,
            "digest": self.digest,
        }
        if self.type_error:
            out["type_error"] = self.type_error
        return out


# ── names ──


def check_controller_name(name: str | None) -> str:
    """``name`` if it is a valid controller name, else :class:`ControllerError`."""
    if not name or not _CONTROLLER_NAME.match(str(name)):
        raise ControllerError(
            "controller name must be a Python identifier (letters, digits, "
            "underscores; e.g. 'pmm_king')"
        )
    return str(name)


def check_sample_name(sample: str | None) -> str:
    """``sample`` if it is a usable style name, else :class:`ControllerError`."""
    if not sample or not _SAMPLE_NAME.match(str(sample)):
        raise ControllerError(
            "sample name must be letters, digits, '_' or '-' (e.g. 'aggressive')"
        )
    if str(sample) == RESERVED_SAMPLE:
        raise ControllerError(
            f"a sample cannot be named '{RESERVED_SAMPLE}': files named "
            f"'{RESERVED_SAMPLE}.yml' are dropped when an agent is published — "
            "pick another name (e.g. 'default')"
        )
    return str(sample)


def check_config_name(config_name: str | None) -> str:
    """A saved-config id on the server: same alphabet as a sample stem."""
    if not config_name or not _SAMPLE_NAME.match(str(config_name)):
        raise ControllerError(
            "config_name must be letters, digits, '_' or '-' (e.g. 'pmm_king__aggressive')"
        )
    return str(config_name)


def default_config_name(controller: str, sample: str) -> str:
    """The server id a sample is uploaded under: ``{controller}__{sample}``.

    Namespaced on purpose: config ids are free-form and global on a server, so
    two agents' ``aggressive`` would otherwise collide.
    """
    return f"{controller}__{sample}"


# ── source ──


def normalise_source(text: str) -> str:
    """Line endings to ``\\n``, trailing whitespace at EOF trimmed, one final newline.

    What the digest is computed over, so a CRLF checkout or an editor's extra
    blank line is never reported as drift.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.rstrip() + "\n"


def source_digest(text: str) -> str:
    """``sha256:<12>`` of the normalised source — the :func:`condor.layering.content_digest` format."""
    digest = hashlib.sha256(normalise_source(text).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):  # Base[Config]
        return _base_name(node.value)
    return ""


def infer_type(source: str) -> str | None:
    """The controller type from the base classes in ``source``, or ``None``.

    ``DirectionalTradingControllerBase`` → ``directional_trading``,
    ``MarketMakingControllerBase`` → ``market_making``, ``ControllerBase`` →
    ``generic``. A module that extends both specific bases, or none of the
    three, or does not parse, is unresolvable — ``CONTROLLER.md`` says instead.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    specific: set[str] = set()
    generic = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = _base_name(base)
            if name in _BASE_TYPES:
                specific.add(_BASE_TYPES[name])
            elif name == _GENERIC_BASE:
                generic = True
    if len(specific) == 1:
        return specific.pop()
    if not specific and generic:
        return "generic"
    return None


def _docstring_line(source: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(source)) or ""
    except SyntaxError:
        return ""
    return next((line.strip() for line in doc.splitlines() if line.strip()), "")


def _read_meta(directory: Path) -> dict:
    path = directory / CONTROLLER_MD
    if not path.is_file():
        return {}
    from condor.frontmatter import parse_frontmatter

    try:
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return meta if isinstance(meta, dict) else {}


# ── discovery ──


def controller_dirs(agent_slug: str | None) -> tuple[Path, Path]:
    """``(local, stock)`` ``controllers/`` of an agent — read order."""
    from condor.memory.paths import agent_home_layers

    local, stock = agent_home_layers(agent_slug)
    return local / CONTROLLERS_DIRNAME, stock / CONTROLLERS_DIRNAME


def _load_one(directory: Path, origin: str, stock: bool) -> ControllerSource | None:
    name = directory.name
    if name.startswith("_") or not _CONTROLLER_NAME.match(name):
        return None
    source_path = directory / f"{name}.py"
    if not source_path.is_file():
        return None
    try:
        text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        log.warning("Could not read controller source %s", source_path)
        return None

    meta = _read_meta(directory)
    declared = meta.get("type")
    controller_type = declared if declared in CONTROLLER_TYPES else infer_type(text)
    description = str(meta.get("description") or "").strip() or _docstring_line(text)

    samples: dict[str, Path] = {}
    samples_dir = directory / SAMPLES_DIRNAME
    if samples_dir.is_dir():
        for path in sorted(samples_dir.iterdir()):
            if path.suffix not in (".yml", ".yaml") or not path.is_file():
                continue
            stem = path.stem
            if stem == RESERVED_SAMPLE or not _SAMPLE_NAME.match(stem):
                continue
            samples.setdefault(stem, path)

    return ControllerSource(
        name=name,
        controller_type=controller_type,
        source_path=source_path,
        digest=source_digest(text),
        description=description,
        samples=dict(sorted(samples.items())),
        origin=origin,
        stock=stock,
    )


def _scan(root: Path, origin: str, stock: bool) -> dict[str, ControllerSource]:
    found: dict[str, ControllerSource] = {}
    if not root.is_dir():
        return found
    for directory in sorted(root.iterdir()):
        if directory.is_dir():
            src = _load_one(directory, origin, stock)
            if src is not None:
                found[src.name] = src
    return found


def shared_controllers() -> dict[str, ControllerSource]:
    """The ``_shared`` library alone, local over stock — what every agent inherits."""
    from condor.memory.paths import shared_controller_roots

    shared_local, shared_stock = shared_controller_roots()
    merged: dict[str, ControllerSource] = {}
    merged.update(_scan(shared_stock, SHARED_ORIGIN, stock=True))
    merged.update(_scan(shared_local, SHARED_ORIGIN, stock=False))
    return merged


def agent_controllers(agent_slug: str | None) -> dict[str, ControllerSource]:
    """Every controller an agent can see: its OWN library over the SHARED one.

    Merge order, later winning by name: shared-stock, shared-local, own-stock,
    own-local — the routines merge. A falsy slug is Condor, like every other
    per-agent resolver.
    """
    from condor.memory.paths import CHAT_SLUG

    slug = agent_slug or CHAT_SLUG
    own_local, own_stock = controller_dirs(slug)
    origin = f"agent:{slug}"

    merged = shared_controllers()
    merged.update(_scan(own_stock, origin, stock=True))
    merged.update(_scan(own_local, origin, stock=False))
    return dict(sorted(merged.items()))


def get_controller(agent_slug: str | None, name: str) -> ControllerSource | None:
    """The controller ``name`` as ``agent_slug`` sees it, or ``None``."""
    try:
        check_controller_name(name)
    except ControllerError:
        return None
    return agent_controllers(agent_slug).get(name)


def read_source(src: ControllerSource) -> str:
    """The controller's source, normalised — exactly what ``sync`` uploads."""
    return normalise_source(src.source_path.read_text(encoding="utf-8"))


def controllers_section(agent_slug: str | None) -> str:
    """The CONTROLLERS index: controller source this agent owns (FEAT-126).

    Empty when the agent owns none and no shared one exists, so every other
    agent pays nothing for it. Shared entries are marked, like shared routines,
    because the agent can read but not edit them. Read fresh each call: a
    handful of small files, and the agent may ``write`` one mid-session.

    Lives here, not in :mod:`condor.agents.prompts`, because the tick prompt and
    :func:`condor.memory.context.domain_context` (delegated and bound runs) both
    emit it, and the latter must not import the prompt builder.
    """
    available = agent_controllers(agent_slug)
    if not available:
        return ""
    lines = [
        "CONTROLLERS — Hummingbot controller source you own (push with "
        "manage_agent_controllers):"
    ]
    for name, src in available.items():
        kind = src.controller_type or "type unknown"
        if src.shared:
            kind += ", shared"
        line = f"  - {name} ({kind})"
        if src.description:
            line += f": {src.description}"
        if src.samples:
            line += " · styles: " + ", ".join(src.samples)
        lines.append(line)
    lines.append(
        'Before deploying or backtesting one: manage_agent_controllers(action="status", '
        "name=…); sync if missing. Read the controller_sources skill before your first sync."
    )
    return "\n".join(lines)


# ── samples ──


def validate_sample(data: Any, controller: str, controller_type: str | None) -> dict:
    """A sample config checked against its controller, with the ids filled in.

    Must be a mapping; a ``controller_name`` must equal the folder name and a
    ``controller_type`` must match the controller's. Missing ones are filled.
    """
    if not isinstance(data, dict):
        raise ControllerError("a sample config must be a YAML mapping of fields")
    out = dict(data)
    declared_name = out.get("controller_name")
    if declared_name is not None and declared_name != controller:
        raise ControllerError(
            f"controller_name is '{declared_name}' but the controller is "
            f"'{controller}' — a config must name the folder it lives in"
        )
    declared_type = out.get("controller_type")
    if (
        declared_type is not None
        and controller_type
        and str(declared_type) != controller_type
    ):
        raise ControllerError(
            f"controller_type is '{declared_type}' but '{controller}' is a "
            f"{controller_type} controller"
        )
    out["controller_name"] = controller
    if controller_type and declared_type is None:
        out["controller_type"] = controller_type
    return out


def load_sample(src: ControllerSource, sample: str) -> dict:
    """``yaml.safe_load`` a style of ``src`` and :func:`validate_sample` it."""
    import yaml

    check_sample_name(sample)
    path = src.samples.get(sample)
    if path is None:
        styles = ", ".join(src.samples) or "none"
        raise ControllerError(
            f"'{src.name}' has no sample '{sample}' (styles: {styles})"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ControllerError(f"sample '{sample}' could not be read: {exc}") from exc
    return validate_sample(data, src.name, src.controller_type)


def read_samples(src: ControllerSource) -> dict[str, str]:
    """Every style's raw YAML text, by stem."""
    out: dict[str, str] = {}
    for stem, path in src.samples.items():
        try:
            out[stem] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


# ── writes (always to the local layer) ──


def library_roots(agent_slug: str | None, shared: bool = False) -> tuple[Path, Path]:
    """``(local, stock)`` controllers library a write targets."""
    if shared:
        from condor.memory.paths import shared_controller_roots

        return shared_controller_roots()
    return controller_dirs(agent_slug)


def owns(agent_slug: str | None, name: str, shared: bool = False) -> bool:
    """Whether ``name`` exists in the library a write would target (either layer)."""
    local, stock = library_roots(agent_slug, shared)
    return (local / name / f"{name}.py").is_file() or (
        stock / name / f"{name}.py"
    ).is_file()


def _writable_dir(agent_slug: str | None, name: str, shared: bool) -> Path:
    """The local folder of ``name``, forking a stock folder down first."""
    from condor.layering import fork_path

    local, stock = library_roots(agent_slug, shared)
    label = f"{'_shared' if shared else agent_slug}/{CONTROLLERS_DIRNAME}/{name}"
    return fork_path(local / name, stock / name, label=label)


def write_source(
    agent_slug: str | None, name: str, code: str, shared: bool = False
) -> dict:
    """Save a controller's source, validated first. Forks a stock folder down."""
    check_controller_name(name)
    if not code or not code.strip():
        raise ControllerError("code is required")
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise ControllerError(f"the controller does not parse: {exc}") from exc

    local, stock = library_roots(agent_slug, shared)
    existing_meta = _read_meta(local / name) or _read_meta(stock / name)
    declared = existing_meta.get("type")
    controller_type = declared if declared in CONTROLLER_TYPES else infer_type(code)
    if controller_type is None:
        raise ControllerError(
            f"cannot tell what kind of controller '{name}' is from its base "
            f"classes — {_TYPE_HINT} (write that file first, then the source)"
        )

    forked = not (local / name).exists() and (stock / name).exists()
    directory = _writable_dir(agent_slug, name, shared)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    text = normalise_source(code)
    atomic_write_text(path, text)
    return {
        "written": True,
        "name": name,
        "controller_type": controller_type,
        "digest": source_digest(text),
        "path": str(path),
        "forked_from_stock": forked,
        "shared": shared,
    }


def write_controller_md(
    agent_slug: str | None, name: str, text: str, shared: bool = False
) -> dict:
    """Save ``CONTROLLER.md`` (description, ``type:`` override, style notes)."""
    check_controller_name(name)
    from condor.frontmatter import parse_frontmatter

    meta, _ = parse_frontmatter(text or "")
    declared = meta.get("type") if isinstance(meta, dict) else None
    if declared is not None and declared not in CONTROLLER_TYPES:
        raise ControllerError(
            f"type must be one of {', '.join(CONTROLLER_TYPES)}, not '{declared}'"
        )
    directory = _writable_dir(agent_slug, name, shared)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONTROLLER_MD
    atomic_write_text(path, text if text.endswith("\n") else text + "\n")
    return {"written": True, "name": name, "path": str(path), "shared": shared}


def write_sample(
    agent_slug: str | None,
    name: str,
    sample: str,
    yaml_text: str,
    shared: bool = False,
) -> dict:
    """Save one style of a controller the target library owns, validated first."""
    import yaml

    check_controller_name(name)
    check_sample_name(sample)
    if not owns(agent_slug, name, shared):
        raise ControllerError(
            f"'{name}' is not in this library — write the controller before its samples"
        )
    try:
        data = yaml.safe_load(yaml_text or "")
    except yaml.YAMLError as exc:
        raise ControllerError(f"the sample is not valid YAML: {exc}") from exc

    local, stock = library_roots(agent_slug, shared)
    source_dir = (
        local / name if (local / name / f"{name}.py").is_file() else stock / name
    )
    src = _load_one(source_dir, "", stock=False)
    validate_sample(data, name, src.controller_type if src else None)

    directory = _writable_dir(agent_slug, name, shared) / SAMPLES_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{sample}.yml"
    atomic_write_text(path, yaml_text if yaml_text.endswith("\n") else yaml_text + "\n")
    return {
        "written": True,
        "name": name,
        "sample": sample,
        "path": str(path),
        "shared": shared,
    }


def delete(
    agent_slug: str | None,
    name: str,
    sample: str | None = None,
    shared: bool = False,
) -> dict:
    """Delete a local controller folder, or one of its samples.

    A shipped item is refused rather than tombstoned — the layering rule. A
    local fork of a shipped one may be deleted, which reverts it to stock.
    """
    from condor.layering import stock_delete_error

    check_controller_name(name)
    local, stock = library_roots(agent_slug, shared)
    if sample is not None:
        check_sample_name(sample)
        rel = Path(name) / SAMPLES_DIRNAME / f"{sample}.yml"
        label = f"{name}/{SAMPLES_DIRNAME}/{sample}.yml"
    else:
        rel = Path(name)
        label = f"controller '{name}'"
    local_path, stock_path = local / rel, stock / rel

    if not local_path.exists():
        if stock_path.exists():
            raise ControllerError(stock_delete_error(label))
        raise ControllerError(f"{label} not found")

    if local_path.is_dir():
        shutil.rmtree(local_path)
    else:
        local_path.unlink()
    return {
        "deleted": True,
        "name": name,
        "sample": sample,
        "reverted_to_stock": stock_path.exists(),
    }


def backup_server_copy(
    agent_slug: str | None, name: str, server_name: str, text: str
) -> Path:
    """Keep a server's copy of ``name`` before it is overwritten.

    ``<local agent home>/controllers/<name>/.server_backups/<server>-<utc ts>.py``
    — a dot directory, so it is never published and never discovered.
    """
    from condor.memory.paths import agent_home

    check_controller_name(name)
    server = re.sub(r"[^A-Za-z0-9_-]", "_", server_name or "server")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = agent_home(agent_slug) / CONTROLLERS_DIRNAME / name / BACKUPS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{server}-{stamp}.py"
    atomic_write_text(path, text)
    return path


def adopt(
    agent_slug: str | None,
    name: str,
    controller_type: str,
    code: str,
    configs: dict[str, dict],
    overwrite: bool = False,
) -> dict:
    """Write a controller fetched from a server into the agent's LOCAL folder.

    The ``pull`` half that touches disk. Refuses to replace a differing
    ``<name>.py`` the agent already owns unless ``overwrite``; a style that
    exists and differs is skipped (and reported) under the same rule.
    ``configs`` is ``{config_id: data}`` as the server stores them; each is
    written as ``sample_configs/<id minus "{name}__">.yml`` without its ``id``
    or ``_``-prefixed keys.
    """
    import yaml

    check_controller_name(name)
    if controller_type not in CONTROLLER_TYPES:
        raise ControllerError(
            f"controller_type must be one of {', '.join(CONTROLLER_TYPES)}"
        )
    text = normalise_source(code)
    existing = get_controller(agent_slug, name)
    if (
        existing is not None
        and not existing.shared
        and existing.digest != source_digest(text)
    ):
        if not overwrite:
            raise ControllerError(
                f"'{name}' already exists in this agent's folder and differs from "
                "the server copy — pass overwrite=true to replace it with the "
                "server's version"
            )

    directory = _writable_dir(agent_slug, name, shared=False)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(directory / f"{name}.py", text)

    wrote_md = False
    if infer_type(text) != controller_type and not _read_meta(directory).get("type"):
        atomic_write_text(
            directory / CONTROLLER_MD,
            f"---\ntype: {controller_type}\n---\n\nPulled from a Hummingbot API server.\n",
        )
        wrote_md = True

    written: list[str] = []
    skipped: dict[str, str] = {}
    prefix = f"{name}__"
    for config_id, data in configs.items():
        stem = config_id[len(prefix) :] if config_id.startswith(prefix) else config_id
        if stem == RESERVED_SAMPLE:
            stem = "default"
        try:
            check_sample_name(stem)
        except ControllerError as exc:
            skipped[config_id] = str(exc)
            continue
        clean = {
            k: v
            for k, v in (data or {}).items()
            if k != "id" and not str(k).startswith("_")
        }
        body = yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)
        path = directory / SAMPLES_DIRNAME / f"{stem}.yml"
        if path.is_file() and not overwrite:
            try:
                same = yaml.safe_load(path.read_text(encoding="utf-8")) == clean
            except (OSError, yaml.YAMLError):
                same = False
            if not same:
                skipped[config_id] = (
                    f"sample '{stem}' exists and differs — pass overwrite=true"
                )
                continue
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, body)
        written.append(stem)

    return {
        "pulled": True,
        "name": name,
        "controller_type": controller_type,
        "digest": source_digest(text),
        "path": str(directory / f"{name}.py"),
        "styles_written": written,
        "styles_skipped": skipped,
        "wrote_controller_md": wrote_md,
    }
