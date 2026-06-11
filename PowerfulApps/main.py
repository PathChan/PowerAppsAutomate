"""
PowerFul APPS
=========================================
基于 LangGraph 有向图记忆的 PowerApps 自动化 Agent。

启动后提供交互式命令行界面：
  - 自动加载/创建项目有向图记忆
  - 连接 PowerApps Studio 浏览器
  - Tree View 扫描 → 构建有向图
  - 智能模式：AI 自动拆解需求、调用工具操作 PowerApps
  - 模型管理：/model 命令切换供应商和模型

命令：
  /help                     - 显示帮助
  /tree scan|graph|json [屏幕]  - Tree View 扫描/可视化/导出
  /project ls|new|use|current   - 项目管理
  /model current|providers|provider|set  - 模型管理
  /exit | /quit              - 退出
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PowerfulApps.Agents.LLM import (
    OpenAICompatibleClient,
    create_llm_client,
    get_manufacturers,
    get_presets_by_manufacturer,
    load_default_preset,
    load_preset,
    load_presets,
    provider_names,
    save_preset,
    ProviderPreset,
)
from PowerfulApps.Agents.config.env import load_env_file
from PowerfulApps.Agents.memory.graph_memory import ControlGraph
from PowerfulApps.Agents.memory.short_memory import ShortTermMemory
from PowerfulApps.Browser.watchdogs.studio_action_watchdog import StudioActionWatchdog

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(name)s | %(message)s")
log = logging.getLogger("main")

# ═══════════════════════════════════════════════════════════════
#  终端颜色
# ═══════════════════════════════════════════════════════════════
_C = {"rst": "[0m", "b": "[1m", "d": "[2m", "r": "[31m",
      "g": "[32m", "y": "[33m", "c": "[36m", "m": "[35m", "w": "[37m"}
_E = chr(27)

def cc(t: str, *ns: str) -> str:
    return _E + "".join(_C.get(n, "") for n in ns) + t + _E + _C["rst"]

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

# ═══════════════════════════════════════════════════════════════
#  全局状态
# ═══════════════════════════════════════════════════════════════
_GP = os.getenv(
    "POWERAPPS_GRAPH_PATH",
    str(_PROJECT_ROOT / "PowerfulApps" / "Agents" / ".memory" / "project_graph.bin"),
)
_session = None
_watchdog = None
_graph: ControlGraph | None = None
_llm: OpenAICompatibleClient | None = None
_short_memory: ShortTermMemory | None = None
_tool_registry = None

# ═══════════════════════════════════════════════════════════════
#  可视化（已移至 Agents/memory/visualizer.py）
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  Watchdog 回调
# ═══════════════════════════════════════════════════════════════
async def on_watchdog_change(changes: list[str]) -> None:
    for ch in changes:
        print(cc(f"  [{ts()}] 👀 {ch}", "d"))

# ═══════════════════════════════════════════════════════════════
#  模型管理
# ═══════════════════════════════════════════════════════════════
def _mask_key(value: str) -> str:
    if not value: return "未设置"
    if len(value) <= 8: return "****"
    return f"{value[:4]}...{value[-4:]}"

async def cmd_model(args: list[str]) -> None:
    """处理 /model 命令"""
    global _llm
    if len(args) < 2 or args[1] in ("help", "h"):
        print(cc("  /model current              查看当前模型配置", "d"))
        print(cc("  /model ls                   列出所有已保存的模型配置（按厂商分组）", "d"))
        print(cc("  /model providers            查看可用厂商", "d"))
        print(cc("  /model new <名称> <模型名>   新建模型配置（基于当前厂商）", "d"))
        print(cc("  /model new --from <厂商> <名称> <模型名>  从指定厂商新建", "d"))
        print(cc("  /model change <名称>        切换到已保存的模型配置", "d"))
        print(cc("  /model set <param> <值>     修改当前模型参数（key/model/base_url/thinking）", "d"))
        return
    action = args[1].lower()

    if action == "current":
        c = create_llm_client() if _llm is None else _llm
        prov_name = os.getenv("LLM_PROVIDER", "").strip().lower()
        print(cc(f"  配置名称: {prov_name}", "c"))
        print(cc(f"  厂商: {c.provider}", "c"))
        print(cc(f"  模型: {c.model}", "c"))
        print(cc(f"  Base URL: {c.base_url}", "d"))
        print(cc(f"  API Key: {_mask_key(c.api_key)}", "d"))
        print(cc(f"  Thinking: {c.thinking_enabled}", "d"))

    elif action == "ls":
        grouped = get_presets_by_manufacturer()
        if not grouped:
            print(cc("  没有已保存的模型配置", "d"))
            return
        current_key = os.getenv("LLM_PROVIDER", "").strip().lower()
        default_preset = load_default_preset()
        default_name = default_preset.name if default_preset else ""
        for manufacturer, models in sorted(grouped.items()):
            print(cc(f"  [{manufacturer}]", "c"))
            for name, p in sorted(models.items()):
                tags = []
                if name == current_key:
                    tags.append(cc("← 当前", "g"))
                if name == default_name:
                    tags.append(cc("⭐默认", "y"))
                suffix = f"  {' '.join(tags)}" if tags else ""
                print(cc(f"    {name:<18} {p.model:<30}{suffix}", ""))
            print()

    elif action == "providers":
        mans = get_manufacturers()
        if mans:
            print(cc("  可用厂商: " + ", ".join(mans), "g"))
        else:
            print(cc("  没有已保存的厂商配置", "d"))

    elif action == "new":
        # /model new <名称> <模型名>
        # /model new --from <厂商> <名称> <模型名>
        if len(args) < 3:
            print(cc("  用法:", "y"))
            print(cc("    /model new my-flash deepseek-v4-flash", "d"))
            print(cc("      ← 基于当前厂商创建", "d"))
            print(cc("    /model new --from volcengine my-pro DeepSeek-V4-Pro", "d"))
            print(cc("      ← 基于指定厂商创建", "d"))
            return

        idx = 2
        from_manufacturer: str | None = None
        if args[idx] == "--from" and idx + 1 < len(args):
            from_manufacturer = args[idx + 1]
            idx += 2

        if idx >= len(args):
            print(cc("  需要指定配置名称", "y"))
            return
        user_name = args[idx]
        model_name = args[idx + 1] if idx + 1 < len(args) else None

        # 确定 manufacturer 和 base 预设
        if from_manufacturer:
            mf = from_manufacturer.strip().lower()
            # 找到该厂商的第一个预设作为模板
            grouped = get_presets_by_manufacturer()
            models = grouped.get(mf)
            if not models:
                print(cc(f"  厂商 '{mf}' 不存在，可用: {', '.join(get_manufacturers())}", "r"))
                return
            base = next(iter(models.values()))
        else:
            current_key = os.getenv("LLM_PROVIDER", "").strip().lower()
            base = load_preset(current_key)
            if base is None:
                # 取第一个预设
                all_p = load_presets()
                if not all_p:
                    print(cc("  没有可用预设", "r"))
                    return
                base = next(iter(all_p.values()))
            mf = base.manufacturer

        new_preset = ProviderPreset(
            name=user_name,
            manufacturer=mf,
            base_url=base.base_url,
            model=model_name or base.model,
            api_key=base.api_key,
            thinking=base.thinking,
            reasoning_effort=base.reasoning_effort,
            api_type=base.api_type,
        )
        save_preset(user_name, new_preset)
        print(cc(f"  ✓ 已保存模型配置 '{user_name}': {mf}/{new_preset.model}", "g"))

    elif action == "change":
        names = provider_names()
        if len(args) < 3:
            print(cc("  用法: /model change <名称>", "y"))
            print(cc("  可用配置: " + ", ".join(names), "d"))
            return
        target = args[2].strip().lower()
        preset = load_preset(target)
        if preset is None:
            print(cc(f"  未找到模型配置 '{target}'", "r"))
            print(cc("  可用配置: " + ", ".join(names), "d"))
            return
        os.environ["LLM_PROVIDER"] = target
        _llm = create_llm_client()
        print(cc(f"  ✓ 已切换到 '{target}': {_llm.provider} / {_llm.model}", "g"))

    elif action == "set":
        if len(args) < 4:
            print(cc("  用法: /model set <param> <值>", "y"))
            print(cc("  param: key | model | base_url | thinking  (true/false)", "d"))
            return
        field = args[2].lower()
        value = " ".join(args[3:])
        provider_key = os.getenv("LLM_PROVIDER", "").strip().lower()
        preset = load_preset(provider_key)
        if preset is None:
            print(cc(f"  当前配置 '{provider_key}' 不存在", "r"))
            return

        env_map = {
            "key": "LLM_API_KEY",
            "api_key": "LLM_API_KEY",
            "base_url": "LLM_BASE_URL",
            "url": "LLM_BASE_URL",
            "model": "LLM_MODEL",
            "thinking": "LLM_THINKING_ENABLED",
        }

        env_name = env_map.get(field)
        if not env_name:
            print(cc(f"  可设置: key, model, base_url, thinking", "y"))
            return

        if field == "thinking":
            value = "true" if value.lower() in {"1", "true", "yes", "y", "on"} else "false"

        os.environ[env_name] = value
        _llm = create_llm_client()
        print(cc(f"  ✓ 已设置 {field}", "g"))
    else:
        print(cc(f"  未知: /model {action}    可用: current, ls, providers, new, change, set", "r"))

# ═══════════════════════════════════════════════════════════════
#  项目管理
# ═══════════════════════════════════════════════════════════════
async def cmd_project(args: list[str]) -> None:
    """处理 /project 命令（基于有向图）"""
    global _graph, _GP
    if len(args) < 2 or args[1] in ("help", "h"):
        print(cc("  /project ls                 列出所有项目", "d"))
        print(cc("  /project new                新建项目", "d"))
        print(cc("  /project use <名称>          使用指定项目", "d"))
        print(cc("  /project current            查看当前项目", "d"))
        return
    action = args[1].lower()
    if action == "current":
        s = _graph.stats()
        print(cc(f"  当前图: {s['screens']} 屏幕 | {s['controls']} 控件 | {s['edges']} 边", "c"))
        print(cc(f"  存储: {_GP}", "d"))
    elif action == "ls":
        # 列出 .memory 目录下所有 .bin 文件
        mem_dir = Path(_GP).parent
        if mem_dir.exists():
            files = list(mem_dir.glob("*.bin"))
            if files:
                for f in files:
                    size = f.stat().st_size
                    print(cc(f"  {f.stem}  ({size} bytes)", "d"))
            else:
                print(cc("  没有保存的项目图", "d"))
        else:
            print(cc("  没有保存的项目图", "d"))
    elif action == "new":
        # 清空当前图重建
        _graph.graph.clear()
        _graph._screens.clear()
        print(cc("  ✓ 已创建新项目（清空图）", "g"))
    elif action == "use":
        if len(args) < 3:
            print(cc("  用法: /project use <名称>", "y"))
            return
        name = args[2]
        path = Path(_GP).parent / f"{name}.bin"
        if not path.exists():
            print(cc(f"  未找到项目: {name}", "r"))
            return
        os.environ["POWERAPPS_GRAPH_PATH"] = str(path)
        _GP = str(path)
        _graph = ControlGraph.load(path)
        s = _graph.stats()
        print(cc(f"  ✓ 已切换到: {name} ({s['screens']} 屏幕 | {s['controls']} 控件)", "g"))
    else:
        print(cc(f"  未知: /project {action}", "r"))

# ═══════════════════════════════════════════════════════════════
#  LLM 智能模式（带工具调用 + 图上下文）
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """你是一个 PowerApps Studio 自动化智能体。

