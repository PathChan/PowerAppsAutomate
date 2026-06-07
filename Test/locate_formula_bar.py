"""定位 PowerApps Studio 公式编辑器（Monaco Formula Bar）的探测脚本。

目标
----
找到一个 **能稳定点到** 公式编辑器、并让 Monaco 内部 textarea 真正聚焦
的入口元素。

结论（来自项目内已积累的经验，见
`mocProcessing/agent/system_prompts/experience.md` 与
`mocProcessing/browser/watchdogs/default_action_watchdog.py`）：

    PowerApps 公式栏使用 Monaco Editor，其隐藏 <textarea> 无法通过
    任何 CDP mouse event / focus 直接激活。
    **唯一可靠的聚焦方式 = 点击 `#formulaBarContainer > button`**。
    点击该按钮后，PowerApps 自身的 click handler 会触发 Monaco 焦点
    转移，把光标放到隐藏 textarea 的最后。

本脚本做的事
------------
1) 用项目里复用过的 BrowserSession 启动一个可见浏览器（持久 profile，
   方便你手动登录一次后下次自动复用）。
2) 打开硬编码的 PowerApps Studio URL。
3) 轮询等待 `#formulaBarContainer > button` 出现，找到后输出：
       - 元素是否可见
       - 它在视口里的几何坐标（boundingClientRect）
       - aria-label / role / classList 等关键属性
4) 真正去点这个按钮（用页面 DOM 上的 .click()，等价于 watchdog 里
   `_POWERAPPS_FOCUS_JS` 的做法），再读 `document.activeElement` 来
   验证焦点是否落到 Monaco 的隐藏 `.inputarea` 上。

运行方式
--------
    cd c:\\Users\\PXNC\\Desktop\\Codes\\PowerAppsAutomate
    python Test\\locate_formula_bar.py

脚本会停留 30 秒让你肉眼观察，然后退出。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# 把项目根目录加入 sys.path，复用 mocProcessing 包。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mocProcessing import BrowserSession  # noqa: E402


def _load_env_file(path: Path) -> None:
    """读取项目根目录的 .env，把 KEY=VALUE 注入到 os.environ。

    不覆盖已经存在的环境变量；不依赖第三方库（main.py 同款实现）。
    """
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

# ── 从 .env 读取 PowerApps Studio URL 与用户数据目录 ─────────────
POWER_APPS_URL = os.getenv("POWER_APPS_URL", "").strip()
if not POWER_APPS_URL:
    raise RuntimeError(
        "POWER_APPS_URL not set. Please configure it in "
        f"{_PROJECT_ROOT / '.env'}"
    )

# 持久化用户数据目录：手动登录一次后，下次会自动保留 cookie/session。
# 优先用 .env 里的 BROWSER_USE_USER_DATA_DIR，跟 main.py 共用同一份 profile，
# 这样你之前在 main.py 里完成的 Microsoft 登录可以直接复用，无需重新登。
_user_data_env = os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
if _user_data_env:
    USER_DATA_DIR = (
        Path(_user_data_env)
        if Path(_user_data_env).is_absolute()
        else (_PROJECT_ROOT / _user_data_env)
    ).resolve()
else:
    USER_DATA_DIR = _PROJECT_ROOT / ".chrome-profile-test"

# 等公式栏出现的最长时间（秒）。给登录、Studio 加载留够时间。
WAIT_TIMEOUT_SEC = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("locate_formula_bar")


# JS 工具函数：必须是 (...args) => 形式，mocProcessing.actor.page 的
# evaluate() 会强校验这个格式。
PROBE_JS = r"""
(() => {
    const btn = document.querySelector('#formulaBarContainer > button');
    if (!btn) {
        return {found: false};
    }
    const rect = btn.getBoundingClientRect();
    const style = window.getComputedStyle(btn);
    const attrs = {};
    for (const a of btn.attributes) {
        attrs[a.name] = a.value;
    }
    return {
        found: true,
        visible: rect.width > 0 && rect.height > 0
            && style.visibility !== 'hidden'
            && style.display !== 'none',
        rect: {
            x: rect.x, y: rect.y,
            width: rect.width, height: rect.height,
        },
        tagName: btn.tagName,
        className: btn.className,
        attributes: attrs,
        innerText: (btn.innerText || '').slice(0, 200),
    };
})()
"""


CLICK_AND_REPORT_JS = r"""
(() => {
    const btn = document.querySelector('#formulaBarContainer > button');
    if (!btn) return {clicked: false, reason: 'button not found'};
    btn.click();
    // 给 PowerApps 内部 focus 流程一点时间
    const active = document.activeElement;
    return {
        clicked: true,
        activeTag: active ? active.tagName : null,
        activeClass: active ? active.className : null,
        // Monaco 隐藏 textarea 的特征 class
        isMonacoInputArea: !!(active && active.classList
            && active.classList.contains('inputarea')),
    };
})()
"""


async def wait_for_formula_bar(page, timeout_sec: int) -> dict:
    """轮询页面，直到 #formulaBarContainer > button 出现并可见。"""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    last_info: dict = {"found": False}
    interval = 1.0
    while asyncio.get_event_loop().time() < deadline:
        raw = await page.evaluate(PROBE_JS)
        try:
            info = json.loads(raw) if raw else {"found": False}
        except json.JSONDecodeError:
            info = {"found": False, "raw": raw}
        last_info = info
        if info.get("found") and info.get("visible"):
            return info
        # 第一秒安静一点，之后才打印进度
        if asyncio.get_event_loop().time() > deadline - timeout_sec + 2:
            log.info("waiting for formula bar... found=%s visible=%s",
                     info.get("found"), info.get("visible"))
        await asyncio.sleep(interval)
    return last_info


