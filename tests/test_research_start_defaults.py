import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from routes.research_routes import setup_research_routes


def _route(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} route not registered")


def test_research_start_uses_run_timeout_setting_when_max_time_omitted(monkeypatch):
    handler = MagicMock()
    router = setup_research_routes(handler)
    endpoint = _route(router, "/api/research/start", "POST")
    body_cls = endpoint.__annotations__["body"]

    monkeypatch.setattr(
        "src.auth_helpers.require_privilege",
        lambda request, key: "alice",
    )
    monkeypatch.setattr(
        "routes.research_routes.resolve_endpoint",
        lambda purpose, owner=None: ("http://local.test/v1/chat/completions", "local-model", {}),
    )
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: 1200 if key == "research_run_timeout_seconds" else default,
    )

    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(current_user="alice"),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )

    asyncio.run(endpoint(body=body_cls(query="local llm research"), request=request))

    handler.start_research.assert_called_once()
    assert handler.start_research.call_args.kwargs["max_time"] == 1200

