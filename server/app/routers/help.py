"""In-product documentation — serves the docs/*.md guides to the console UI so
users (not just GitHub readers) can read the board manual without leaving the app."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

router = APIRouter()

# docs/ ships next to the install. runtime_root() resolves to the exe's dir when
# frozen (Windows x64) and the repo root from source — __file__.parents[3] would
# point inside the PyInstaller _MEIPASS temp dir for the exe (docs not embedded).
from app.core.version import runtime_root

_DOCS_DIR = runtime_root() / "docs"

# Whitelisted docs (name → filename + display title). Order = display order.
_DOCS: list[tuple[str, str, str]] = [
    ("usage",        "USAGE.md",              "使用手册（各板块说明）"),
    ("config",       "CONFIG.md",             "配置项参考"),
    ("install",      "INSTALL.md",            "安装与部署"),
    ("integrations", "INTEGRATIONS.md",       "外部集成"),
    ("lingxing",     "lingxing-erp-guide.md", "领星 ERP 指南"),
    ("windows",      "windows-install.md",    "Windows 安装"),
]
_BY_NAME = {name: (fn, title) for name, fn, title in _DOCS}


@router.get("/help/docs")
def list_docs(_user: str = Depends(require_user)) -> dict:
    """List the docs that actually exist on disk (newest content wins)."""
    return {
        "docs": [
            {"name": name, "title": title}
            for name, fn, title in _DOCS
            if (_DOCS_DIR / fn).is_file()
        ]
    }


@router.get("/help/doc/{name}")
def get_doc(name: str, _user: str = Depends(require_user)) -> dict:
    """Return one doc's markdown content."""
    entry = _BY_NAME.get(name)
    if not entry:
        raise HTTPException(404, "unknown doc")
    fn, title = entry
    fp = _DOCS_DIR / fn
    if not fp.is_file():
        raise HTTPException(404, f"doc not found: {fn}")
    return {"name": name, "title": title, "markdown": fp.read_text(encoding="utf-8")}
