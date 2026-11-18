"""
MixCut · 素材分析结果内存缓存（逐素材）

按“单个素材”缓存分析结果：键 = 文件路径 + 文件大小 + 修改时间 + 检测配置
+ 分析来源。这样：

  · 58 个素材里只改了第 58 个，其余 57 个仍然命中缓存，不需要全部重检；
  · 取消勾选某个素材后重跑，剩下的素材全部秒过缓存；
  · 任务中途失败/取消时，已经检测完的素材结果会保留下来，下次直接复用。

缓存键里带“分析来源”（filter / legacy），两条检测路径互相隔离。

纯内存对象，不写磁盘：软件关闭、进程结束即自动释放。
"""

import hashlib
import json
import os


DEFAULT_SOURCE = "filter"   # 当前默认分析来源：智能粗剪筛选引擎
LEGACY_SOURCE = "legacy"    # 旧检测器路径（analyze_sources）


def _file_signature(path):
    """素材文件签名：路径 + 大小 + 修改时间；文件不存在时记录 missing。"""
    p = str(path)
    try:
        stat = os.stat(p)
    except OSError:
        return {"path": p, "missing": True}
    return {"path": p, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class AnalysisCache:
    def __init__(self):
        self._store = {}

    # ---- 逐素材键 ----
    @staticmethod
    def make_entry_key(path, options, source=None):
        payload = json.dumps(
            {
                "source": str(source or DEFAULT_SOURCE),
                "file": _file_signature(path),
                "options": options,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    # ---- 逐素材读写 ----
    def get_entry(self, path, options, source=None):
        """返回单个素材的分析结果，没有则 None。"""
        return self._store.get(self.make_entry_key(path, options, source))

    def set_entry(self, path, options, analysis_item, source=None):
        self._store[self.make_entry_key(path, options, source)] = analysis_item

    def get_many(self, paths, options, source=None):
        """批量读取：返回 (命中 {path: item}, 未命中 [path, ...])。"""
        hits = {}
        misses = []
        for path in paths:
            item = self.get_entry(path, options, source)
            if item is None:
                misses.append(path)
            else:
                hits[str(path)] = item
        return hits, misses

    # ---- 兼容旧接口（整批）----
    @staticmethod
    def make_key(paths, options, source=None):
        """整批缓存键（保留给旧调用；新代码请用逐素材接口）。"""
        payload = json.dumps(
            {
                "source": str(source or DEFAULT_SOURCE),
                "files": [_file_signature(p) for p in paths],
                "options": options,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def get(self, paths, options, source=None):
        return self._store.get(self.make_key(paths, options, source))

    def set(self, paths, options, analysis, source=None):
        """整批写入：拆成逐素材条目，避免整批键失效牵连所有素材。"""
        for item in analysis or []:
            path = (item.get("source") or {}).get("path")
            if path:
                self.set_entry(path, options, item, source=source)

    def clear(self):
        self._store.clear()

    def __len__(self):
        return len(self._store)