## 可用工具（按优先级排列）

### 1. 日常增改（首选）
- insert_component_and_set_formula — 插入控件并设置属性
- set_property_formula — 修改当前选中控件的属性公式

### 2. 查看现状（轻量，优先用）
- get_tree_structure — 【首选查看】扫描 Tree View，返回所有控件的准确列表
- search_in_tree — 在 Tree View 中按名称搜索控件

### 3. 属性查看
- traverse_all_properties — 遍历当前控件全部属性
- get_property_options — 查看当前控件的可选属性列表

### 4. 插入菜单
- scan_insert_menu — 打开插入菜单，扫描可用控件模板

### 5. 兜底（最后手段）
- get_dom_snapshot — DOM 快照，仅当前面所有工具都失败时才用

## 工作方式
1. 用户给出需求后，拆解为可执行步骤，每一步调一个工具。
2. 插入控件后用 get_tree_structure 确认实际插入了什么、叫什么名字。
3. 写入 Power Fx 字符串文本时要包含双引号，例如 Text 属性写入 "点我一下"。
4. 每次工具调用后根据结果决定下一步；成功完成后用中文简短说明。
5. 不要连续调用 get_dom_snapshot — 同一任务最多调 1 次。先试 get_tree_structure。

当前项目的控件引用图（有向图）会在上下文提供。
"""

async def _ensure_tool_registry():
    global _tool_registry
    if _tool_registry is None and _session is not None:
        from PowerfulApps.Agents.core.tools import PowerAppsToolRegistry
        _tool_registry = PowerAppsToolRegistry(_session)


async def cmd_llm(raw: str) -> None:
    """LLM 智能模式：图上下文 + 工具调用"""
    global _graph, _llm, _short_memory

    # 提取控件名作为上下文
    ctrl_match = re.findall(r"([A-Z][a-zA-Z0-9_]+)", raw)
    nb = ""
    for m in ctrl_match:
        if _graph.graph.has_node(m):
            nb = _graph.serialize_neighborhood_pseudocode(m, 2)
            break
    if not nb and _graph.graph.number_of_nodes() > 0:
        all_ctrls = _graph.get_all_controls()
        if all_ctrls:
            nb = _graph.serialize_neighborhood_pseudocode(all_ctrls[0], 1)

    graph_info = f"\n当前项目图状态: {_graph.stats()}\n相关控件拓扑:\n{nb}" if nb else f"\n当前项目图状态: {_graph.stats()}\n(图中暂无控件)"

    _short_memory.add_user(raw)
    await _short_memory.compact_if_needed(_llm, preserve_last=1)

    await _ensure_tool_registry()

    print(cc(f"  [{ts()}] 🤔 思考中...", "y"))

    max_steps = 50
    dom_call_count = 0  # 跟踪 get_dom_snapshot 调用次数

    for step in range(1, max_steps + 1):
        messages = _short_memory.build_messages(SYSTEM_PROMPT + graph_info, "")
        kwargs = {"messages": messages}
        if _tool_registry:
            kwargs["tools"] = _tool_registry.openai_tools()
            kwargs["tool_choice"] = "auto"

        try:
            response = await _llm.chat(**kwargs)
        except Exception as e:
            print(cc(f"  [X] LLM 调用失败: {e}", "r"))
            return

        msg = response.choices[0].message
        _short_memory.add_assistant(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(cc(f"  {msg.content or ''}", ""))
            return

        # 拦截过多 DOM 调用
        has_dom_call = any(call.function.name == "get_dom_snapshot" for call in msg.tool_calls)
        if has_dom_call and dom_call_count >= 1:
            print(cc(f"  [{ts()}] ⛔ get_dom_snapshot 已调用过，建议用 get_tree_structure 替代", "y"))
            # 替换调用为 get_tree_structure
            _short_memory.add_tool({
                "role": "tool",
                "tool_call_id": msg.tool_calls[0].id,
                "name": "get_dom_snapshot",
                "content": "[系统拦截] 不建议连续调用 get_dom_snapshot。请使用 get_tree_structure 或 search_in_tree 查看当前控件状态。",
            })
            continue
        if has_dom_call:
            dom_call_count += 1

        for call in msg.tool_calls:
            tool_name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                print(cc(f"  [X] 参数解析失败: {call.function.arguments}", "r"))
                continue

            print(cc(f"  [{ts()}] ({step}/{max_steps}) ⚙️ 调用: {tool_name}({args})", "d"))
            try:
                result = await _tool_registry.run_tool(tool_name, args)
                _short_memory.add_tool({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": tool_name,
                    "content": result,
                })

                # ── 记忆更新：根据工具类型做定点维护 ──
                if tool_name == "insert_component_and_set_formula":
                    component = args.get("component", "")
                    prop_name = args.get("property_name", "")
                    formula = args.get("formula", "")
                    if component and not _graph.graph.has_node(component):
                        _graph.add_control("__unknown__", component)
                    if component and prop_name:
                        _graph.update_property(component, prop_name, formula)
                        _graph.auto_detect_references(component)
                        _graph.auto_detect_variable_chain(component)
                        _graph.save(Path(_GP))

                    # 插入控件后自动扫描 Tree View 补充新节点
                    try:
                        from PowerfulApps.Agents.memory.tree_traversal import traverse_tree_via_cdp as _scan_tree
                        tree_items = await _scan_tree(_session)
                        if tree_items:
                            before_count = _graph.graph.number_of_nodes()
                            new_nodes = []
                            for item in tree_items:
                                name = item["name"]
                                parent = item.get("parent", "")
                                typ = item.get("type", "control")
                                if typ == "screen":
                                    _graph.add_screen(name)
                                elif not _graph.graph.has_node(name):
                                    _graph.add_control(screen=parent or "__unknown__", name=name)
                                    new_nodes.append(name)
                            added = _graph.graph.number_of_nodes() - before_count
                            if added > 0:
                                for nn in new_nodes:
                                    _graph.auto_detect_variable_chain(nn)
                                _graph.save(Path(_GP))
                                msg_tip = f"（Tree View 自动同步: 新增 {added} 个节点）"
                                print(cc(f"  [{ts()}] 🌳 {msg_tip}", "d"))
                                _short_memory.add_tool({
                                    "role": "tool",
                                    "tool_call_id": f"auto_tree_{step}",
                                    "name": "_auto_tree_sync",
                                    "content": msg_tip,
                                })
                    except Exception:
                        pass

                elif tool_name == "set_property_formula":
                    prop_name = args.get("property_name", "")
                    formula = args.get("formula", "")
                    if prop_name and _graph.graph.number_of_nodes() > 0:
                        # 对图中所有控件做一次变量链检测（set_property 不知道目标控件名）
                        _graph.auto_detect_all_variable_chains()
                        _graph.save(Path(_GP))

            except Exception as e:
                err = f"[错误] {tool_name}: {e}"
                _short_memory.add_tool({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": tool_name,
                    "content": err,
                })
                print(cc(f"  [X] {err}", "r"))

        await _short_memory.compact_if_needed(_llm, preserve_last=len(msg.tool_calls) + 1)

    # 达到最大步骤数 → 续做
    print(cc(f"  [{ts()}] 已达到 {max_steps} 步上限，任务未完成，自动续做...", "y"))
    _short_memory.add_user("[系统] 已达到步骤上限，但任务尚未完成。请从断点处继续执行，直到全部完成后给出总结。")
    await cmd_llm(raw)


# ═══════════════════════════════════════════════════════════════
#  原有命令处理
# ═══════════════════════════════════════════════════════════════
async def cmd_help() -> None:
    print(cc("  ┌─ 命令列表 ──────────────────────────────────┐", "c"))
    print(cc("  │  /help                    帮助              │", "d"))
    print(cc("  │  /project                 项目管理          │", "d"))
    print(cc("  │  /tree                    项目树            │", "d"))
    print(cc("  │  /model                   模型管理          │", "d"))
    print(cc("  └────────────────────────────────────────────┘", "c"))

async def cmd_tree(args: list[str]) -> None:
    """处理 /tree 子命令

    用法:
      /tree scan                   最轻量：只扫描组件名和层级
      /tree scan --props [屏幕]    中等：组件名 + 常用属性（Text/OnSelect/Visible 等）
      /tree scan --full [屏幕]     最重：组件名 + 全部属性
      /tree scan --control <名称>          扫描指定控件的常用属性
      /tree scan --control <名称> --full   扫描指定控件的全部属性
      /tree graph                  可视化当前图
      /tree json [屏幕]            导出 JSON
    """
    global _graph, _session
    if len(args) < 2:
        _print_tree_help()
        return

    sub = args[1].lower()

    # ── scan 命令（支持多种模式）──
    if sub == "scan":
        if not _session:
            print(cc("  [X] 需要先连接浏览器", "r"))
            return

        from PowerfulApps.Agents.memory.tree_traversal import apply_scan_to_graph

        # 解析参数
        mode = "light"
        screen_filter = None
        control_name = None

        i = 2
        while i < len(args):
            a = args[i].lower()
            if a == "--props":
                mode = "props"
            elif a == "--full":
                mode = "full" if mode != "control" else "control_full"
            elif a == "--control":
                if i + 1 < len(args):
                    control_name = args[i + 1]
                    mode = "control"
                    i += 1
                else:
                    print(cc("  [X] --control 需要指定控件名称，如: /tree scan --control Button1", "r"))
                    return
            else:
                # 非选项参数 → 屏幕过滤
                screen_filter = args[i]
            i += 1

        # 执行扫描
        mode_labels = {
            "light": "最轻量（仅组件名）",
            "props": "中等（组件名 + 常用属性）",
            "full": "最重（组件名 + 全部属性）",
            "control": f"单控件常用属性 → {control_name}",
            "control_full": f"单控件全部属性 → {control_name}",
        }
        label = mode_labels.get(mode, mode)
        print(cc(f"  [{ts()}] 扫描模式: {label}", "y"))

        result = await apply_scan_to_graph(
            _session, _graph,
            mode=mode,
            screen_filter=screen_filter,
            control_name=control_name,
        )

        if result.get("error"):
            print(cc(f"  [X] {result['error']}", "r"))
            return

        nodes = result.get("nodes_added", 0)
        props = result.get("props_updated", 0)
        print(cc(f"  [{ts()}] 完成: +{nodes} 节点, {props} 属性更新", "g"))
        stats = _graph.stats()
        print(cc(f"  📊 当前: {stats['screens']} 屏幕 | {stats['controls']} 控件 | {stats['edges']} 引用", "c"))

    # ── graph ──
    elif sub == "graph":
        from PowerfulApps.Agents.memory.visualizer import visualize_graph
        out = visualize_graph(_graph)
        print(cc(f"  [可视化] {out}", "g"))

    # ── json ──
    elif sub == "json":
        j = _graph.to_json()
        if len(args) > 2:
            screen_filter = args[2]
            sf = screen_filter.lower()
            data = json.loads(j)
            if isinstance(data, dict):
                filtered_nodes = [
                    n for n in data.get("nodes", [])
                    if sf in n.get("screen", "").lower()
                    or sf in n.get("id", "").lower()
                ]
                filtered_ids = {n["id"] for n in filtered_nodes}
                data["nodes"] = filtered_nodes
                data["edges"] = [
                    e for e in data.get("edges", [])
                    if e["source"] in filtered_ids or e["target"] in filtered_ids
                ]
                data["screens"] = [s for s in data.get("screens", []) if sf in s.lower()]
                j = json.dumps(data, ensure_ascii=False, indent=2)
        print(cc(j[:5000] + ("\n  ... (截断)" if len(j) > 5000 else ""), "y"))

    else:
        _print_tree_help()


def _print_tree_help() -> None:
    print(cc("  ┌─ /tree 命令 ───────────────────────────────────────────────┐", "c"))
    print(cc("  │                                                            │", "c"))
    print(cc("  │  扫描：                                                    │", "c"))
    print(cc("  │    /tree scan                    最轻量  仅组件名和层级     │", "d"))
    print(cc("  │    /tree scan --props [屏幕]     中等    组件名+常用属性    │", "d"))
    print(cc("  │    /tree scan --full [屏幕]      最重    组件名+全部属性    │", "d"))
    print(cc("  │    /tree scan --control <名称>           单控件常用属性     │", "d"))
    print(cc("  │    /tree scan --control <名称> --full    单控件全部属性     │", "d"))
    print(cc("  │                                                            │", "c"))
    print(cc("  │  查看：                                                     │", "c"))
    print(cc("  │    /tree graph                   可视化当前图              │", "d"))
    print(cc("  │    /tree json [屏幕]             导出 JSON                 │", "d"))
    print(cc("  │                                                            │", "c"))
    print(cc("  │  示例：                                                     │", "c"))
    print(cc("  │    /tree scan --props Screen1    扫描 Screen1 的常用属性   │", "d"))
    print(cc("  │    /tree scan --control Button1  查看 Button1 的常用属性   │", "d"))
    print(cc("  │    /tree scan --full             全量扫描整个项目           │", "d"))
    print(cc("  └────────────────────────────────────────────────────────────┘", "c"))


# ═══════════════════════════════════════════════════════════════
#  主循环
# ═══════════════════════════════════════════════════════════════
async def main_loop() -> None:
    global _graph, _session, _watchdog, _llm, _short_memory

    load_env_file(_PROJECT_ROOT / ".env")

    # ── 启动画面 ──
    _ascii = r"""
  _____                       ______     _            _____  _____   _____
 |  __ \                     |  ____|   | |     /\   |  __ \|  __ \ / ____|
 | |__) |____      _____ _ __| |__ _   _| |    /  \  | |__) | |__) | (___
 |  ___/ _ \ \ /\ / / _ \ '__|  __| | | | |   / /\ \ |  ___/|  ___/ \___ \
 | |  | (_) \ V  V /  __/ |  | |  | |_| | |  / ____ \| |    | |     ____) |
 |_|   \___/ \_/\_/ \___|_|  |_|   \__,_|_| /_/    \_\_|    |_|    |_____/
    """
    for line in _ascii.split("\n"):
        print(cc(line, "c"))
    print()

    # ── 加载/创建图 ──
    gp = Path(_GP)
    gp.parent.mkdir(parents=True, exist_ok=True)
    _graph = ControlGraph.load(gp)
    s = _graph.stats()
    if s["controls"] == 0:
        print(cc(f"  📂 新项目 — 使用 /tree scan 命令扫描 Tree View（/tree 查看所有模式）", "y"))
    else:
        print(cc(f"  📂 已加载: {s['screens']} 屏幕 | {s['controls']} 控件 | {s['edges']} 引用", "g"))

    # ── 初始化 LLM + 短期记忆 ──
    _llm = create_llm_client()
    _short_memory = ShortTermMemory(max_messages=24, max_summaries=12)
    print(cc(f"  🤖 {_llm.provider} / {_llm.model}", "d"))

    # ── 连接浏览器 ──
    try:
        from PowerfulApps.Agents.core.runtime import create_browser_session, prepare_studio
        url = os.getenv("POWER_APPS_URL", "https://make.powerapps.com")
        ud = Path(os.getenv("BROWSER_USE_USER_DATA_DIR", str(_PROJECT_ROOT / "browser_profile")))
        ud.mkdir(parents=True, exist_ok=True)
        _session = await create_browser_session(url, ud)
        if await prepare_studio(_session):
            print(cc(f"  🔌 Studio 已连接", "g"))
            _watchdog = StudioActionWatchdog(_session)
            await _watchdog.start()
            print(cc(f"  👀 监测器已启动", "d"))
            asyncio.create_task(_watchdog.watch_loop(_graph, interval=3.0, on_change=on_watchdog_change))
        else:
            print(cc(f"  ⚠ Studio 未连接 — 部分命令不可用", "y"))
    except Exception as e:
        print(cc(f"  ⚠ 浏览器初始化: {e}", "y"))

    # ── 命令循环 ──
    print()
    print(cc("  输入 /help 查看命令  |  其他文本自动走智能模式  |  /exit 退出", "d"))
    print()

    while True:
        try:
            raw = input(cc("  > ", "c", "b")).strip()
        except (EOFError, KeyboardInterrupt):
            print(); print(cc("  Bye", "m")); break
        if not raw: continue

        cmd = raw.lower().split()
        a = cmd[0]

        try:
            if a in ("/exit", "/quit"):
                print(cc("  Bye", "m")); break
            elif a == "/help":
                await cmd_help()
            elif a == "/tree":
                await cmd_tree(cmd)
            elif a == "/project":
                await cmd_project(cmd)
            elif a == "/model":
                await cmd_model(cmd)
            else:
                # 其他输入 → 智能模式
                await cmd_llm(raw)
        except Exception as e:
            print(cc(f"  [ERR] {e}", "r"))
            traceback.print_exc()

    # ── 清理 ──
    _graph.save(gp)
    print(cc(f"  💾 图已保存", "d"))
    if _watchdog: _watchdog.stop()
    if _session:
        try: await _session.stop()
        except: pass

def main() -> None:
    asyncio.run(main_loop())

if __name__ == "__main__":
    main()