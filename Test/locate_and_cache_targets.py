"""批量预填充 PowerApps Studio 常用 UI 元素的坐标缓存。

工作流程
--------
1. 复用 main.py 的持久化 profile，打开 Studio；
2. 你手动登录 / 打开目标 App 到 Studio 编辑器；
3. 脚本依次定位以下 target 并把中心坐标写入
   ``.cache/powerapps/dom_targets.json``：

    - ribbon::插入 / 视图 / 主题 …
    - component::按钮 / 文本输入框 / 标签 / 图标 …
    - property::Text / X / Y / Width / Height / Fill …
    - formula_bar::view_lines

   对 component / property 这种需要先展开面板的 target，脚本会先点击
   对应的 ribbon 入口、等面板出现，再做定位。

不会修改任何 PowerApps 数据：只读 getBoundingClientRect。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mocProcessing import BrowserSession  # noqa: E402
from mocProcessing.tools.powerapps_chain import execute_in_studio  # noqa: E402
from mocProcessing.tools.target_cache import (  # noqa: E402
    _get_iframe_offset_and_viewport,
    list_all,
    listitem_locator,
    property_input_locator,
    save_cached,
    selector_locator,
    text_button_locator,
)


# ── 默认要预热的目标 ────────────────────────────────────────────
# 修改此处即可扩展常用元素。
DEFAULT_RIBBON_BUTTONS = [
    "插入",
    "视图",
    "主题",
]

DEFAULT_COMPONENTS = [
    "按钮",
    "文本输入框",
    "标签",
    "图标",
]

DEFAULT_PROPERTIES = [
    "Text",
    "X",
    "Y",
    "Width",
    "Height",
    "Fill",
]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(_PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("locate_and_cache_targets")


async def _locate_and_save(
    session: BrowserSession,
    key: str,
    kind: str,
    label: str,
    locator,
) -> bool:
    info = await locator(session)
    if not info or not info.get("found"):
        log.warning("MISS  %-30s reason=%s", key, info)
        return False

    rect = info["rect"]
    geo = await _get_iframe_offset_and_viewport(session)
    ox = float(geo.get("iframe_x", 0) or 0)
    oy = float(geo.get("iframe_y", 0) or 0)
    cx = ox + float(rect["x"]) + float(rect["w"]) / 2.0
    cy = oy + float(rect["y"]) + float(rect["h"]) / 2.0

    from datetime import datetime, timezone

    save_cached(key, {
        "name": key,
        "kind": kind,
        "label": label,
        "selector": info.get("selector", ""),
        "matched_text": info.get("text", ""),
        "x": round(cx, 1),
        "y": round(cy, 1),
        "viewport_width": geo.get("vw"),
        "viewport_height": geo.get("vh"),
        "device_pixel_ratio": geo.get("dpr"),
        "iframe_offset": {"x": ox, "y": oy},
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    log.info("CACHE %-30s -> (%.1f, %.1f) text=%r", key, cx, cy, info.get("text", "")[:40])
    return True


async def _click_in_studio_via_js(session: BrowserSession, label: str) -> None:
    """直接在 Studio iframe 派发事件点击 ribbon 按钮（用来打开面板）。"""
    js = rf"""
    (() => {{
        const target = {json.dumps(label)};
        const buttons = document.querySelectorAll('button, [role="button"], [role="tab"]');
        for (const btn of buttons) {{
            const text = (btn.textContent || '').trim();
            const lbl = (btn.getAttribute('aria-label') || '').trim();
            if (text.includes(target) || lbl.includes(target)) {{
                const opts = {{bubbles: true, cancelable: true, view: window}};
                btn.dispatchEvent(new PointerEvent('pointerdown', opts));
                btn.dispatchEvent(new MouseEvent('mousedown', opts));
                btn.dispatchEvent(new PointerEvent('pointerup', opts));
                btn.dispatchEvent(new MouseEvent('mouseup', opts));
                btn.dispatchEvent(new MouseEvent('click', opts));
                return {{ok: true}};
            }}
        }}
        return {{ok: false}};
    }})()
    """
    await execute_in_studio(session, js)


async def warm_cache(session: BrowserSession) -> dict[str, int]:
    """按 ribbon → component → property → formula_bar 顺序定位并缓存。"""
    stats = {"ribbon": 0, "component": 0, "property": 0, "formula_bar": 0}

    # 1) ribbon
    log.info("=== Locating ribbon buttons ===")
    for label in DEFAULT_RIBBON_BUTTONS:
        if await _locate_and_save(
            session,
            f"ribbon::{label}",
            "ribbon_button",
            label,
            text_button_locator(label),
        ):
            stats["ribbon"] += 1

    # 2) component：先点击"插入"展开面板
    log.info("=== Opening insert panel and locating components ===")
    await _click_in_studio_via_js(session, "插入")
    await asyncio.sleep(1.2)
    for label in DEFAULT_COMPONENTS:
        if await _locate_and_save(
            session,
            f"component::{label}",
            "component_item",
            label,
            listitem_locator(label),
        ):
            stats["component"] += 1

    # 3) property：属性面板一般默认就在右侧；如果没有，提示手动选中一个控件
    log.info("=== Locating property inputs (make sure a control is selected) ===")
    for label in DEFAULT_PROPERTIES:
        if await _locate_and_save(
            session,
            f"property::{label}",
            "property_input",
            label,
            property_input_locator(label),
        ):
            stats["property"] += 1

    # 4) formula bar
    log.info("=== Locating formula bar ===")
    if await _locate_and_save(
        session,
        "formula_bar::view_lines",
        "formula_bar",
        "formula_bar",
        selector_locator("#formulaBarContainer .view-lines", "formula_bar"),
    ):
        stats["formula_bar"] += 1

    return stats


async def main() -> None:
    power_apps_url = os.getenv("POWER_APPS_URL", "").strip()
    if not power_apps_url:
        raise RuntimeError(
            "POWER_APPS_URL not set; configure it in .env (same as main.py)."
        )

    user_data_env = os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
    if user_data_env:
        user_data_dir = (
            Path(user_data_env)
            if Path(user_data_env).is_absolute()
            else (_PROJECT_ROOT / user_data_env)
        ).resolve()
    else:
        user_data_dir = _PROJECT_ROOT / ".chrome-profile-test"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    log.info("Launching browser profile=%s", user_data_dir)
    session = BrowserSession(
        headless=False,
        user_data_dir=str(user_data_dir),
        enable_default_extensions=False,
        keep_alive=True,
    )
    await session.start()
    await session.new_page(power_apps_url)

    log.info("Sign in if needed, open a Studio editor, and SELECT any control to populate property pane.")
    await asyncio.to_thread(
        input,
        "Press Enter here once the Studio editor is open and a control is selected...",
    )

    stats = await warm_cache(session)
    log.info("Done. Cached counts: %s", stats)
    log.info("Total cached targets now: %d", len(list_all()))

    log.info("Browser stays open for 15s so you can inspect; then it exits.")
    await asyncio.sleep(15)
    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
