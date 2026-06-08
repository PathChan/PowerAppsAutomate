"""experience/db.py：经验持久化存储层。

以 JSON 文件存储每个可交互元素的多维度特征向量，
相比 target_cache.py 只存坐标，本模块存 10+ 维特征，
支持下次运行时多特征模糊匹配重定位。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _PROJECT_ROOT / ".cache" / "powerapps"
_CACHE_FILE = _CACHE_DIR / "experience.json"

# ── 特征得分权重（只保留跨环境稳定的特征） ──────────────
FEATURE_WEIGHTS: dict[str, float] = {
    "data_automationid": 1.0,   # Microsoft 的稳定标识
    "data_control_name": 0.95,  # 控件名字
    "aria_label": 0.85,         # 无障碍标签
    "id": 0.80,                 # DOM id
    "text": 0.65,               # 文本内容
    "role": 0.45,               # ARIA 角色
    "tag": 0.40,                # HTML 标签名
    "placeholder": 0.35,        # 占位文本
    "title": 0.30,              # 标题
    "rect": 0.0,                # 坐标 — 跨环境不可用
}


@dataclass
class ElementExperience:
    """一个可交互元素的完整经验记录。"""

    # 唯一键：human-readable 名称，如 "property_selector_dropdown"
    key: str

    # ── 多维度特征向量 ──────────────────────────────────
    features: dict[str, Any] = field(default_factory=dict)

    # ── 元数据 ──────────────────────────────────────────
    confidence: float = 0.5        # 置信度（0~1），每次验证成功递增
    usage_count: int = 0           # 使用次数
    success_count: int = 0         # 成功次数
    fail_count: int = 0            # 失败次数
    area_hint: str = ""            # 区域提示，如 "formulaBar" / "ribbon" / "propertyPanel"
    created_at: float = 0.0        # unix 时间戳
    last_used_at: float = 0.0      # 最后成功使用时间
    last_error: str = ""           # 上次失败的错误信息

    # 被哪个 key 引用（如果有父子关系）
    parent_key: str = ""

    def record_success(self) -> None:
        """调用一次成功。"""
        self.usage_count += 1
        self.success_count += 1
        self.last_used_at = time.time()
        self.confidence = min(1.0, self.confidence + 0.1)

    def record_failure(self, error: str) -> None:
        """调用一次失败。"""
        self.usage_count += 1
        self.fail_count += 1
        self.last_error = error
        self.confidence = max(0.1, self.confidence - 0.15)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ElementExperience:
        return cls(**d)


class ExperienceDB:
    """经验数据库，以 JSON 文件持久化。"""

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._file = Path(file_path) if file_path else _CACHE_FILE

    # ── 读 / 写 ──────────────────────────────────────────────

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self._file.exists():
            return {}
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("experience.json corrupted, starting fresh: %s", e)
            return {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ── CRUD ─────────────────────────────────────────────────

    def get(self, key: str) -> ElementExperience | None:
        raw = self._read().get(key)
        if raw:
            return ElementExperience.from_dict(raw)
        return None

    def save(self, exp: ElementExperience) -> None:
        data = self._read()
        data[exp.key] = exp.to_dict()
        self._write(data)
        logger.debug("Saved experience: %s (confidence=%.2f)", exp.key, exp.confidence)

    def delete(self, key: str) -> None:
        data = self._read()
        if key in data:
            del data[key]
            self._write(data)
            logger.info("Deleted experience: %s", key)

    def list_all(self) -> list[ElementExperience]:
        raw = self._read()
        return [ElementExperience.from_dict(v) for v in raw.values()]

    def list_by_area(self, area_hint: str) -> list[ElementExperience]:
        """按区域列出经验。"""
        return [e for e in self.list_all() if e.area_hint == area_hint]

    def find_by_feature(
        self,
        feature_name: str,
        feature_value: str,
        min_confidence: float = 0.0,
    ) -> list[ElementExperience]:
        """按指定特征值搜索经验。"""
        result = []
        for e in self.list_all():
            val = e.features.get(feature_name)
            if val is not None and str(val) == str(feature_value):
                if e.confidence >= min_confidence:
                    result.append(e)
        return result

    # ── 匹配（智能搜索，按权重打分） ──────────────────────────

    def search(
        self,
        *,
        text: str = "",
        automation_id: str = "",
        control_name: str = "",
        aria_label: str = "",
        min_confidence: float = 0.0,
        top_k: int = 3,
    ) -> list[tuple[ElementExperience, float]]:
        """多特征模糊搜索，返回 (经验, 得分) 列表按得分降序。

        权重高的特征优先匹配，支持部分匹配（contains）。
        """
        candidates = self.list_all()
        if min_confidence > 0:
            candidates = [c for c in candidates if c.confidence >= min_confidence]

        scored: list[tuple[ElementExperience, float]] = []
        for exp in candidates:
            score = 0.0
            total_weight = 0.0

            checks: list[tuple[str, str, float]] = [
                ("data_automationid", automation_id, 1.0),
                ("data_control_name", control_name, 0.95),
                ("aria_label", aria_label, 0.85),
                ("text", text, 0.65),
            ]

            for feat_name, query, weight in checks:
                if not query:
                    continue
                feat_val = str(exp.features.get(feat_name, "") or "")
                if query == feat_val:
                    score += weight * 1.0
                elif query.lower() in feat_val.lower():
                    score += weight * 0.7
                total_weight += weight

            if total_weight > 0:
                score = score / total_weight  # 归一化到 0~1
            else:
                score = exp.confidence  # 没有查询条件时用置信度

            # 置信度修正
            score *= (0.5 + exp.confidence * 0.5)

            if score > 0:
                scored.append((exp, score))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def get_stats(self) -> dict[str, Any]:
        """返回数据库统计信息。"""
        all_exp = self.list_all()
        return {
            "total_elements": len(all_exp),
            "avg_confidence": sum(e.confidence for e in all_exp) / max(len(all_exp), 1),
            "total_usage": sum(e.usage_count for e in all_exp),
            "total_success": sum(e.success_count for e in all_exp),
            "areas": list({e.area_hint for e in all_exp if e.area_hint}),
        }


# ── 便利函数 ──────────────────────────────────────────────────

def _build_dom_chain(el: dict) -> list[str]:
    """从 feature 提取的 element info 中构建 DOM chain。"""
    return el.get("dom_chain", [])


def _best_selector(el: dict) -> str:
    """从特征中生成最稳定的 CSS 选择器（只使用跨环境稳定的特征）。"""
    # 1) data-automationid
    aid = el.get("data_automationid") or el.get("dataAutomationId") or ""
    if aid:
        return f'[data-automationid="{aid}"]'
    # 2) data-control-name
    cn = el.get("data_control_name") or el.get("dataControlName") or ""
    if cn:
        return f'[data-control-name="{cn}"]'
    # 3) id
    id_val = el.get("id") or ""
    if id_val:
        return f"#{id_val}"
    # 4) tag + role
    tag = el.get("tag", "").lower() or ""
    role = el.get("role", "") or ""
    if role:
        return f"{tag}[role=\"{role}\"]"
    # 5) tag + text (text search)
    text = el.get("text", "") or ""
    if text:
        return f"{tag}:contains(\"{text}\")"
    return tag or "*"


__all__ = ["ElementExperience", "ExperienceDB", "_build_dom_chain", "_best_selector"]