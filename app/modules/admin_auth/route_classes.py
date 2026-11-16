"""Which routes are Bearer admin routes (R3 class 1): `current_admin` in the dependant tree.

Shared by the admin CORS scope (ADR-0011) and `tests/test_route_auth_classes.py`, so both use
one definition.
"""

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.routing import Match
from starlette.types import Scope

from app.modules.admin_auth.deps import current_admin


def dependant_calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for sub in dependant.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from dependant_calls(sub)


def is_bearer_dependant(dependant: Dependant) -> bool:
    return current_admin in dependant_calls(dependant)


class BearerRouteMatcher:
    """`RouteMatcher`: True iff the route the router would pick for (path, method) is Bearer.

    Built on the first call (every router is included by then), in router order; the first
    `Match.FULL` decides. No full match (404/405) → False.
    """

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self._routes: list[tuple[RouteContext, bool]] | None = None

    def _build(self) -> list[tuple[RouteContext, bool]]:
        return [
            (ctx, isinstance(ctx.original_route, APIRoute) and is_bearer_dependant(ctx.dependant))
            for ctx in iter_route_contexts(self.app.routes)
        ]

    def __call__(self, scope: Scope, method: str) -> bool:
        if self._routes is None:
            self._routes = self._build()
        probe = {**scope, "method": method}
        for ctx, bearer in self._routes:
            match, _ = ctx.matches(probe)
            if match == Match.FULL:
                return bearer
        return False
