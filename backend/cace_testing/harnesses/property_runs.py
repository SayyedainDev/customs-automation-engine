"""Hypothesis execution-policy harness for CACE property evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from hypothesis import Verbosity, given, settings
from hypothesis import strategies as st

_FnT = TypeVar("_FnT", bound=Callable[..., None])

settings.register_profile(
    "cace_l1",
    max_examples=60,
    deadline=None,
    database=None,
    print_blob=True,
    verbosity=Verbosity.normal,
)
settings.load_profile("cace_l1")


def run_property(
    *given_args: st.SearchStrategy[Any],
    **given_kwargs: st.SearchStrategy[Any],
) -> Callable[[_FnT], _FnT]:
    """Apply Hypothesis generation with harness-owned CACE settings and replay output."""

    def _decorator(fn: _FnT) -> _FnT:
        configured = settings(
            max_examples=60,
            deadline=None,
            database=None,
            print_blob=True,
        )(fn)
        wrapped: Any = given(*given_args, **given_kwargs)(configured)
        return wrapped  # type: ignore[no-any-return]  # Hypothesis decorators wrap runtime callables

    return _decorator
