"""
Test 3: 用户动作监测测试
========================
启动 StudioActionWatchdog，监测用户在 PowerApps Studio 中的一切操作：
  - 点击了什么控件（Tree View 变化）
  - 修改了什么属性（属性面板变化）
  - 在公式栏输入了什么（公式变化）
  - 所有监测到的动作实时输出到控制台

运行方式：
  1. 确保已登录 https://make.powerapps.com 并打开 App
  2. 在画布/属性面板/公式栏中进行操作
  3. 观察控制台输出
    uv run python .\Test\test_watchdog_monitor.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PowerfulApps.Agents.memory.graph_memory import ControlGraph
from PowerfulApps.Browser.watchdogs.studio_action_watchdog import StudioActionWatchdog
from PowerfulApps.Agents.config.env import load_env_file

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_watchdog")

# ── 颜色工具 ──────────────────────────────────────────────────
_C = {"rst": "[0m", "b": "[1m", "d": "[2m", "r": "[31m",
      "g": "[32m", "y": "[33m", "c": "[36m", "m": "[35m"}
_E = chr(27)
def cc(t: str, *ns: str) -> str:
    return _E + "".join(_C.get(n, "") for n in ns) + t + _E + _C["rst"]

# ── 时间戳 ────────────────────────────────────────────────────
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def print_header(title: str) -> None:
    print()
    print(cc(f"{'='*60}", "c"))
    print(cc(f"  {title}", "c", "b"))
    print(cc(f"{'='*60}", "c"))


async def setup() -> tuple:
    """创建浏览器会话并连接 PowerApps Studio"""
    from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio

    load_env_file(_PROJECT_ROOT / ".env")
    url = os.getenv("POWER_APPS_URL", "https://make.powerapps.com")
    ud = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile")))
    ud.mkdir(parents=True, exist_ok=True)

    print(cc("  [1/3] 启动浏览器...", "y"))
    session = await create_browser_session(url, ud)

    print(cc("  [2/3] 连接 Studio...", "y"))
    ok = await prepare_studio(session)
    if not ok:
        print(cc("  [WARN] Studio 未连接", "r"))
    else:
        print(cc("  [OK] Studio 已连接", "g"))

    return session, ok


async def monitor_loop(
    watchdog: StudioActionWatchdog,
    graph: ControlGraph,
) -> None:
    """主监测循环：轮询变更 + 快照 + 输出到控制台。再按回车即停止。"""
    last_snapshot = None
    change_count = 0
    snapshot_count = 0

    print()
    print(cc(f"  ╔{'═'*58}╗", "c"))
    print(cc(f"  ║  监测已启动！到 PowerApps Studio 中操作吧              ║", "c", "b"))
    print(cc(f"  ║  按回车停止监测                                        ║", "d"))
    print(cc(f"  ╚{'═'*58}╝", "c"))
    print()

    async def wait_enter():
        """等待用户按回车"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, input)

    async def monitor():
        nonlocal last_snapshot, change_count, snapshot_count
        while True:
            try:
                changes = await watchdog.collect_changes()
                for ch in changes:
                    change_count += 1
                    ct = ch.get("type", "?")
                    ts_str = ts()
                    if ct == "tree_add":
                        print(cc(f"  [{ts_str}] 📦 新增控件: {ch.get('text','')}", "g", "b"))
                        graph.add_control("__unknown__", ch.get("text", ""))
                    elif ct == "tree_remove":
                        print(cc(f"  [{ts_str}] 🗑️ 删除控件: {ch.get('text','')}", "r"))
                        graph.remove_control(ch.get("text", ""))
                    elif ct == "tree_change":
                        print(cc(f"  [{ts_str}] 🔄 Tree 变化: {ch.get('text','')}", "y"))
                    elif ct == "prop_change":
                        old = ch.get("oldValue", "")
                        new = ch.get("newValue", "")
                        prop = ch.get("property", "?")
                        print(cc(f"  [{ts_str}] ✏️  属性修改: {prop}", "g"))
                        print(cc(f"      旧值: {old!r}", "r"))
                        print(cc(f"      新值: {new!r}", "g"))
                        sel = (await watchdog.snapshot()).selected_control
                        if sel and graph.graph.has_node(sel):
                            graph.update_property(sel, prop, new)
                            graph.auto_detect_references(sel)
                    elif ct == "formula_change":
                        old_f = ch.get("oldValue", "")
                        new_f = ch.get("newValue", "")
                        print(cc(f"  [{ts_str}] 📝 公式变更:", "m", "b"))
                        if old_f:
                            print(cc(f"      旧公式: {old_f[:150]}", "r"))
                        print(cc(f"      新公式: {new_f[:150]}", "g"))
                        sel = (await watchdog.snapshot()).selected_control
                        if sel and graph.graph.has_node(sel):
                            graph.update_property(sel, "_formula", new_f)
                            graph.auto_detect_references(sel)
                    else:
                        print(cc(f"  [{ts_str}] [?] 其他: {json.dumps(ch, ensure_ascii=False)}", "d"))

                # 快照
                snapshot = await watchdog.snapshot()
                snapshot_count += 1
                if snapshot.selected_control:
                    sel = snapshot.selected_control
                    if not last_snapshot or last_snapshot.selected_control != sel:
                        print(cc(f"  [{ts()}] 🎯 选中控件: {sel}", "c", "b"))
                    if snapshot.properties:
                        for k, v in snapshot.properties.items():
                            old_v = last_snapshot.properties.get(k) if last_snapshot else None
                            if old_v is None:
                                print(cc(f"  [{ts()}] 📋 属性 {sel}.{k} = {v!r}", "d"))
                        for k, v in snapshot.properties.items():
                            if graph.graph.has_node(sel):
                                graph.update_property(sel, k, v)
                        if graph.graph.has_node(sel):
                            graph.auto_detect_references(sel)
                last_snapshot = snapshot
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(cc(f"  [{ts()}] [ERR] {e}", "r"))

    # 同时跑监测和等待回车
    monitor_task = asyncio.create_task(monitor())
    await wait_enter()
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    print()
    print(cc(f"  ╔{'═'*58}╗", "c"))
    print(cc(f"  ║  监测结束                                     ║", "c", "b"))
    print(cc(f"  ║  共检测到 {change_count} 次变更, {snapshot_count} 次快照  ║", "d"))
    print(cc(f"  ╚{'═'*58}╝", "c"))


