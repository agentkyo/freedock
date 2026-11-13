"""Companion apps: other programs on this PC that drive alongside FreeDock (MyWheel, scripts...).

They talk to FreeDock over the loopback HTTP bridge (see :mod:`freedock.notify`):

* ``GET /info`` - who we are, the pages, the current page and every hotkey the deck can send.
  A companion that binds keys of its own reads ``hotkeys`` so both never fire the same key.
* ``/page?id=<page id>`` - show a page.
* ``/context?game=ets2&vehicle=bus&name=...&source=mywheel`` - what the player is driving. When the
  deck shows one of that game's vehicle pages it follows the vehicle (truck page <-> bus page).

Everything here is optional: FreeDock behaves exactly the same when no companion ever calls.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .. import __version__

if TYPE_CHECKING:
    from ..engine import Engine
    from ..profile import Profile

log = logging.getLogger(__name__)

API_VERSION = 1

# (game, vehicle) -> page id. The pages of one game are "siblings": the deck only swaps between them.
VEHICLE_PAGES = {
    ("ets2", "truck"): "ets2",
    ("ets2", "bus"): "ets2_bus",
}


def _walk_hotkeys(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "hotkey" and node.get("keys"):
            out.append(str(node["keys"]))
        for v in node.values():
            _walk_hotkeys(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk_hotkeys(v, out)


def hotkeys(profile: "Profile") -> list[str]:
    """Every key combo a hotkey action of the profile sends (multi-action steps included)."""
    found: list[str] = []
    _walk_hotkeys(profile.to_dict(), found)
    return sorted(set(found))


class Companion:
    def __init__(self, engine: "Engine"):
        self.engine = engine
        self.context: dict[str, dict] = {}      # game -> {"vehicle", "name", "source", "t"}

    def info(self) -> dict:
        e = self.engine
        return {
            "app": "freedock", "version": __version__, "api": API_VERSION,
            "page": {"id": e.page.id, "name": e.page.name},
            "pages": [{"id": p.id, "name": p.name} for p in e.visible_pages()],
            "hotkeys": hotkeys(e.profile),
            "context": self.context,
        }

    def siblings_of(self, page_id: str) -> set[str]:
        """The other vehicle pages of the game ``page_id`` belongs to (empty when it has none)."""
        game = next((g for (g, _v), pid in VEHICLE_PAGES.items() if pid == page_id), None)
        return {pid for (g, _v), pid in VEHICLE_PAGES.items() if g == game} - {page_id} if game else set()

    def resolve(self, page_id: str) -> str:
        """The page to really show when ``page_id`` is asked for: its sibling for the current vehicle.

        A context that arrived while another page was showing is applied here, the moment the
        game's page opens."""
        for (game, vehicle), pid in VEHICLE_PAGES.items():
            if pid != page_id and pid in self.siblings_of(page_id) \
                    and self.context.get(game, {}).get("vehicle") == vehicle \
                    and self.engine.profile.page(pid) is not None:
                return pid
        return page_id

    def forget(self, game: str | None = None) -> None:
        """Drop what we know about a game's vehicle (the game closed)."""
        if game is None:
            self.context.clear()
        else:
            self.context.pop(game, None)

    def has_page(self, page_id: str) -> bool:
        return bool(page_id) and self.engine.profile.page(page_id) is not None

    def show_page(self, page_id: str) -> None:
        """UI thread."""
        self.engine.goto(page_id)

    def set_context(self, game: str, vehicle: str, name: str = "", source: str = "") -> bool:
        """UI thread. Remember what is being driven; follow it if a sibling page is showing.

        Returns True when the deck changed page. A repeated context is a no-op, so the player can
        still pick the other page by hand without being pulled back."""
        prev = self.context.get(game, {}).get("vehicle")
        self.context[game] = {"vehicle": vehicle, "name": name, "source": source, "t": time.time()}
        if vehicle == prev:
            return False
        siblings = {pid for (g, _v), pid in VEHICLE_PAGES.items() if g == game}
        target = VEHICLE_PAGES.get((game, vehicle))
        e = self.engine
        if not target or e.page.id == target or e.page.id not in siblings or e.profile.page(target) is None:
            return False
        log.info("companion %s: %s is now a %s -> page %s", source or "?", game, vehicle, target)
        e.goto(target, auto=True)
        return True
