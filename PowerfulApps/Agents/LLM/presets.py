"""LLM 供应商预设管理 — 从 models_presets.json 加载/保存。"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_PRESETS_PATH = Path(__file__).resolve().parent / "models_presets.json"


@dataclass
class ProviderPreset:
    name: str                # 用户自定义名称，如 "deepseek-flash"
    manufacturer: str        # 厂商名，如 "deepseek"
    base_url: str
    model: str
    api_key: str
    thinking: bool = False
    reasoning_effort: str | None = None
    api_type: str = "openai-compatible"
    default: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderPreset:
        return cls(
            name=d.get("name", ""),
            manufacturer=d.get("manufacturer", ""),
            base_url=d.get("base_url", ""),
            model=d.get("model", ""),
            api_key=d.get("api_key", ""),
            thinking=d.get("thinking", False),
            reasoning_effort=d.get("reasoning_effort"),
            api_type=d.get("api_type", "openai-compatible"),
            default=d.get("default", False),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 加载 ──────────────────────────────────────────────────────

def _load_raw() -> dict[str, Any]:
    if not _PRESETS_PATH.exists():
        return {"presets": {}}
    return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))


def load_presets() -> dict[str, ProviderPreset]:
    """从嵌套 JSON 加载所有预设，按用户自定义名称扁平化为 {name: ProviderPreset}。"""
    raw = _load_raw()
    presets_data = raw.get("presets", {})
    result: dict[str, ProviderPreset] = {}
    for _manufacturer, models in presets_data.items():
        if not isinstance(models, dict):
            continue
        for _user_key, model_cfg in models.items():
            if isinstance(model_cfg, dict):
                preset = ProviderPreset.from_dict(model_cfg)
                result[preset.name] = preset
    return result


def load_preset(name: str) -> ProviderPreset | None:
    """通过用户自定义名称加载单个预设。"""
    presets = load_presets()
    return presets.get(name.strip().lower())


def load_default_preset() -> ProviderPreset | None:
    """加载标记为 default=true 的预设。如果没有，返回第一个。"""
    presets = load_presets()
    for p in presets.values():
        if p.default:
            return p
    # fallback: 第一个
    if presets:
        return next(iter(presets.values()))
    return None


def provider_names() -> list[str]:
    """返回所有可用的用户自定义名称列表。"""
    return sorted(load_presets().keys())


def get_manufacturers() -> list[str]:
    """返回所有厂商名列表。"""
    raw = _load_raw()
    return sorted(raw.get("presets", {}).keys())


def get_presets_by_manufacturer() -> dict[str, dict[str, ProviderPreset]]:
    """返回 {manufacturer: {name: ProviderPreset}} 分组结构。"""
    raw = _load_raw()
    result: dict[str, dict[str, ProviderPreset]] = {}
    for manufacturer, models in raw.get("presets", {}).items():
        if not isinstance(models, dict):
            continue
        group: dict[str, ProviderPreset] = {}
        for _user_key, model_cfg in models.items():
            if isinstance(model_cfg, dict):
                preset = ProviderPreset.from_dict(model_cfg)
                group[preset.name] = preset
        if group:
            result[manufacturer] = group
    return result


# ── 保存 ──────────────────────────────────────────────────────

def save_preset(name: str, preset: ProviderPreset) -> None:
    """保存/覆盖一个预设到 JSON（按 manufacturer 分组）。"""
    raw = _load_raw()
    manufacturer = preset.manufacturer or "other"
    raw.setdefault("presets", {}).setdefault(manufacturer, {})
    raw["presets"][manufacturer][name] = preset.to_dict()
    _write_raw(raw)


def delete_preset(name: str) -> bool:
    """删除一个预设（在所有厂商分组中搜索）。"""
    raw = _load_raw()
    name_lower = name.strip().lower()
    for manufacturer, models in raw.get("presets", {}).items():
        if not isinstance(models, dict):
            continue
        to_delete = None
        for user_key, model_cfg in models.items():
            if isinstance(model_cfg, dict) and model_cfg.get("name", "").strip().lower() == name_lower:
                to_delete = user_key
                break
        if to_delete:
            del models[to_delete]
            # 如果该厂商分组空了，也删掉
            if not models:
                del raw["presets"][manufacturer]
            _write_raw(raw)
            return True
    return False


def _write_raw(raw: dict[str, Any]) -> None:
    _PRESETS_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )