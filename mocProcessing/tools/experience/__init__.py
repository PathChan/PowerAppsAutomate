"""experience：PowerApps Studio 自学习点击经验系统。

本模块让 Agent 在 PowerApps Studio iframe 内自动探索 DOM，
提取每个可交互元素的多维度特征，持久化存储，并在下次运行时
通过多特征匹配精确定位并点击元素，无需人类重复干预。

核心流程
--------
1. 探索（explore）：扫描 DOM，找出所有可见可交互元素
2. 提取（extract）：对每个元素提取 10+ 维特征向量
3. 存储（persist）：写入 JSON 经验数据库
4. 重放（replay）：给定任务描述，匹配最佳元素并执行点击
5. 学习（learn）：每次点击后验证结果，更新置信度
"""

from mocProcessing.tools.experience.db import ExperienceDB, ElementExperience
from mocProcessing.tools.experience.engine import ExperienceEngine

__all__ = ["ExperienceDB", "ElementExperience", "ExperienceEngine"]