"""What can be updated, how it is versioned, and what stands in the way.

Two components, described as data rather than as branches in a handler: Condor
itself, which is a git checkout, and hummingbot-api, which is a git checkout
*and* a container image. Those two facets answer different questions -- the repo
governs the compose file and the bind-mounted ``bots/``, the image governs the
API's own code -- and conflating them is why ``/update`` used to report a
version that had no relationship to what was running.

The apply mode is not a setting. ``docker compose config`` already knows how
this install produces the container: a ``build:`` key means source, no
``build:`` key means a published image. Asking it costs one command; a toggle
would be a second place to get it wrong.

Nothing here talks to Telegram or to the dashboard. It shells out through
:mod:`utils.updater` and returns dataclasses with ``to_wire()``, so a surface
serializes them without a translation layer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from utils import updater

log = logging.getLogger(__name__)

# Component keys. Public: a surface addresses a component by these.
CONDOR = "condor"
HUMMINGBOT_API = "hummingbot-api"

# The compose service that *is* the API, as opposed to its database and broker.
HB_SERVICE = "hummingbot-api"

# How long a status check is reused. The hourly notice and an admin refreshing
# the screen twice must not each cost a registry round-trip.
CHECK_TTL = 60.0

# Commit subjects shown before collapsing into "and N more".
_DETAIL_LINES = 5


# ── Wire shapes ──


@dataclass(frozen=True)
class Facet:
    """One answer to "what version is this", for one way of being versioned."""

    kind: str  # "repo" | "image"
    current: str
    available: str | None = None
    behind: int = 0
    up_to_date: bool = True
    detail: list[str] = field(default_factory=list)
    error: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComponentStatus:
    """Everything a surface needs to render one component's card."""

    key: str
    name: str
    facets: dict[str, Facet]
    mode: str | None = None  # "image" | "source", hummingbot-api only
    up_to_date: bool = True

    def to_wire(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "facets": {k: v.to_wire() for k, v in self.facets.items()},
            "mode": self.mode,
            "up_to_date": self.up_to_date,
        }


