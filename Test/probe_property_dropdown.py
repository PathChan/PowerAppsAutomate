"""探针 v4（自动版）：自动点击属性选择器下拉框 → 遍历所有属性选项 → 持久化存储。

相比 v3 的核心变化
-------------------
- 自动点击第一层按钮（属性选择器下拉框），无需用户额外干预
- 自动遍历更深一层的按钮（下拉选项），逐个点击并收集属性编辑器内容
- 所有结果缓存聚合，持久化存储到 JSON/TXT
- 用户只需登录 + 在画布上选中控件，其余全自动
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mocProcessing import BrowserSession
from mocProcessing.tools.powerapps_chain import (
    _ensure_studio_context,
    execute_in_studio,
)


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
log = logging.getLogger("probe_v4_auto")

OUT_DIR = _PROJECT_ROOT / "Test" / "probe_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# 探针 1: 页面基本信息
# ═══════════════════════════════════════════════════════════════
PROBE_PAGE_INFO_JS = r"""
(() => {
    return {
        title: document.title,
        url: location.href,
        bodyClass: (document.body.className || '').slice(0, 200),
        readyState: document.readyState,
    };
})()
"""

# ═══════════════════════════════════════════════════════════════
# 探针 2: formulaBarContainer 全貌
# ═══════════════════════════════════════════════════════════════
PROBE_FORMULA_BAR_JS = r"""
(() => {
    const container = document.querySelector('#formulaBarContainer');
    if (!container) return {found: false};
    const r = container.getBoundingClientRect();
    return {
        found: true,
        rect: {x: r.x, y: r.y, w: r.width, h: r.height},
        childCount: container.children.length,
        tagNames: Array.from(container.children).map(c => c.tagName + (c.id ? '#' + c.id : '')),
        innerHTML: container.innerHTML.slice(0, 3000),
    };
})()
"""

# ═══════════════════════════════════════════════════════════════
# 探针 3: 打开属性选择器下拉框，获取所有选项列表（第一层按钮）
#
# 执行流程：
#   1. 在 formulaBarContainer 中找到下拉触发器（button / [role=combobox]）
#   2. 点击打开下拉框
#   3. 枚举所有可见的 [role="option"] 元素
#   4. 返回选项列表 + 触发器信息
# ═══════════════════════════════════════════════════════════════
PROBE_GET_OPTIONS_JS = r"""
(async () => {
    try {
        const container = document.querySelector('#formulaBarContainer');
        if (!container) return {found: false, error: 'no #formulaBarContainer'};

        // 找下拉触发器——第一个可见的 button / [role=combobox] / select
        const candidates = container.querySelectorAll(
            'button, [role="combobox"], [role="listbox"], select'
        );
        let trigger = null;
        for (const c of candidates) {
            const r = c.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) { trigger = c; break; }
        }
        if (!trigger) return {found: false, error: 'no visible trigger in container'};

        // 用 dispatchEvent 触发（Fabric UI 依赖完整事件链，原生 .click() 可能抛异常）
        const tr = trigger.getBoundingClientRect();
        const opts = {bubbles: true, cancelable: true, view: window};
        trigger.dispatchEvent(new PointerEvent('pointerover', opts));
        trigger.dispatchEvent(new PointerEvent('pointerdown', opts));
        trigger.dispatchEvent(new MouseEvent('mousedown', opts));
        trigger.click();
        trigger.dispatchEvent(new PointerEvent('pointerup', opts));
        trigger.dispatchEvent(new MouseEvent('mouseup', opts));
        await new Promise(r => setTimeout(r, 500));

        // 搜集所有可见选项
        const optionEls = document.querySelectorAll(
            '[role="option"], [role="listbox"] [role="option"], '
            '.ms-Dropdown-item, [class*="dropdown"] li, '
            '[class*="menu"] [role="menuitem"], '
            '.ms-ContextualMenu-item, [class*="ContextualMenu"] li'
        );
        const options = [];
        optionEls.forEach(opt => {
            const r2 = opt.getBoundingClientRect();
            if (r2.width <= 0 || r2.height <= 0) return;
            options.push({
                text: (opt.textContent || '').trim().slice(0, 120),
                tag: opt.tagName,
                role: opt.getAttribute('role') || '',
                selected: opt.getAttribute('aria-selected') || '',
                rect: {x: r2.x, y: r2.y, w: r2.width, h: r2.height},
            });
        });

        return {
            found: true,
            trigger: {
                tag: trigger.tagName,
                id: trigger.id,
                className: (trigger.className || '').slice(0, 200),
                role: trigger.getAttribute('role') || '',
                ariaLabel: trigger.getAttribute('aria-label') || '',
                ariaHasPopup: trigger.getAttribute('aria-haspopup') || '',
                dataAutomationId: trigger.getAttribute('data-automationid') || '',
                dataControlName: trigger.getAttribute('data-control-name') || '',
                innerText: (trigger.innerText || '').trim().slice(0, 100),
                rect: {x: tr.x, y: tr.y, w: tr.width, h: tr.height},
            },
            optionsCount: options.length,
            options: options,
        };
    } catch (e) {
        return {found: false, error: 'JS exception: ' + (e.message || e)};
    }
})()
"""

# ═══════════════════════════════════════════════════════════════
# 探针 4: 选择第 N 个选项 + 收集该属性下的编辑器内容（更深一层按钮）
#
# 参数：{index} — 选项在 PROBE_GET_OPTIONS_JS 返回列表中的索引
#        {text}  — 选项文本（用于日志）
#
# 执行流程：
#   1. 重新打开下拉框
#   2. 通过索引找到目标选项并点击
#   3. 等待属性面板刷新
#   4. 收集 formulaBar 当前内容 + 属性面板所有输入/标签
#   5. 返回数据
# ═══════════════════════════════════════════════════════════════
def _build_select_and_collect_js(index: int) -> str:
    return f"""
