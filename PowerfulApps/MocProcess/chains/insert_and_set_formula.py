"""insert_and_set_formula.py — 插入组件并设置属性值的完整流程。

步骤：
  1. 点击 Ribbon 的"插入"按钮
  2. 等待菜单出现
  3. 在插入菜单中找到并点击指定控件
  4. 等待 PowerApps 加载
  5. 打开属性选择器下拉框
  6. 选中目标属性
  7. 在公式编辑器中写入公式

这是一个编排链，所有原子操作来自 MocProcess.actions。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from PowerfulApps.Browser.core import BrowserSession
from PowerfulApps.MocProcess.actions import insert_menu, formula_bar
from PowerfulApps.MocProcess.actions.click_funcInput import (
    ClickFuncInputParams,
    click_func_input,
)

logger = logging.getLogger(__name__)

load_dotenv()


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


# 各步骤间等待时间
_DELAY_AFTER_INSERT = _env_float("POWERAPPS_DELAY_AFTER_INSERT", 8.0)    # 插入控件后等待 PowerApps 加载
_DELAY_AFTER_RIBBON = _env_float("POWERAPPS_DELAY_AFTER_RIBBON", 1.5)    # 点击 Ribbon 后等菜单弹出
_DELAY_AFTER_SELECT = _env_float("POWERAPPS_DELAY_AFTER_SELECT", 0.5)    # 选择属性后等面板刷新
_DELAY_AFTER_TYPE = _env_float("POWERAPPS_DELAY_AFTER_TYPE", 0.3)        # 写入公式后


DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


async def insert_component_and_set_formula(
    session: BrowserSession,
    component: str = "按钮",
    property_name: str = "Text",
    formula: str = "点我一下",
    *,
    results_dir: str | Path | None = None,
) -> dict[str, Any]:
    """完整的插入组件并设置属性流程。

    Args:
        session: BrowserSession
        component: 要插入的控件名称，如 "按钮"、"文本输入"、"标签"
        property_name: 要设置的属性名，如 "Text"、"X"、"Y"、"Fill"
        formula: 要写入的公式/文本
        results_dir: 结果保存目录

    Returns:
        {success, component, propertyName, formula, steps: [{name, success}], reportPath}
    """
    steps: list[dict] = []
    report = {
        "component": component,
        "propertyName": property_name,
        "formula": formula,
        "steps": steps,
    }

    def _step(name: str, ok: bool, detail: str = "") -> None:
        steps.append({"name": name, "success": ok, "detail": detail})
        if ok:
            logger.info("  ✅ %s", name)
        else:
            logger.warning("  ✗ %s: %s", name, detail)

    # ── Step 1: 点击 Ribbon 插入 ──────────────────────────
    r1 = await insert_menu.click_ribbon_insert(session, "插入")
    _step("click_ribbon_insert", r1.get("success", False), r1.get("error", ""))
    if not r1.get("success"):
        # 尝试英文
        r1b = await insert_menu.click_ribbon_insert(session, "Insert")
        if r1b.get("success"):
            steps[-1]["success"] = True
            steps[-1]["detail"] = "used 'Insert' instead"
        else:
            report["success"] = False
            return report
    await asyncio.sleep(_DELAY_AFTER_RIBBON)

    # ── Step 2: 点击控件模板 ──────────────────────────────
    r2 = await insert_menu.click_insert_menu_item(session, component)
    _step(f"click_insert_menu_item({component})", r2.get("success", False), r2.get("error", ""))
    if not r2.get("success"):
        report["success"] = False
        return report
    await asyncio.sleep(_DELAY_AFTER_INSERT)

    # ── Step 3: 打开属性选择器 ────────────────────────────
    opts = await formula_bar.get_property_options(session)
    if not opts.get("found"):
        _step("get_property_options", False, opts.get("error", ""))
        report["success"] = False
        return report
    _step(f"get_property_options({opts.get('optionsCount', 0)} options)", True)

    # ── Step 4: 找到目标属性的索引 ────────────────────────
    all_options = opts.get("options", [])
    target_idx = -1
    for i, opt in enumerate(all_options):
        if opt.get("text", "").strip() == property_name:
            target_idx = i
            break
    if target_idx < 0:
        _step(f"select_property({property_name})", False, f"property '{property_name}' not found in dropdown")
        report["success"] = False
        return report
    await asyncio.sleep(_DELAY_AFTER_SELECT)

    # ── Step 5: 选择属性 ──────────────────────────────────
    r5 = await formula_bar.select_property_option(session, target_idx)
    _step(f"select_property({property_name})", r5.get("found", False), r5.get("error", ""))
    if not r5.get("found"):
        report["success"] = False
        return report
    await asyncio.sleep(_DELAY_AFTER_SELECT)

    # ── Step 6: 写入公式 ──────────────────────────────────
    # 必须通过 Monaco 专用输入逻辑聚焦公式编辑器，再用 CDP insertText 写入；
    # 不能直接给 DOM input/textarea 赋值，否则容易写到属性输入框。
    r6 = await click_func_input(
        ClickFuncInputParams(text=formula, clear_existing=True),
        session,
    )
    _step(f"type_formula({formula})", not bool(r6.error), r6.error or "")
    if r6.error:
        report["success"] = False
        return report
    await asyncio.sleep(_DELAY_AFTER_TYPE)

    report["success"] = True

    # 保存报告
    out_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"insert_{component}_{property_name}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["reportPath"] = str(report_path)
    logger.info("报告已保存：%s", report_path)

    return report


async def scan_insert_menu(session: BrowserSession) -> dict:
    """扫描插入菜单的所有控件（用于探索/学习）。

    Returns:
        {found, count, items: [{text, rect}], categoriesExpanded}
    """
    r1 = await insert_menu.click_ribbon_insert(session, "插入")
    if not r1.get("success"):
        r1 = await insert_menu.click_ribbon_insert(session, "Insert")
    if not r1.get("success"):
        return {"found": False, "error": "cannot click insert button"}
    await asyncio.sleep(1.5)

    menu = await insert_menu.get_insert_menu_items(session)
    return menu


async def traverse_all_properties(session: BrowserSession) -> dict:
    """获取当前选中控件的所有属性选项并收集面板数据。

    Returns:
        {success, optionsCount, properties: {optionText: {formulaValue, ...}}}
    """
    opts = await formula_bar.get_property_options(session)
    if not opts.get("found"):
        return {"success": False, "error": opts.get("error", "get options failed")}

    all_options = opts.get("options", [])
    total = opts.get("optionsCount", 0)
    properties = {}
    for idx in range(total):
        opt_text = all_options[idx].get("text", f"<{idx}>")[:50]
        r = await formula_bar.select_property_option(session, idx)
        if r.get("found"):
            panel = r.get("panelData", {})
            properties[opt_text] = {
                "formulaValue": panel.get("formulaValue", ""),
                "inputs": len(panel.get("propertyInputs", [])),
                "labels": len(panel.get("propertyLabels", [])),
            }
        await asyncio.sleep(0.3)

    return {
        "success": True,
        "optionsCount": total,
        "properties": properties,
    }