@dataclass(frozen=True)
class Block:
    """Something that stops the update, in words the admin can act on."""

    component: str
    code: str
    message: str
    paths: list[str] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Warning:
    """A consequence worth knowing about. Never a reason to refuse."""

    component: str
    code: str
    message: str

    def to_wire(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Preflight:
    """The answer to "can this run, and what will it do"."""

    components: list[str]
    blocks: list[Block] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocks

    def to_wire(self) -> dict[str, Any]:
        return {
            "components": list(self.components),
            "blocks": [b.to_wire() for b in self.blocks],
            "warnings": [w.to_wire() for w in self.warnings],
            "steps": list(self.steps),
            "ok": self.ok,
        }


# ── Descriptors ──


@dataclass(frozen=True)
class Component:
    """A thing that can be updated. A table entry, not a subclass."""

    key: str
    name: str
    repo_dir: str
    service: str | None = None  # a compose service means it also has an image


def _table() -> dict[str, Component]:
    """The component table, read at call time so the env can still move it."""
    return {
        CONDOR: Component(CONDOR, "Condor", updater.CONDOR_DIR),
        HUMMINGBOT_API: Component(
            HUMMINGBOT_API,
            "Hummingbot API",
            updater.HUMMINGBOT_API_DIR,
            service=HB_SERVICE,
        ),
    }


def get(key: str) -> Component | None:
    return _table().get(key)


def keys() -> list[str]:
    """Component keys that exist on this install, in apply order.

    hummingbot-api first, Condor last: Condor's last step ends the process.
    """
    order = [HUMMINGBOT_API, CONDOR]
    return [k for k in order if os.path.isdir(_table()[k].repo_dir)]


# ── Facets ──


def _short_digest(digest: str | None) -> str:
    """``sha256:4ae0104dd377…`` → ``sha256:4ae0104d``. Enough to compare by eye."""
    if not digest:
        return ""
    algo, _, hexdigest = digest.partition(":")
    return f"{algo}:{hexdigest[:8]}" if hexdigest else digest


async def _repo_facet(repo_dir: str) -> Facet:
    """HEAD versus ``origin/<branch>``, having fetched first."""
    if not await updater.is_git_repo(repo_dir):
        return Facet(
            kind="repo",
            current="unknown",
            up_to_date=False,
            error=f"{repo_dir} is not a git checkout.",
        )

    info = await updater.check_for_updates(repo_dir=repo_dir)
    branch = info.get("branch") or "?"
    local = info.get("local_commit") or "?"

    if info.get("error"):
        return Facet(
            kind="repo",
            current=f"{branch} @ {local}",
            up_to_date=False,
            error=str(info["error"]),
        )

    behind = int(info.get("commits_behind") or 0)
    detail: list[str] = []
    if behind:
        lines = [ln for ln in (info.get("commit_log") or "").split("\n") if ln.strip()]
        detail = lines[:_DETAIL_LINES]
        if behind > len(detail):
            detail.append(f"…and {behind - len(detail)} more")

    return Facet(
        kind="repo",
        current=f"{branch} @ {local}",
        available=f"{branch} @ {info['remote_commit']}" if behind else None,
        behind=behind,
        up_to_date=behind == 0,
        detail=detail,
    )


async def _image_facet(repo_dir: str, service: str) -> tuple[Facet, str]:
    """The digest running locally versus the one the tag resolves to upstream.

    Returns the facet and the detected apply mode, because both come out of the
    same ``compose config`` call and asking twice would double the cost.
    """
    definition = await updater.compose_service(repo_dir, service)
    if definition is None:
        return (
            Facet(
                kind="image",
                current="unknown",
                up_to_date=False,
                error=(
                    "Could not read the compose file — is Docker running? "
                    "(docker compose config failed)"
                ),
            ),
            "unknown",
        )

    mode = "source" if definition.get("build") else "image"
    image_ref = str(definition.get("image") or "")

    if mode == "source":
        # Built here: the repo is the version, and there is no registry to ask.
        return (
            Facet(
                kind="image",
                current=image_ref or "(built from source)",
                detail=["Built from source; the repo below is the version."],
            ),
            mode,
        )

    if not image_ref:
        return (
            Facet(
                kind="image",
                current="unknown",
                up_to_date=False,
                error=f"The {service} service declares neither an image nor a build.",
            ),
            mode,
        )

    local, remote = await asyncio.gather(
        updater.local_image_digest(image_ref),
        updater.registry_image_digest(image_ref),
    )

    if local is None:
        return (
            Facet(
                kind="image",
                current="unknown",
                available=_short_digest(remote),
                up_to_date=False,
                error=(
                    f"No local digest for {image_ref} — it has never been pulled "
                    "by tag, so there is nothing to compare."
                ),
            ),
            mode,
        )
    if remote is None:
        return (
            Facet(
                kind="image",
                current=_short_digest(local),
                up_to_date=False,
                error=(
                    f"Could not reach the registry for {image_ref}; "
                    "the running version is known, the available one is not."
                ),
            ),
            mode,
        )

    same = local == remote
    return (
        Facet(
            kind="image",
            current=_short_digest(local),
            available=None if same else _short_digest(remote),
            # An image is behind or it is not; there is no commit count to give.
            behind=0 if same else 1,
            up_to_date=same,
            detail=[] if same else ["A newer image is published under this tag."],
        ),
        mode,
    )


async def status(key: str) -> ComponentStatus:
    """Every facet of one component, fetched concurrently."""
    component = _table()[key]

    if component.service is None:
        repo = await _repo_facet(component.repo_dir)
        return ComponentStatus(
            key=component.key,
            name=component.name,
            facets={"repo": repo},
            up_to_date=repo.up_to_date,
        )

    repo, (image, mode) = await asyncio.gather(
        _repo_facet(component.repo_dir),
        _image_facet(component.repo_dir, component.service),
    )
    return ComponentStatus(
        key=component.key,
        name=component.name,
        facets={"image": image, "repo": repo},
        mode=mode,
        up_to_date=repo.up_to_date and image.up_to_date,
    )


# ── The cached check ──

_cache: tuple[float, list[ComponentStatus]] | None = None
_cache_lock = asyncio.Lock()


async def check(*, force: bool = False) -> list[ComponentStatus]:
    """Status of every component on this install, at most once per minute."""
    global _cache

    async with _cache_lock:
        if not force and _cache and (time.monotonic() - _cache[0]) < CHECK_TTL:
            return _cache[1]

        statuses = await asyncio.gather(*(status(k) for k in keys()))
        result = list(statuses)
        _cache = (time.monotonic(), result)
        return result


def invalidate() -> None:
    """Drop the cached check. Called after anything that changes a version."""
    global _cache
    _cache = None


# ── What the rest of Condor knows ──


def hb_api_base_url() -> str:
    """Where the API this install talks to is served, or "".

    Read from the configured servers rather than from an env var of its own:
    the address the update probes must be the address Condor actually uses, or
    a healthy answer proves nothing.
    """
    try:
        from config_manager import get_config_manager

        cm = get_config_manager()
        name = cm.get_default_server() or next(iter(cm.list_servers()), "")
        server = cm.get_server(name) if name else None
    except Exception:  # noqa: BLE001 - no config is "no URL", not a crash
        log.debug("Could not resolve the API server", exc_info=True)
        return ""

    if not server or not server.get("host"):
        return ""
    return f"http://{server['host']}:{server['port']}"


def hb_api_health_url() -> str:
    """The endpoint a restarted API answers first. Empty when unconfigured."""
    base = hb_api_base_url()
    return f"{base}/openapi.json" if base else ""


async def running_executor_count() -> int | None:
    """How many executors would be reaped by restarting the API, or None.

    One request: the summary endpoint already buckets by status, so this costs
    no pagination. None means the question could not be answered — which the
    caller must not read as zero.
    """
    from config_manager import get_config_manager

    client = await get_config_manager().get_client()
    summary = await client.executors.get_summary()
    by_status = (summary or {}).get("by_status") or {}
    return int(by_status.get("RUNNING", 0) or 0)


# ── Preflight ──


async def repo_blocks(component: Component) -> list[Block]:
    """What would stop ``git merge --ff-only`` in this checkout.

    Not "is the tree dirty" — that question blocks forever on a checkout which
    is also a runtime working directory, and ``hummingbot-api/bots`` is exactly
    that (it is bind-mounted into the container). The question is whether the
    incoming commits would clobber something local, which is the intersection
    of the two sets and is almost always empty.
    """
    if not await updater.is_git_repo(component.repo_dir):
        return [
            Block(
                component=component.key,
                code="not-a-repo",
                message=(
                    f"{component.repo_dir} is not a git checkout, so there is "
                    "nothing to fast-forward."
                ),
                resolutions=["cancel"],
            )
        ]

    # Fetch first, always. The incoming set is only meaningful against an
    # ``origin/`` that is current, and this runs immediately before the
    # fast-forward that would act on it -- judging the blockers from a stale
    # ref is how local work gets clobbered by commits nobody had seen yet.
    await updater.fetch(component.repo_dir)

    branch = await updater.get_current_branch(component.repo_dir)
    ahead, dirty, incoming = await asyncio.gather(
        updater.ahead_count(component.repo_dir, branch),
        updater.dirty_state(component.repo_dir),
        updater.incoming_paths(component.repo_dir, branch),
    )

    if ahead:
        return [
            Block(
                component=component.key,
                code="diverged",
                message=(
                    f"This checkout has {ahead} commit{'s' if ahead != 1 else ''} "
                    f"that {branch} on the remote does not, so it cannot be "
                    "fast-forwarded. Push or reset it by hand."
                ),
                resolutions=["cancel"],
            )
        ]

    incoming_set = set(incoming)
    if not incoming_set:
        return []

    blocks: list[Block] = []
    tracked = sorted(incoming_set.intersection(dirty.tracked))
    untracked = sorted(incoming_set.intersection(dirty.untracked))

    if tracked:
        blocks.append(
            Block(
                component=component.key,
                code="dirty-conflict",
                message=(
                    f"{len(tracked)} file{'s' if len(tracked) != 1 else ''} "
                    "changed locally would be overwritten by the incoming "
                    "commits."
                ),
                paths=tracked,
                resolutions=["discard", "stash", "cancel"],
            )
        )
    if untracked:
        blocks.append(
            Block(
                component=component.key,
                code="untracked-conflict",
                message=(
                    f"{len(untracked)} local file{'s' if len(untracked) != 1 else ''} "
                    "not in git would be overwritten by the incoming commits."
                ),
                paths=untracked,
                resolutions=["discard", "stash", "cancel"],
            )
        )
    return blocks


def _steps_for(component_key: str, status_: ComponentStatus) -> list[str]:
    """The plan for one component, in the order it will run."""
    steps: list[str] = []
    if component_key == HUMMINGBOT_API:
        repo = status_.facets.get("repo")
        if repo is not None and not repo.up_to_date and repo.error is None:
            steps.append("Fast-forward the hummingbot-api checkout")
        if status_.mode == "source":
            steps.append("Rebuild the hummingbot-api image from source")
        else:
            steps.append("Pull the published hummingbot-api image")
        steps.append("Recreate the containers")
        steps.append("Wait for the API to answer")
    else:
        steps.append("Fast-forward the Condor checkout")
        steps.append("Sync dependencies")
        steps.append("Rebuild the dashboard if the update touched it")
        steps.append("Restart Condor")
    return steps


async def _executor_warning() -> Warning | None:
    """Restarting the API reaps running executors. Say so, with a count.

    Best effort by design: if the count cannot be read the warning still goes
    out without a number, because the consequence is real either way.
    """
    count: int | None = None
    try:
        count = await running_executor_count()
    except Exception:  # noqa: BLE001 - a warning must never break the preflight
        log.debug("Could not count running executors", exc_info=True)

    if count is None:
        detail = "Any running executors will be reaped."
    elif count == 0:
        return None
    else:
        detail = f"{count} running executor{'s' if count != 1 else ''} will be reaped."
    return Warning(
        component=HUMMINGBOT_API,
        code="executors-will-be-reaped",
        message=f"Restarting the API container stops its executors. {detail}",
    )


async def preflight(component_keys: list[str]) -> Preflight:
    """Can this update run, what will it do, and what should the admin know."""
    known = _table()
    selected = [k for k in keys() if k in set(component_keys) and k in known]

    if not selected:
        return Preflight(
            components=[],
            blocks=[
                Block(
                    component="",
                    code="nothing-selected",
                    message="No component was selected for update.",
                    resolutions=["cancel"],
                )
            ],
        )

    statuses = {s.key: s for s in await check()}
    blocks: list[Block] = []
    warnings: list[Warning] = []
    steps: list[str] = []

    for key in selected:
        component = known[key]
        current = statuses.get(key)

        # Only the repo facets that will actually be moved need a preflight.
        needs_repo = True
        if current is not None:
            repo_facet = current.facets.get("repo")
            needs_repo = repo_facet is None or not repo_facet.up_to_date
        if needs_repo:
            blocks.extend(await repo_blocks(component))

        if current is not None:
            image = current.facets.get("image")
            if image is not None and image.error:
                blocks.append(
                    Block(
                        component=key,
                        code=(
                            "docker-unavailable"
                            if "compose" in image.error
                            else "registry-unreachable"
                        ),
                        message=image.error,
                        resolutions=["cancel"],
                    )
                )
            steps.extend(_steps_for(key, current))

    if HUMMINGBOT_API in selected:
        executor_warning = await _executor_warning()
        if executor_warning is not None:
            warnings.append(executor_warning)
    if CONDOR in selected:
        warnings.append(
            Warning(
                component=CONDOR,
                code="sessions-will-restart",
                message=(
                    "Condor restarts to finish. Bots keep running; continuous "
                    "routines and agent loops are restored on the way back up."
                ),
            )
        )

    return Preflight(components=selected, blocks=blocks, warnings=warnings, steps=steps)
