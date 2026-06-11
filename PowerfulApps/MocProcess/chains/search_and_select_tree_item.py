"""search_and_select_tree_item.py — 在 Tree View 中搜索并点击选中控件。

流程编排（chain）：
  1. 确保左侧栏"树视图"tab 已打开
  2. 在搜索框输入关键词过滤控件
  3. 按名称点击目标控件将其选中

适用于：
  - 不知道控件在树中的精确位置，但知道名称
  - 树中控件太多，不想逐层展开
  - 定位某个控件后进行后续操作（设置属性等）
"""
from __future__ import annotations

import json
import logging
from typing import Any

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.MocProcess.actions.click_sidebar_tab import click_sidebar_tab
from PowerfulApps.MocProcess.actions.search_element import search_in_tree_view, click_tree_item

logger = logging.getLogger(__name__)


async def search_and_select_tree_item(
    session: BrowserSession,
    keyword: str,
    target_name: str | None = None,
    *,
    sidebar_label: str = "树视图",
    ensure_sidebar_open: bool = True,
) -> dict[str, Any]:
    """在 Tree View 中搜索控件并点击选中。

    流程：
      1. （可选）点击左侧栏 tab 确保 Tree View 可见
      2. 在搜索框输入 keyword 过滤
      3. 按 target_name 点击匹配的控件（默认同 keyword）

    Args:
        session: Browser 会话
        keyword: 搜索关键词，如 "Button"、"Text"、"Screen1"
        target_name: 要点击的控件名称。为 None 时等同于 keyword
        sidebar_label: 左侧栏 tab 标签，默认 "树视图"
        ensure_sidebar_open: 是否先确保左侧栏 tab 打开，默认 True

    Returns:
        {
            "success": bool,
            "keyword": str,
            "target": str,
            "search_result": dict | None,
            "click_result": dict | None,
            "error"?: str,
        }
    """
    target = target_name or keyword
    result: dict[str, Any] = {
        "success": False,
        "keyword": keyword,
        "target": target,
        "search_result": None,
        "click_result": None,
    }

    # Step 1: 打开左侧栏 tab
    if ensure_sidebar_open:
        tab_result = await click_sidebar_tab(session, sidebar_label)
        if not tab_result.get("success"):
            result["error"] = f"左侧栏 tab '{sidebar_label}' 点击失败: {tab_result.get('error', '未知错误')}"
            logger.warning(result["error"])
            return result
        logger.info("左侧栏 tab '%s' 已激活", sidebar_label)

    # Step 2: 搜索
    search_result = await search_in_tree_view(session, keyword)
    result["search_result"] = search_result

    if search_result.get("error"):
        result["error"] = f"搜索失败: {search_result['error']}"
        logger.warning(result["error"])
        return result

    found_count = search_result.get("found_count", 0)
    if found_count == 0:
        result["error"] = f"未找到匹配 '{keyword}' 的控件"
        logger.warning(result["error"])
        return result

    logger.info("搜索 '%s' 找到 %d 个匹配", keyword, found_count)

    # Step 3: 点击目标控件
    click_result = await click_tree_item(session, target)
    result["click_result"] = click_result

    if click_result.get("success"):
        result["success"] = True
        logger.info("成功选中控件 '%s'", target)
    else:
        result["error"] = f"点击控件 '{target}' 失败: {click_result.get('error', '未知错误')}"
        logger.warning(result["error"])

    return result


# ── 便捷函数：仅搜索 + 点击，不打开侧栏（假设已经打开）───


async def search_and_select_in_current_tree(
    session: BrowserSession,
    keyword: str,
    target_name: str | None = None,
) -> dict[str, Any]:
    """在当前已打开的 Tree View 中搜索并点击选中（不操作侧栏 tab）。

    等效于 search_and_select_tree_item(..., ensure_sidebar_open=False)。
    """
    return await search_and_select_tree_item(
        session, keyword, target_name, ensure_sidebar_open=False,
    )