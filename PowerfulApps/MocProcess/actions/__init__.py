"""MocProcess.actions: PowerApps Studio 原子操作。

每个文件对应一个操作域：
  insert_menu.py      — 打开插入菜单、展开分类、点击控件模板
  formula_bar.py      — 属性选择器 combobox、公式编辑器写入
  search_element.py   — Tree View 搜索框输入关键词 + 点击 tree item
  click_sidebar_tab.py — 点击左侧栏任意 tab（树视图/插入/数据...）
"""
from . import insert_menu, formula_bar, search_element, click_sidebar_tab