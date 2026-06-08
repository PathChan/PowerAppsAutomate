"""MocProcess.chains: PowerApps Studio 流程编排。

每个文件对应一个完整的操作流程：
  insert_and_set_formula.py  — 插入组件 → 选中属性 → 写入公式
  set_property_formula.py    — 当前选中控件 → 选中属性 → 写入公式
"""
from . import insert_and_set_formula, set_property_formula