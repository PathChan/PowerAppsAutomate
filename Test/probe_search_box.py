"""快速探测 PowerApps Studio Tree View 区域的输入框 DOM 结构。"""
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
    url = os.getenv("POWER_APPS_URL", "https://make.powerapps.com")
    ud = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile")))
    ud.mkdir(parents=True, exist_ok=True)
    session = await create_browser_session(url, ud)
    reset_studio_cache()
    if not await prepare_studio(session):
        print("Studio 未连接")
        return

    # Dump 所有 input / 搜索相关元素
    js = """
    (() => {
        const doc = document;
        const results = [];

        // 所有 input
        const inputs = doc.querySelectorAll('input');
        inputs.forEach(inp => {
            if (inp.offsetParent !== null || inp.getBoundingClientRect().width > 0) {
                results.push({
                    tag: 'input',
                    type: inp.type || '(no type)',
                    placeholder: inp.placeholder || '',
                    id: inp.id || '',
                    cls: (inp.className || '').slice(0, 120),
                    role: inp.getAttribute('role') || '',
                    ariaLabel: inp.getAttribute('aria-label') || '',
                    automationId: inp.getAttribute('data-automationid') || '',
                    parentCls: (inp.parentElement?.className || '').slice(0, 120),
                    parentRole: inp.parentElement?.getAttribute('role') || '',
                });
            }
        });

        // 带 search/filter 关键字的元素
        const searchEls = doc.querySelectorAll('[class*="search" i], [class*="Search" i], [class*="filter" i], [class*="Filter" i], [role="searchbox"]');
        searchEls.forEach(el => {
            results.push({
                tag: el.tagName,
                id: el.id || '',
                cls: (el.className || '').slice(0, 120),
                role: el.getAttribute('role') || '',
                text: (el.textContent || '').trim().slice(0, 80),
            });
        });

        // Tree View 容器内的所有元素
        const tree = doc.querySelector('[role="tree"], [class*="tree-view"], [class*="TreeView"]');
        if (tree) {
            const treeInputs = tree.querySelectorAll('input, [role="searchbox"], [class*="search"], [class*="Search"]');
            treeInputs.forEach(el => {
                results.push({
                    tag: el.tagName,
                    inTree: true,
                    type: el.type || '',
                    placeholder: el.placeholder || '',
                    id: el.id || '',
                    cls: (el.className || '').slice(0, 120),
                    role: el.getAttribute('role') || '',
                });
            });
        }

        return results;
    })();
    """
    result = await execute_in_studio(session, js)
    data = (result.get("result") or {}).get("value", [])
    print(f"\n找到 {len(data)} 个搜索相关元素：\n")
    for i, d in enumerate(data):
        print(f"  [{i}] {d.get('tag','?')} type={d.get('type','')} placeholder={d.get('placeholder','')}")
        print(f"       id={d.get('id','')} cls={d.get('cls','')}")
        print(f"       role={d.get('role','')} ariaLabel={d.get('ariaLabel','')}")
        print(f"       automationId={d.get('automationId','')}")
        print()

    print("按回车关闭浏览器...")
    await asyncio.to_thread(input)
    await session.stop()

if __name__ == "__main__":
    asyncio.run(main())