async def main() -> None:
    print(cc("+-----------------------------------------------------------+", "c"))
    print(cc("|  Test 3: 用户动作监测                                     |", "c", "b"))
    print(cc("|  启动浏览器 → 你登录 → 按回车 → 实时监测所有操作           |", "d"))
    print(cc("+-----------------------------------------------------------+", "c"))

    # ── 先建立连接 ──────────────────────────────────────────
    session, studio_ok = await setup()
    if studio_ok:
        print(cc("  [OK] 浏览器已启动，请在打开的页面中登录 PowerApps Studio", "g"))
    else:
        print(cc("  [WARN] 浏览器已启动但 Studio 未连接", "y"))

    input(cc("\n  > 登录完成后，按回车启动监测 (去到 Studio 中进行操作)...\n", "c", "b"))

    # ── 用户按回车后重试连接 ────────────────────────────────
    if not studio_ok:
        print(cc("  [3/3] 重试连接 Studio...", "y"))
        from PowerfulApps.Agents.core.runtime import prepare_studio
        from PowerfulApps.Browser.cdp.studio_cdp import reset_studio_cache
        reset_studio_cache()
        studio_ok = await prepare_studio(session)
        if studio_ok:
            print(cc("  [OK] Studio 已连接！", "g"))
        else:
            print(cc("  [WARN] Studio 仍未连接，尝试直接启动观察器", "y"))

    # ── 创建图 ───────────────────────────────────────────────
    graph = ControlGraph()

    # ── 启动 Watchdog ────────────────────────────────────────
    print_header("1. 注入 MutationObserver")
    watchdog = StudioActionWatchdog(session)
    injected = await watchdog.start()
    if injected:
        print(cc("  ✓ Observer 注入成功", "g"))
    else:
        print(cc("  [!] Observer 注入可能失败，但继续尝试", "y"))

    # ── 先拍一张初始快照 ────────────────────────────────────
    print_header("2. 初始状态快照")
    initial = await watchdog.snapshot()
    if initial.tree:
        print(cc(f"  Tree View: {len(initial.tree)} 个条目", "g"))
        for item in initial.tree[:10]:
            name = item.get("name", "")
            depth = item.get("depth", 0)
            indent = "  " * depth
            print(cc(f"    {indent}▸ {name}", "d"))
    if initial.selected_control:
        print(cc(f"  当前选中: {initial.selected_control}", "c"))
    if initial.properties:
        for k, v in initial.properties.items():
            print(cc(f"    {k} = {v!r}", "d"))

    # ── 持续监测 ─────────────────────────────────────────────
    print_header("3. 实时监测")
    try:
        await monitor_loop(watchdog, graph)
    except KeyboardInterrupt:
        print(cc("\n  [!] 用户中断", "y"))

    # ── 输出统计 ─────────────────────────────────────────────
    print_header("4. 监测统计")
    stats = graph.stats()
    print(cc(f"  Graph: {stats['screens']} 屏幕, {stats['controls']} 控件, {stats['edges']} 引用边", "g"))
    print(cc(f"  图 JSON: {graph.to_json()[:2000]}", "d"))

    # ── 清理 ─────────────────────────────────────────────────
    watchdog.stop()
    try:
        await session.stop()
    except Exception:
        pass
    print(cc("\n  Bye", "m"))


if __name__ == "__main__":
    asyncio.run(main())