async def main() -> None:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Launching browser, profile=%s", USER_DATA_DIR)
    session = BrowserSession(
        headless=False,
        user_data_dir=str(USER_DATA_DIR),
        enable_default_extensions=False,
        keep_alive=True,
    )

    # 必须先 start() 建立 CDP 连接，否则 new_page() 会断言失败
    # (assert self._cdp_client_root is not None)。
    await session.start()

    page = await session.new_page(POWER_APPS_URL)
    log.info("Opened: %s", await page.get_url())
    log.info("Title : %s", await page.get_title())

    log.info("If this is the first run, sign in manually and open an app's "
             "Studio editor. Probing for the formula bar (timeout=%ds)...",
             WAIT_TIMEOUT_SEC)

    info = await wait_for_formula_bar(page, WAIT_TIMEOUT_SEC)

    if not info.get("found"):
        log.error("FAIL: #formulaBarContainer > button never appeared. "
                  "Make sure you opened a PowerApps Studio editor page.")
        log.error("Last probe info: %s", info)
        return

    log.info("=" * 70)
    log.info("FOUND #formulaBarContainer > button")
    log.info("  tag       : %s", info.get("tagName"))
    log.info("  class     : %s", info.get("className"))
    log.info("  rect      : %s", info.get("rect"))
    log.info("  visible   : %s", info.get("visible"))
    log.info("  attributes: %s", info.get("attributes"))
    log.info("  innerText : %r", info.get("innerText"))
    log.info("=" * 70)

    # 真正去点，验证 Monaco 是否聚焦
    log.info("Clicking the button to test Monaco focus...")
    raw = await page.evaluate(CLICK_AND_REPORT_JS)
    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        result = {"raw": raw}
    log.info("Click result: %s", result)

    if result.get("isMonacoInputArea"):
        log.info("SUCCESS: Monaco hidden <textarea>.inputarea is now focused.")
    else:
        log.warning("Monaco was NOT focused after click. activeTag=%s class=%s",
                    result.get("activeTag"), result.get("activeClass"))

    # 也用 CSS 选择器拿到 Element 句柄，证明可以走标准 actor API 点击
    elements = await page.get_elements_by_css_selector(
        "#formulaBarContainer > button"
    )
    log.info("get_elements_by_css_selector returned %d element(s).",
            len(elements))
    if elements:
        el = elements[0]
        basic = await el.get_basic_info()
        log.info("Element basic info: nodeName=%s bbox=%s",
                 basic.get("nodeName"), basic.get("boundingBox"))

    log.info("Holding the browser open for 30s so you can inspect...")
    await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
