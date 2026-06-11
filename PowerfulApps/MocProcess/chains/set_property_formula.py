"""set_property_formula.py — 修改当前选中控件的某个属性公式。

用于不插入新控件，只在当前已选中控件上：
  1. 打开属性选择器
  2. 选择目标属性
  3. 在公式编辑器写入公式
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
from PowerfulApps.MocProcess.actions import formula_bar
from PowerfulApps.MocProcess.actions.click_funcInput import (
    ClickFuncInputParams,
    click_func_input,
)

logger = logging.getLogger(__name__)

load_dotenv()


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    return float(value) if value else default


_DELAY_AFTER_SELECT = _env_float("POWERAPPS_DELAY_AFTER_SELECT", 0.5)
_DELAY_AFTER_TYPE = _env_float("POWERAPPS_DELAY_AFTER_TYPE", 0.3)

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


async def set_property_formula(
    session: BrowserSession,
    property_name: str,
    formula: str,
    *,
    results_dir: str | Path | None = None,
) -> dict[str, Any]:
    """给当前选中的控件设置某个属性公式。"""
    steps: list[dict] = []
    report = {
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

    opts = await formula_bar.get_property_options(session)
    if not opts.get("found"):
        _step("get_property_options", False, opts.get("error", ""))
        report["success"] = False
        return report
    _step(f"get_property_options({opts.get('optionsCount', 0)} options)", True)

    target_idx = -1
    for i, opt in enumerate(opts.get("options", [])):
        if opt.get("text", "").strip() == property_name:
            target_idx = i
            break
    if target_idx < 0:
        _step(f"select_property({property_name})", False, f"property '{property_name}' not found in dropdown")
        report["success"] = False
        return report

    await asyncio.sleep(_DELAY_AFTER_SELECT)
    selected = await formula_bar.select_property_option(session, target_idx)
    _step(f"select_property({property_name})", selected.get("found", False), selected.get("error", ""))
    if not selected.get("found"):
        report["success"] = False
        return report

    await asyncio.sleep(_DELAY_AFTER_SELECT)
    typed = await click_func_input(
        ClickFuncInputParams(text=formula, clear_existing=True),
        session,
    )
    _step(f"type_formula({formula})", not bool(typed.error), typed.error or "")
    if typed.error:
        report["success"] = False
        return report

    await asyncio.sleep(_DELAY_AFTER_TYPE)
    report["success"] = True

    out_dir = Path(results_dir) if results_dir else DEFAULT_RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = property_name.replace("/", "_").replace("\\", "_")
    report_path = out_dir / f"set_property_{safe_name}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["reportPath"] = str(report_path)
    logger.info("报告已保存：%s", report_path)

    return report
