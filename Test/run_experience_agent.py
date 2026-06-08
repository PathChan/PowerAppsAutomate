"""经验 Agent：自动探索 PowerApps Studio DOM → 学习 → 点击 → 持久化。

用户只需登录 + 在画布上选中控件，后续全自动：
1. CDP 稳定性恢复
2. 连接 Studio iframe
3. 自动探索 formulaBar / 属性面板 / 功能区 等区域的所有按钮
4. 提取每个元素的 10+ 维特征（data-automationid、文本、DOM 路径、坐标等）
5. 持久化到 .cache/powerapps/experience.json
6. 尝试点击属性选择器下拉框
7. 下次运行直接复用经验，无需重新探索

运行方式
--------
    uv run python .\Test\run_experience_agent.py

第一次运行：手动登录 → 打开 App → 选中控件 → 按 Enter → 自动学习
第二次运行：如果经验已存在，直接按经验点击，无需重新探索
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

from mocProcessing import BrowserSession
from mocProcessing.tools.experience import ExperienceDB, ExperienceEngine
from mocProcessing.tools.powerapps_chain import (
    _ensure_studio_context,
    execute_in_studio,
    reset_studio_cache,
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
    format="%(asctime)s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("experience_agent")


async def _ensure_cdp_stable(session: BrowserSession) -> bool:
    """CDP 稳定性等待（最多 30 秒）。"""
    for attempt in range(1, 11):
        try:
            cdp_s = await session.get_or_create_cdp_session()
            await cdp_s.cdp_client.send.Runtime.evaluate(
                params={"expression": "document.readyState", "returnByValue": True},
                session_id=cdp_s.session_id,
            )
            log.info("CDP stable (attempt %d/10)", attempt)
            return True
        except Exception as e:
            log.info("  Waiting for CDP (%d/10): %s", attempt, e)
            await asyncio.sleep(1.5)
    return False


async def main() -> None:
    power_apps_url = os.getenv("POWER_APPS_URL", "").strip()
    if not power_apps_url:
        log.error("POWER_APPS_URL not set in .env")
        return

    user_data_dir = (
        Path(os.getenv("BROWSER_USE_USER_DATA_DIR", ".chrome-profile-test"))
    )
    if not user_data_dir.is_absolute():
        user_data_dir = (_PROJECT_ROOT / user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # ── 加载经验库 ──────────────────────────────────────────
    db = ExperienceDB()
    stats = db.get_stats()
    log.info("Experience DB: %d elements, avg confidence=%.2f",
             stats["total_elements"], stats.get("avg_confidence", 0))
    if stats["total_elements"] > 0:
        log.info("  Areas: %s", stats.get("areas", []))
        log.info("  Total usages: %d, successes: %d",
                 stats.get("total_usage", 0), stats.get("total_success", 0))

    # ── 启动浏览器 ──────────────────────────────────────────
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
    await asyncio.to_thread(input, "Ready? Press Enter after you selected a control...")

    # ── CDP 恢复 ──────────────────────────────────────────
    log.info("Waiting for CDP stability...")
    if not await _ensure_cdp_stable(session):
        log.error("CDP not stable, aborting.")
        await session.stop()
        return
    reset_studio_cache()

    # ── 找回正确的标签页 ──────────────────────────────────
    # SessionManager 在 CDP 重连后可能切到了非 PowerApps 标签页
    try:
        tabs = await session.get_tabs()
        log.info("Available tabs: %d", len(tabs))
        for t in tabs:
            snippet = (t.url or "")[:80] if hasattr(t, "url") and t.url else (t.target_id or "")
            log.info("  ...%s  %s", t.target_id[-6:], snippet)
        # 找 make.powerapps 的标签页
        for t in tabs:
            url = (t.url or "").lower()
            if "make.powerapps" in url or "authoring" in url or "powerapps" in url:
                try:
                    cdp_s = await session.get_or_create_cdp_session()
                    if cdp_s.target_id != t.target_id:
                        from mocProcessing.browser.events import SwitchTabEvent
                        ev = session.event_bus.dispatch(SwitchTabEvent(target_id=t.target_id))
                        await ev
                        await ev.event_result(raise_if_any=False, raise_if_none=False)
                        await asyncio.sleep(1.0)
                        log.info("Switched to PowerApps tab ...%s", t.target_id[-6:])
                except Exception as e:
                    log.warning("Tab switch failed: %s", e)
                break
    except Exception as e:
        log.warning("Tab detection: %s", e)

    # ── 连接 Studio iframe ──────────────────────────────────
    ctx = None
    for attempt in range(1, 8):
        if attempt > 1:
            await asyncio.sleep(2.0)
            reset_studio_cache()
        try:
            ctx = await _ensure_studio_context(session)
            if ctx and not ctx.get("error"):
                break
        except Exception as e:
            log.warning("Studio connect attempt %d: %s", attempt, e)
    if not ctx or ctx.get("error"):
        log.error("Cannot connect to Studio iframe: %s", ctx)
        await session.stop()
        return
    log.info("EmbeddedStudio frameId=%s", ctx.get("frameId")[:16])

    # ── 创建经验引擎 ──────────────────────────────────────
    engine = ExperienceEngine(session, db)

    # ══════════════════════════════════════════════════════
    # 探索模式：自动学习 DOM 元素
    # ══════════════════════════════════════════════════════
    log.info("[探索] Starting DOM auto-learning...")
    counts = await engine.learn_all()
    total = sum(counts.values())
    log.info("[探索] Learned %d elements across areas: %s", total, counts)

    # 打印学到了什么
    all_exp = db.list_all()
    if all_exp:
        log.info("[探索] Element list:")
        for e in sorted(all_exp, key=lambda x: x.area_hint):
            feat = e.features
            label = (
                feat.get("text", "") or
                feat.get("aria_label", "") or
                feat.get("data_automationid", "") or
                e.key
            )
            tag = feat.get("tag", "?")
            role = feat.get("role", "")
            log.info("  [%-12s] %-6s %-12s %s",
                     e.area_hint, tag, f"({role})", label[:60])

    # ══════════════════════════════════════════════════════
    # 尝试点击属性选择器下拉框
    # ══════════════════════════════════════════════════════
    log.info("[操作] Trying to click property selector dropdown...")

    # 策略 1: 用经验库匹配 "formulaBar" 区域的 button/combobox
    click_result = None
    formula_bar_exp = db.list_by_area("formulaBar")

    # 找第一个 button 或 combobox
    target_exp = None
    for e in formula_bar_exp:
        role = e.features.get("role", "")
        tag = e.features.get("tag", "").upper()
        if role in ("combobox", "listbox", "button") or tag in ("BUTTON", "SELECT"):
            target_exp = e
            break
    # 没找到就选第一个
    if not target_exp and formula_bar_exp:
        target_exp = formula_bar_exp[0]

    if target_exp:
        log.info("  Found experience match: %s (confidence=%.2f)",
                 target_exp.key, target_exp.confidence)
        click_result = await engine.replay(target_exp.key)
        if click_result.get("success"):
            log.info("  ✅ Clicked via experience! confidence=%.2f",
                     click_result.get("confidence", 0))
            click_success = True
            await asyncio.sleep(0.5)
    else:
        log.info("  No formulaBar experience found yet.")

    # 策略 2: 如果经验点击失败或没有经验，直接 JS 探索+点击
    if not click_result or not click_result.get("success"):
        log.info("  Falling back to direct DOM click...")
        # 直接执行点击公式栏第一个 button
        fallback_js = r"""
        (() => {
            try {
                const container = document.querySelector('#formulaBarContainer');
                if (!container) return {success: false, error: 'no container'};
                const btn = container.querySelector('button, [role="combobox"], [role="listbox"]');
                if (!btn) return {success: false, error: 'no button in container'};
                const r = btn.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return {success: false, error: 'button hidden'};
                const opts = {bubbles: true, cancelable: true, view: window};
                btn.dispatchEvent(new PointerEvent('pointerover', opts));
                btn.dispatchEvent(new PointerEvent('pointerdown', opts));
                btn.dispatchEvent(new MouseEvent('mousedown', opts));
                btn.click();
                btn.dispatchEvent(new PointerEvent('pointerup', opts));
                btn.dispatchEvent(new MouseEvent('mouseup', opts));
                return {
                    success: true,
                    tag: btn.tagName,
                    text: (btn.textContent || '').trim().slice(0, 100),
                    ariaLabel: btn.getAttribute('aria-label') || '',
                    dataAutomationId: btn.getAttribute('data-automationid') || '',
                    role: btn.getAttribute('role') || '',
                    rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                };
            } catch (e) {
                return {success: false, error: 'JS exception: ' + (e.message || e)};
            }
        })()
        """
        raw = await execute_in_studio(session, fallback_js)
        if raw.get("exceptionDetails"):
            log.error("  ❌ JS exception: %s", raw["exceptionDetails"].get("text", ""))
        else:
            fb_result = (raw.get("result") or {}).get("value") or {}
            if fb_result.get("success"):
                log.info("  ✅ Direct click succeeded! %s | %s",
                         fb_result.get("tag"), fb_result.get("ariaLabel", fb_result.get("text", "")))
                log.info("  data-automationid=%s", fb_result.get("dataAutomationId", ""))
                log.info("  role=%s", fb_result.get("role", ""))
                # 重新学习 formulaBar 区域把经验补全（包含刚刚点击的 combobox）
                log.info("  Re-learning formulaBar area to capture clicked element...")
                await engine.learn_formula_bar()
                click_success = True  # 标记成功供后续使用
            else:
                log.error("  ❌ Direct click failed: %s", fb_result.get("error", "unknown"))

    # ══════════════════════════════════════════════════════
    # 全自动点击一切探索
    # ══════════════════════════════════════════════════════
    log.info("=" * 60)
    log.info("[全自动] Starting click-all-explorable...")
    log.info("=" * 60)

    explore_result = await engine.click_all_explorable()
    props = explore_result.get("properties", {})
    ribbon_results = explore_result.get("ribbon", [])
    insert_results = explore_result.get("insertResults", [])

    log.info("[全自动] ✅ Ribbon buttons clicked: %d/%d",
             sum(1 for r in ribbon_results if r.get("success")), len(ribbon_results))
    log.info("[全自动] ✅ Insert panel controls: %d", len(insert_results))
    log.info("[全自动] ✅ Current control properties: %d", len(props))

    # 打印插入的控件列表
    if insert_results:
        log.info("[全自动] Inserted controls:")
        for ins in insert_results:
            name = ins.get("control_name", "?")
            tr = ins.get("traverse", {})
            prop_count = len(tr.get("properties", {}))
            err = tr.get("error", "")
            if prop_count:
                log.info("  ✅ %-30s properties=%d", name[:30], prop_count)
            else:
                log.info("  ⚠️  %-30s error=%s", name[:30], err[:40])

    # 打印属性列表
    if props:
        log.info("[全自动] Current control properties:")
        for name in props:
            p = props[name]
            log.info("  %-35s formula=%-30s inputs=%d labels=%d",
                     name[:35],
                     (p.get("formulaValue", "") or "(empty)")[:30],
                     len(p.get("panelInputs", [])),
                     len(p.get("panelLabels", [])))

    # 持久化
    from datetime import datetime
    full_report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ribbon": ribbon_results,
        "insertResults": insert_results,
        "properties": props,
        "properties_count": len(props),
    }
    report_path = Path(__file__).resolve().parent / "probe_results" / "experience_full_explore.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("[全自动] Report saved: %s", report_path)

    # ══════════════════════════════════════════════════════
    # 打印总结
    # ══════════════════════════════════════════════════════
    stats = db.get_stats()
    log.info("=" * 60)
    log.info("AGENT SUMMARY")
    log.info("=" * 60)
    log.info("  Elements in DB: %d", stats["total_elements"])
    log.info("  Avg confidence: %.2f", stats.get("avg_confidence", 0))
    log.info("  Areas: %s", stats.get("areas", []))
    log.info("  Experience file: %s",
             (Path(os.getenv("USERPROFILE", "~")) / ".cache" / "powerapps" / "experience.json"
              if not os.getenv("EXPERIENCE_FILE") else os.getenv("EXPERIENCE_FILE")))
    log.info("")
    log.info("  NEXT RUN: just log in and press Enter — agent will reuse experiences!")
    log.info("=" * 60)

    log.info("Browser stays open 30s for inspection.")
    await asyncio.sleep(30)
    await session.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")