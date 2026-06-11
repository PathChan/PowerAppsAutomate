"""Browser CDP 层：PowerApps Studio iframe 操作、CDP 超时、坐标缓存。"""

from ._cdp_timeout import TimeoutWrappedCDPClient
from .moc_target_cache import (
    click_via_cache,
    get_cached,
    invalidate,
    list_all,
    save_cached,
    selector_locator,
)
from .studio_cdp import (
    click_in_studio,
    diagnose_focus_state,
    execute_in_studio,
    find_studio_frame_id,
    focus_formula_editor_via_dispatch,
    inspect_formula_bar_dom,
    reset_studio_cache,
    _ensure_studio_context,
)

__all__ = [
    "TimeoutWrappedCDPClient",
    "click_in_studio",
    "click_via_cache",
    "diagnose_focus_state",
    "execute_in_studio",
    "find_studio_frame_id",
    "focus_formula_editor_via_dispatch",
    "get_cached",
    "inspect_formula_bar_dom",
    "invalidate",
    "list_all",
    "reset_studio_cache",
    "save_cached",
    "selector_locator",
    "_ensure_studio_context",
]