(async () => {{
    try {{
        const container = document.querySelector('#formulaBarContainer');
        if (!container) return {{found: false, error: 'no container'}};

        // 重新打开下拉框
        const candidates = container.querySelectorAll(
            'button, [role="combobox"], [role="listbox"], select'
        );
        let trigger = null;
        for (const c of candidates) {{
            const r = c.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {{ trigger = c; break; }}
        }}
        if (!trigger) return {{found: false, error: 'no trigger'}};

        const opts = {{bubbles: true, cancelable: true, view: window}};
        trigger.dispatchEvent(new PointerEvent('pointerover', opts));
        trigger.dispatchEvent(new PointerEvent('pointerdown', opts));
        trigger.dispatchEvent(new MouseEvent('mousedown', opts));
        trigger.click();
        trigger.dispatchEvent(new PointerEvent('pointerup', opts));
        trigger.dispatchEvent(new MouseEvent('mouseup', opts));
        await new Promise(r => setTimeout(r, 500));

        // 按 index 找选项
        const optionEls = document.querySelectorAll(
            '[role="option"], [role="listbox"] [role="option"], '
            '.ms-Dropdown-item, [class*="dropdown"] li, '
            '[class*="menu"] [role="menuitem"], '
            '.ms-ContextualMenu-item, [class*="ContextualMenu"] li'
        );
        // 过滤掉不可见的
        const visible = [];
        optionEls.forEach(opt => {{
            const r2 = opt.getBoundingClientRect();
            if (r2.width > 0 && r2.height > 0) visible.push(opt);
        }});

        if (!visible[{index}]) return {{found: false, error: 'option index {index} not visible'}};

        const target = visible[{index}];
        const targetText = (target.textContent || '').trim().slice(0, 120);
        const targetRect = target.getBoundingClientRect();

        // 用原生事件点击选项
        target.dispatchEvent(new PointerEvent('pointerover', opts));
        target.dispatchEvent(new PointerEvent('pointerdown', opts));
        target.dispatchEvent(new MouseEvent('mousedown', opts));
        target.click();
        target.dispatchEvent(new PointerEvent('pointerup', opts));
        target.dispatchEvent(new MouseEvent('mouseup', opts));

        // 等待属性面板刷新
        await new Promise(r => setTimeout(r, 800));

        // ── 收集当前属性编辑器内容 ──────────────────────────
        const panelData = {{}};

        // formulaBar 中的当前公式/值
        const formulaInput = container.querySelector(
            'input, textarea, [role="textbox"], [contenteditable="true"]'
        );
        if (formulaInput) {{
            panelData.formulaValue = formulaInput.value || formulaInput.textContent || '';
        }}

        // 属性面板中的所有输入框
        const inputs = document.querySelectorAll(
            '.property-pane input, .property-editor input, '
            '[class*="property"] input:not([type="hidden"]):not([type="password"]), '
            '.property-pane textarea, .property-editor textarea'
        );
        const inputList = [];
        inputs.forEach(inp => {{
            const r3 = inp.getBoundingClientRect();
            if (r3.width <= 0 || r3.height <= 0) return;
            inputList.push({{
                placeholder: inp.placeholder || '',
                ariaLabel: inp.getAttribute('aria-label') || '',
                value: (inp.value || '').slice(0, 200),
                dataAutomationId: inp.getAttribute('data-automationid') || '',
                rect: {{x: r3.x, y: r3.y, w: r3.width, h: r3.height}},
            }});
        }});
        panelData.propertyInputs = inputList;

        // 属性面板中的所有标签
        const labels = document.querySelectorAll(
            '.property-pane label, .property-editor label, '
            '[class*="property"] label, [class*="editor"] label, '
            '.property-pane [class*="label"], .property-editor [class*="label"]'
        );
        const labelList = [];
        labels.forEach(lbl => {{
            const text = (lbl.textContent || '').trim();
            if (!text) return;
            labelList.push(text.slice(0, 100));
        }});
        panelData.propertyLabels = labelList;

        // 属性面板中的按钮/下拉框
        const buttons = document.querySelectorAll(
            '.property-pane button, .property-editor button, '
            '[class*="property"] button, [class*="editor"] button'
        );
        const btnList = [];
        buttons.forEach(btn => {{
            const r4 = btn.getBoundingClientRect();
            if (r4.width <= 0 || r4.height <= 0) return;
            btnList.push({{
                text: (btn.textContent || '').trim().slice(0, 80),
                ariaLabel: btn.getAttribute('aria-label') || '',
                role: btn.getAttribute('role') || '',
                rect: {{x: r4.x, y: r4.y, w: r4.width, h: r4.height}},
            }});
        }});
        panelData.propertyButtons = btnList;

        return {{
            found: true,
            index: {index},
            optionText: targetText,
            optionRect: {{x: targetRect.x, y: targetRect.y, w: targetRect.width, h: targetRect.height}},
            panelData: panelData,
        }};
    }} catch (e) {{
        return {{found: false, error: 'JS exception: ' + (e.message || e)}};
    }}
}})()
"""

# ═══════════════════════════════════════════════════════════════
# 探针 5: 收集当前公式栏内容（作为 fallback / 额外数据）
# ═══════════════════════════════════════════════════════════════
PROBE_CURRENT_FORMULA_JS = r"""
(() => {
    const container = document.querySelector('#formulaBarContainer');
    if (!container) return {found: false};
    const inp = container.querySelector('input, textarea, [role="textbox"], [contenteditable="true"]');
    return {
        found: !!inp,
        value: inp ? (inp.value || inp.textContent || '') : '',
        tag: inp ? inp.tagName : null,
    };
})()
"""


async def main() -> None:
    power_apps_url = os.getenv("POWER_APPS_URL", "").strip()
    if not power_apps_url:
        raise RuntimeError("POWER_APPS_URL not set; configure it in .env")

    user_data_env = os.getenv("BROWSER_USE_USER_DATA_DIR", "").strip()
    user_data_dir = (
        (Path(user_data_env) if Path(user_data_env).is_absolute() else _PROJECT_ROOT / user_data_env)
        if user_data_env else (_PROJECT_ROOT / ".chrome-profile-test")
    ).resolve()
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

    log.info("=" * 60)
    log.info("1. Sign in if needed")
    log.info("2. Open any app to Studio editor")
    log.info("3. SELECT ANY CONTROL on the canvas")
    log.info("4. Press Enter to probe")
    log.info("=" * 60)
    log.info("(After this, everything is automatic — no more clicks needed)")
    await asyncio.to_thread(input, "Ready? Press Enter after you selected a control...")

    # ══════════════════════════════════════════════════════════
    # CDP 恢复 + Studio iframe 连接（整体重试循环）
    # WebSocket 可能在 createIsolatedWorld 等重型 CDP 调用时再次断开，
    # 因此需要把稳定性检查 + 缓存重置 + iframe 连接整个包进重试。
    # ══════════════════════════════════════════════════════════
    from mocProcessing.tools.powerapps_chain import reset_studio_cache as _reset_studio_cache

    ctx = None
    _connect_error = None
    for connect_attempt in range(1, 11):
        if connect_attempt > 1:
            log.info("Retrying studio connection (attempt %d/10)...", connect_attempt)
            await asyncio.sleep(2.0)

        # 1) CDP 稳定性等待
        _cdp_ok = False
        for stab_attempt in range(1, 6):
            try:
                cdp_session = await session.get_or_create_cdp_session()
                await cdp_session.cdp_client.send.Runtime.evaluate(
                    params={"expression": "document.readyState", "returnByValue": True},
                    session_id=cdp_session.session_id,
                )
                _cdp_ok = True
                break
            except Exception as e:
                log.info("  CDP stabilize (%d/5): %s", stab_attempt, e)
                await asyncio.sleep(1.5)

        if not _cdp_ok:
            _connect_error = "CDP did not stabilize after 5 attempts"
            log.warning("  %s", _connect_error)
            continue

        # 2) 清除缓存 + 连接 Studio iframe
        _reset_studio_cache()
        log.info("Studio context cache cleared.")

        try:
            log.info("Connecting to EmbeddedStudio iframe via CDP...")
            ctx = await _ensure_studio_context(session)
            if ctx.get("error"):
                _connect_error = ctx["error"]
                log.warning("  _ensure_studio_context error: %s", _connect_error)
                ctx = None
                continue
            log.info("EmbeddedStudio frameId=%s", ctx.get("frameId")[:16])
            _connect_error = None
            break  # 成功！
        except Exception as e:
            _connect_error = str(e)
            log.warning("  _ensure_studio_context exception: %s", _connect_error)
            ctx = None
            continue

    if not ctx:
        log.error("Failed to connect to Studio iframe after 10 attempts: %s", _connect_error)
        await asyncio.sleep(10)
        await session.stop()
        return

    # ── 报告容器 ──────────────────────────────────────────────
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "frame_id": ctx.get("frameId"),
        "data": {},
    }

    # ══════════════════════════════════════════════════════════
    # Step 1: 页面基本信息 + formulaBarContainer
    # ══════════════════════════════════════════════════════════
    log.info("[1/5] Probing page info...")
    raw = await execute_in_studio(session, PROBE_PAGE_INFO_JS)
    report["data"]["page_info"] = (raw.get("result") or {}).get("value") or {}
    log.info("  title=%s", report["data"]["page_info"].get("title", ""))

    log.info("[2/5] Probing #formulaBarContainer...")
    raw = await execute_in_studio(session, PROBE_FORMULA_BAR_JS)
    report["data"]["formulaBar"] = (raw.get("result") or {}).get("value") or {}
    if report["data"]["formulaBar"].get("found"):
        log.info("  FOUND! rect=%s children=%d",
                 report["data"]["formulaBar"].get("rect"),
                 report["data"]["formulaBar"].get("childCount"))
    else:
        log.warning("  NOT FOUND — aborting.")
        await session.stop()
        return

    # ══════════════════════════════════════════════════════════
    # Step 2: 自动点击第一层按钮（属性选择器下拉框），获取选项列表
    # ══════════════════════════════════════════════════════════
    log.info("[3/5] Auto-clicking property selector dropdown (first-layer button)...")
    raw = await execute_in_studio(session, PROBE_GET_OPTIONS_JS)
    if raw.get("exceptionDetails"):
        log.error("  JS exception: %s", raw["exceptionDetails"].get("text", raw["exceptionDetails"]))
        await session.stop()
        return
    options_data = (raw.get("result") or {}).get("value") or {}
    report["data"]["dropdown_info"] = options_data

    if not options_data.get("found"):
        log.error("  Failed to open dropdown: %s", options_data.get("error", "unknown"))
        await session.stop()
        return

    trigger_info = options_data.get("trigger", {})
    log.info("  Trigger: %s | %s", trigger_info.get("tag"), trigger_info.get("ariaLabel") or trigger_info.get("innerText", ""))
    log.info("  Found %d options", options_data.get("optionsCount", 0))

    options = options_data.get("options", [])
    for i, opt in enumerate(options):
        sel = " [SELECTED]" if opt.get("selected") == "true" else ""
        log.info("    [%d] %s%s", i, opt.get("text", "")[:60], sel)

    # ══════════════════════════════════════════════════════════
    # Step 3: 遍历所有选项（更深一层按钮），逐个点击并收集属性编辑器内容
    # ══════════════════════════════════════════════════════════
    log.info("[4/5] Auto-clicking each option (deeper buttons) and collecting property data...")
    all_properties = {}
    max_options = options_data.get("optionsCount", 0)

    for idx in range(max_options):
        opt_text = options[idx].get("text", f"<index {idx}>")[:50]
        log.info("  --- Option [%d/%d]: %s ---", idx + 1, max_options, opt_text)

        js_code = _build_select_and_collect_js(idx)
        raw = await execute_in_studio(session, js_code)
        if raw.get("exceptionDetails"):
            log.warning("    ✗ JS exception: %s", raw["exceptionDetails"].get("text", raw["exceptionDetails"]))
            all_properties[opt_text] = {"error": str(raw["exceptionDetails"])}
            continue
        result = (raw.get("result") or {}).get("value") or {}

        if result.get("found"):
            panel = result.get("panelData", {})
            formula_val = panel.get("formulaValue", "")
            inputs_count = len(panel.get("propertyInputs", []))
            labels_count = len(panel.get("propertyLabels", []))
            buttons_count = len(panel.get("propertyButtons", []))
            log.info("    ✓ formula=%s inputs=%d labels=%d buttons=%d",
                     (formula_val or "(empty)")[:40], inputs_count, labels_count, buttons_count)
            all_properties[opt_text] = {
                "optionText": result.get("optionText", ""),
                "optionRect": result.get("optionRect"),
                "panelData": panel,
            }
        else:
            err = result.get("error", "unknown error")
            log.warning("    ✗ FAILED: %s", err)
            all_properties[opt_text] = {"error": err}

        # 选项间短暂停顿让 UI 消化
        await asyncio.sleep(0.3)

    report["data"]["all_properties"] = all_properties
    report["data"]["properties_count"] = len(all_properties)
    log.info("  Collected %d / %d properties", len(all_properties), max_options)

    # ══════════════════════════════════════════════════════════
    # Step 4: 持久化存储 — JSON + TXT
    # ══════════════════════════════════════════════════════════
    log.info("[5/5] Persisting results...")

    json_path = OUT_DIR / "probe_v4_all_properties.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    txt_path = OUT_DIR / "probe_v4_report.txt"
    with txt_path.open("w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write("PowerApps DOM Probe v4 (auto-click all properties inside EmbeddedStudio)\n")
        f.write(f"Timestamp: {report['timestamp']}\n")
        f.write(f"frameId: {report['frame_id'][:16] if report.get('frame_id') else 'N/A'}\n")
        f.write("=" * 70 + "\n\n")

        pi = report["data"].get("page_info", {})
        f.write(f"Page title: {pi.get('title', 'N/A')}\n")
        f.write(f"Page url:   {pi.get('url', 'N/A')}\n\n")

        fb = report["data"].get("formulaBar", {})
        f.write("--- #formulaBarContainer ---\n")
        if fb.get("found"):
            f.write(f"  rect: {fb.get('rect')}\n")
            f.write(f"  childCount: {fb.get('childCount')}\n")
            for t in fb.get("tagNames", []):
                f.write(f"    - {t}\n")
        else:
            f.write("  NOT FOUND\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("PROPERTY SELECTOR DROPDOWN (auto-clicked)\n")
        f.write("=" * 70 + "\n")
        di = report["data"].get("dropdown_info", {})
        if di.get("found"):
            trig = di.get("trigger", {})
            f.write(f"Trigger: {trig.get('tag')} id={trig.get('id','')} "
                    f"aria-label={trig.get('ariaLabel','')}\n")
            f.write(f"  data-automationid={trig.get('dataAutomationId','')}\n")
            f.write(f"  data-control-name={trig.get('dataControlName','')}\n")
            f.write(f"  rect={trig.get('rect')}\n")
            f.write(f"\nAll {di.get('optionsCount', 0)} options:\n")
            for i, opt in enumerate(di.get("options", [])):
                sel = "  [SELECTED]" if opt.get("selected") == "true" else ""
                f.write(f"  [{i}] {opt.get('text','')}{sel}\n")
        else:
            f.write(f"  FAILED: {di.get('error', 'unknown')}\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("PER-PROPERTY PANEL DATA\n")
        f.write("=" * 70 + "\n")
        for prop_name, prop_data in report["data"].get("all_properties", {}).items():
            f.write(f"\n--- {prop_name} ---\n")
            if "error" in prop_data:
                f.write(f"  ERROR: {prop_data['error']}\n")
                continue
            panel = prop_data.get("panelData", {})
            f.write(f"  Formula value: {panel.get('formulaValue', '(empty)')}\n")
            f.write(f"  Inputs ({len(panel.get('propertyInputs', []))}):\n")
            for inp in panel.get("propertyInputs", []):
                f.write(f"    placeholder={inp.get('placeholder','')} "
                        f"value={inp.get('value','')}\n")
            f.write(f"  Labels: {panel.get('propertyLabels', [])}\n")
            f.write(f"  Buttons ({len(panel.get('propertyButtons', []))}):\n")
            for btn in panel.get("propertyButtons", []):
                f.write(f"    text={btn.get('text','')} "
                        f"aria-label={btn.get('ariaLabel','')}\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write(f"Total properties collected: {report['data'].get('properties_count', 0)}\n")
        f.write("=" * 70 + "\n")

    log.info("=" * 70)
    log.info("RESULTS SAVED:")
    log.info("  JSON: %s", json_path)
    log.info("  TXT:  %s", txt_path)
    log.info("  Properties: %d / %d",
             report["data"].get("properties_count", 0),
             options_data.get("optionsCount", 0))
    log.info("=" * 70)
    log.info("Browser stays open 30s for inspection, then auto-closes.")

    await asyncio.sleep(30)
    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")