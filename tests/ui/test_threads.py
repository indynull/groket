"""UI thread marshaling helpers."""

from __future__ import annotations

from types import SimpleNamespace

from groket.ui.threads import call_ui, resolve_ui_app
from textual.app import App


def test_resolve_ui_app_none_for_plain_object() -> None:
    assert resolve_ui_app(object()) is None
    assert resolve_ui_app(SimpleNamespace(app="not-an-app")) is None


def test_resolve_ui_app_returns_mounted_app() -> None:
    app = App()
    assert resolve_ui_app(SimpleNamespace(app=app)) is app


def test_call_ui_none_app_skips() -> None:
    called = False

    def _cb() -> int:
        nonlocal called
        called = True
        return 1

    assert call_ui(None, _cb) is None
    assert called is False


def test_call_ui_runtime_error_falls_back_inline() -> None:
    class _App:
        def call_from_thread(self, callback, *args, **kwargs):
            raise RuntimeError("not on worker thread")

    assert call_ui(_App(), lambda: 7) == 7  # type: ignore[arg-type]


def test_call_ui_skips_when_app_not_running() -> None:
    class _App:
        def call_from_thread(self, callback, *args, **kwargs):
            raise RuntimeError("App is not running")

    called = False

    def _cb() -> int:
        nonlocal called
        called = True
        return 1

    assert call_ui(_App(), _cb) is None  # type: ignore[arg-type]
    assert called is False


def test_call_ui_other_error_falls_back_or_none() -> None:
    class _App:
        def call_from_thread(self, callback, *args, **kwargs):
            raise ValueError("broken bridge")

    assert call_ui(_App(), lambda: 9) == 9  # type: ignore[arg-type]

    class _AppBoom:
        def call_from_thread(self, callback, *args, **kwargs):
            raise ValueError("broken bridge")

    def _boom() -> None:
        raise RuntimeError("also broken")

    assert call_ui(_AppBoom(), _boom) is None  # type: ignore[arg-type]
