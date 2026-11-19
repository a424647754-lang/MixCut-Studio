# -*- coding: utf-8 -*-
"""工程自动保存与崩溃恢复。

设计要点
--------
**自动保存**
    · 每 60 秒检查一次，**仅当工程有改动（dirty）时**才写盘
    · 写入 ``<项目根>/_project_backups/<工程名>.autosave.mixcut``
      文件名继承工程名，多个工程互不覆盖
    · 全程静默，只在日志里留一行

**崩溃恢复**
    打开工程时先校验主文件；若主文件损坏（JSON 解析失败／结构不合法），
    自动在同目录下找对应的 ``.autosave.mixcut``，有则询问用户是否恢复。

本模块只负责"文件层面的存档与校验"，不依赖 Qt 以外的业务模块，
也不导入 main.py。
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

# 备份目录名（位于项目根目录下）
BACKUP_DIRNAME = "_project_backups"
# 自动保存文件后缀：<工程名>.autosave.mixcut
AUTOSAVE_SUFFIX = ".autosave.mixcut"
# 自动保存间隔（毫秒）
AUTOSAVE_INTERVAL_MS = 60 * 1000


def project_root():
    """返回项目根目录（本文件位于 <项目根>/mixcut/appkit/ 下）。"""
    return Path(__file__).resolve().parent.parent.parent


def backup_dir():
    """备份目录路径；不存在时自动创建。"""
    d = project_root() / BACKUP_DIRNAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def autosave_path(project_path):
    """由工程路径推导自动保存文件路径。

    例：``D:/桌面/大肥鱼测试工程.mixcut``
        → ``<项目根>/_project_backups/大肥鱼测试工程.autosave.mixcut``

    工程路径为空时返回 None（没有工程名可继承，不写自动保存）。
    """
    if not project_path:
        return None
    stem = Path(str(project_path)).stem
    if not stem:
        return None
    # 去掉可能残留的 .autosave，避免叠加成 xxx.autosave.autosave
    if stem.endswith(".autosave"):
        stem = stem[: -len(".autosave")]
    return str(backup_dir() / (stem + AUTOSAVE_SUFFIX))


def atomic_write_json(path, data):
    """原子写 JSON：先写同目录临时文件，fsync 后用 os.replace 替换。

    中途崩溃不会留下半截文件，也不会破坏原文件。
    返回 True 表示写入成功。
    """
    tmp = None
    try:
        folder = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".autosave_", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
        return True
    except Exception:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False


def validate_project_file(path):
    """校验工程文件是否为可用的 MixCut 工程。

    返回 ``(ok: bool, data_or_reason)``：
      · ok=True  → data_or_reason 是解析后的 dict
      · ok=False → data_or_reason 是失败原因（字符串）
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, "文件不存在"
    except json.JSONDecodeError as exc:
        return False, "JSON 解析失败：%s" % exc
    except OSError as exc:
        return False, "读取失败：%s" % exc
    except Exception as exc:
        return False, "读取失败：%s" % exc

    if not isinstance(data, dict):
        return False, "工程文件格式不正确（顶层不是对象）"
    if not isinstance(data.get("materials", []), list):
        return False, "素材清单格式不正确"
    lib = data.get("library", {})
    if lib is not None and not isinstance(lib, dict):
        return False, "素材库结构不正确"
    return True, data


def find_recovery(project_path):
    """为主工程查找可用的自动保存备份。

    返回 ``(path, data)`` 或 ``None``。只返回**校验通过**的备份。
    """
    cand = autosave_path(project_path)
    if not cand or not os.path.isfile(cand):
        return None
    ok, payload = validate_project_file(cand)
    if not ok:
        return None
    return cand, payload


def copy_as_autosave(project_path):
    """把现有工程复制一份为自动保存基线（用于『打开即备份』）。

    这样即使主文件在用户编辑期间损坏，也有一份与打开时一致的可恢复副本。
    """
    if not project_path or not os.path.isfile(project_path):
        return None
    dest = autosave_path(project_path)
    if not dest:
        return None
    try:
        shutil.copy2(project_path, dest)
        return dest
    except OSError:
        return None
