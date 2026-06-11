"""
测试 search_and_select_tree_item chain
========================================
流程：
  1. 启动浏览器 → 你登录 PowerApps 并打开 App
  2. 按回车 → 脚本自动：
     a. 点击左侧栏"树视图"tab
     b. 在搜索框输入关键词过滤控件
     c. 按名称点击目标控件选中它
  3. 浏览器保持打开，让你看到选中效果
  4. 按回车关闭浏览器

用法：
    uv run python .\Test\test_search_and_select_tree_item.py
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

from PowerfulApps.Agents.config.env import load_env_file

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("test_search_select")


async def main() -> None:
    from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio
    from PowerfulApps.MocProcess.chains.search_and_select_tree_item import (
        search_and_select_tree_item,
    )

    load_env_file(_PROJECT_ROOT / ".env")

    # ── 1. 启动浏览器 ──────────────────────────────────────────
    session = await create_browser_session(
        os.getenv("POWER_APPS_URL", "https://make.powerapps.com"),
        Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile"))),
    )

    from PowerfulApps.Browser.cdp.studio_cdp import reset_studio_cache
    reset_studio_cache()

    if not await prepare_studio(session):
        print("[WARN] Studio 未连接，请登录 https://make.powerapps.com 并打开 App 后按回车")
        input()
        reset_studio_cache()
        if not await prepare_studio(session):
            print("[X] 还是连不上，退出")
            await session.stop()
            return

    print("[OK] Studio 已连接")

    # ── 2. 用户输入关键词 ──────────────────────────────────────
    print()
    keyword = input("请输入要搜索的关键词（默认 TextInput）: ").strip() or "TextInput"
    target = input("请输入要点击的控件名称（默认搜索第一个）: ").strip() or None

    print(f"\n按回车 → 执行 search_and_select_tree_item(keyword='{keyword}', target_name={target})")
    input()

    # ── 3. 执行 chain ─────────────────────────────────────────
    print("=" * 60)
    print("执行中...")
    print("=" * 60)

    result = await search_and_select_tree_item(
        session,
        keyword=keyword,
        target_name=target,
    )

    # ── 4. 输出结果 ───────────────────────────────────────────
    print()
    print("=" * 60)
    if result.get("success"):
        print("  ✅ chain 执行成功！")
    else:
        print(f"  ❌ chain 执行失败: {result.get('error', '未知错误')}")
    print("=" * 60)

    print(f"\n  关键词:        {result['keyword']}")
    print(f"  目标控件:      {result['target']}")
    print(f"  搜索结果数:    {result.get('search_result', {}).get('found_count', 0)}")
    print(f"  点击是否成功:  {result.get('click_result', {}).get('success', False)}")

    if result.get("search_result", {}).get("items"):
        print(f"\n  搜索匹配项:")
        for item in result["search_result"]["items"][:10]:
            print(f"    - {item.get('name', '?')}")
        if len(result["search_result"]["items"]) > 10:
            print(f"    ... 还有 {len(result['search_result']['items']) - 10} 个")

    # ── 5. 保留浏览器 ─────────────────────────────────────────
    print("\n" + "=" * 60)
    print("浏览器已保持打开，去 PowerApps 看看控件是否被选中了吧！")
    print("按回车关闭浏览器...")
    await asyncio.to_thread(input)
    await session.stop()


if __name__ == "__main__":
    asyncio.run(main())