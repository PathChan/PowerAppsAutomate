"""
在 PowerApps Studio Tree View 搜索框输入 "Text"
===============================================
流程：
  1. 启动浏览器 → 你登录
  2. 按回车 → 脚本自动：
     a. 探测所有输入框
     b. 在搜索框输入 "Text"
  3. 浏览器保持打开，你看效果
"""
from __future__ import annotations
import asyncio, logging, os, sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(_PROJECT_ROOT))
from PowerfulApps.Agents.config.env import load_env_file
logging.basicConfig(level=logging.INFO, format="%(message)s")


async def main():
    from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio
    from PowerfulApps.Browser.cdp.studio_cdp import execute_in_studio, reset_studio_cache

    load_env_file(_PROJECT_ROOT / ".env")
    session = await create_browser_session(
        os.getenv("POWER_APPS_URL", "https://make.powerapps.com"),
        Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile"))),
    )
    reset_studio_cache()
    if not await prepare_studio(session):
        print("[WARN] Studio 未连接，请登录并打开 App 后按回车")
        input()
        reset_studio_cache()
        if not await prepare_studio(session):
            print("[X] 还是连不上，退出")
            await session.stop()
            return

    print("[OK] Studio 已连接")
    print("按回车 → 探测输入框 + 自动搜索 Text")
    input()

    # ═══════════ 第一步：探测所有可见输入框 ═══════════
    probe_js = """
    (() => {
        const doc = document;
        const inputs = doc.querySelectorAll('input');
        const results = [];
        inputs.forEach(inp => {
            results.push({
                type: inp.type || '',
                placeholder: inp.placeholder || '',
                id: inp.id || '',
                cls: (inp.className || '').slice(0, 150),
                ariaLabel: inp.getAttribute('aria-label') || '',
                role: inp.getAttribute('role') || '',
                parentRole: (inp.parentElement?.getAttribute('role') || ''),
                parentCls: (inp.parentElement?.className || '').slice(0, 100),
            });
        });
        return results;
    })();
    """
    result = await execute_in_studio(session, probe_js)
    inputs = ((result.get("result") or {}).get("value") or [])
    print(f"\n在 Studio iframe 中找到 {len(inputs)} 个 input 元素：")
    for i, inp in enumerate(inputs):
        print(f"  [{i}] type={inp.get('type',''):8s} placeholder={inp.get('placeholder',''):20s} role={inp.get('role',''):15s}")
        print(f"       cls={inp.get('cls','')}")
        print()

    # ═══════════ 第二步：找搜索框并输入 Text ═══════════
    search_js = """
    (() => {
        const doc = document;

        // 多种方式找搜索框
        let box = null;
        // 方式1: input[type=search]
        box = doc.querySelector('input[type="search"]');
        // 方式2: placeholder 包含 search/Search/搜索/筛选
        if (!box) box = doc.querySelector('input[placeholder*="search" i], input[placeholder*="Search" i], input[placeholder*="搜索" i], input[placeholder*="筛选" i]');
        // 方式3: 在 [role=searchbox] 里找 input
        if (!box) box = doc.querySelector('[role="searchbox"] input');
        // 方式4: class 含 search/Search 的 input
        if (!box) box = doc.querySelector('input[class*="search" i], input[class*="Search" i]');
        // 方式5: 在 .ms-SearchBox 里找 input
        if (!box) box = doc.querySelector('.ms-SearchBox input');

        if (!box) {
            // 方式6: 拿第1个可见的 input 当搜索框（如果有）
            const allInputs = doc.querySelectorAll('input');
            for (const inp of allInputs) {
                const r = inp.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && inp.offsetParent !== null) {
                    box = inp;
                    break;
                }
            }
        }

        if (!box) return {error: "一个 input 都找不到"};

        const info = {
            type: box.type || '',
            placeholder: box.placeholder || '',
            id: box.id || '',
            cls: (box.className || '').slice(0, 150),
        };

        // 输入 Text
        box.focus();
        box.value = "Text";
        box.dispatchEvent(new Event('input', {bubbles: true, cancelable: true}));
        box.dispatchEvent(new Event('keyup', {bubbles: true, cancelable: true}));
        box.dispatchEvent(new Event('change', {bubbles: true, cancelable: true}));

        return {
            success: true,
            message: '已输入 "Text"',
            input_info: info,
        };
    })();
    """
    result2 = await execute_in_studio(session, search_js)
    data = (result2.get("result") or {}).get("value", {})
    print("=" * 60)
    if data.get("error"):
        print(f"[X] 搜索失败: {data['error']}")
    else:
        print(f"[✓] {data.get('message','')}")
        print(f"    使用的输入框: type={data['input_info']['type']} placeholder={data['input_info']['placeholder']} cls={data['input_info']['cls']}")
    print("=" * 60)

    # ═══════════ 保持浏览器打开 ═══════════
    print("\n浏览器已保持打开，看看 PowerApps Tree View 搜索结果吧！")
    print("按回车关闭浏览器...")
    await asyncio.to_thread(input)
    await session.stop()


if __name__ == "__main__":
    asyncio.run(main())