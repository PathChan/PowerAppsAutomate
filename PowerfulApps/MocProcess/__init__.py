"""MocProcess: PowerApps 自动化操作模块。

层级：
  actions/     — 原子操作（插入菜单、公式栏、属性面板等）
  chains/      — 流程编排（把多个 action 串成一步完成）
"""
from . import actions, chains