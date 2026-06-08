"""PowerApps Agent 项目记忆。

持久化内容：
- projects.json：项目名称、URL、标题、MD 文档路径
- 每个项目一个 markdown：屏幕/元素/属性/用途说明

短期对话记忆不在这里持久化。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass
class ProjectRecord:
    name: str
    url: str
    title: str
    md_path: str
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectRecord":
        return cls(
            name=data["name"],
            url=data["url"],
            title=data.get("title", data["name"]),
            md_path=data["md_path"],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "title": self.title,
            "md_path": self.md_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectMemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.projects_dir = root / "projects"
        self.index_path = root / "projects.json"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index({"projects": []})

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"projects": []}
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    def _write_index(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
        return slug or "project"

    @staticmethod
    def _url_key(url: str) -> str:
        parsed = urlparse(url.strip())
        return f"{parsed.netloc}{parsed.path}".lower().rstrip("/")

    def list_projects(self) -> list[ProjectRecord]:
        return [ProjectRecord.from_dict(item) for item in self._read_index().get("projects", [])]

    def get(self, name: str) -> ProjectRecord | None:
        for project in self.list_projects():
            if project.name == name:
                return project
        return None

    def find_by_url(self, url: str) -> ProjectRecord | None:
        key = self._url_key(url)
        for project in self.list_projects():
            if self._url_key(project.url) == key:
                return project
        return None

    def upsert(self, name: str, url: str, title: str | None = None) -> ProjectRecord:
        now = datetime.now().isoformat(timespec="seconds")
        data = self._read_index()
        projects = data.setdefault("projects", [])
        existing_idx = None
        for i, item in enumerate(projects):
            if item.get("name") == name:
                existing_idx = i
                break

        if existing_idx is not None:
            old = ProjectRecord.from_dict(projects[existing_idx])
            record = ProjectRecord(
                name=name,
                url=url,
                title=title or old.title or name,
                md_path=old.md_path,
                created_at=old.created_at,
                updated_at=now,
            )
            projects[existing_idx] = record.to_dict()
        else:
            md_path = self.projects_dir / f"{self._slug(name)}.md"
            record = ProjectRecord(
                name=name,
                url=url,
                title=title or name,
                md_path=str(md_path),
                created_at=now,
                updated_at=now,
            )
            projects.append(record.to_dict())
            self._ensure_project_doc(record)

        self._write_index(data)
        self._ensure_project_doc(record)
        return record

    def _ensure_project_doc(self, project: ProjectRecord) -> None:
        path = Path(project.md_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        path.write_text(
            f"# {project.title}\n\n"
            f"- 项目名称：{project.name}\n"
            f"- URL：{project.url}\n"
            f"- 创建时间：{project.created_at}\n\n"
            "## 说明\n\n"
            "这个文档用于记录 PowerApps 项目中每个屏幕、每个元素的用途，以及被修改过的属性公式。\n"
            "未修改的属性不需要记录。\n\n"
            "## 屏幕与元素\n\n",
            encoding="utf-8",
        )

    def read_doc(self, project: ProjectRecord) -> str:
        self._ensure_project_doc(project)
        return Path(project.md_path).read_text(encoding="utf-8")

    def append_doc(self, project: ProjectRecord, content: str) -> None:
        if not content.strip():
            return
        self._ensure_project_doc(project)
        path = Path(project.md_path)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n### 更新 {timestamp}\n\n{content.strip()}\n")
