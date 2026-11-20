"""撤销栈与剪辑命令。

所有对工程的修改都通过 Command 完成，保证撤销/重做/自动保存一致。
命令只保存"变更前后的片段列表"，因此天然可逆且不会漂移。
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from . import Clip, Project, Track, new_id

MAX_UNDO = 200


class Command:
    label = "操作"

    def redo(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def undo(self) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError


class UndoStack:
    def __init__(self, max_depth: int = MAX_UNDO) -> None:
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self.max_depth = max(1, int(max_depth))
        self.dirty = False

    # -- 查询 -------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def depth(self) -> int:
        return len(self._undo)

    # -- 操作 -------------------------------------------------------------
    def push(self, command: Command) -> None:
        command.redo()
        self._undo.append(command)
        self._redo.clear()
        if len(self._undo) > self.max_depth:
            self._undo.pop(0)
        self.dirty = True

    def undo(self) -> bool:
        if not self._undo:
            return False
        command = self._undo.pop()
        command.undo()
        self._redo.append(command)
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        command = self._redo.pop()
        command.redo()
        self._undo.append(command)
        self.dirty = True
        return True

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self.dirty = False

    def mark_clean(self) -> None:
        self.dirty = False


def _clip_list(project: Project, track_id: str) -> list[Clip]:
    track = project.track(track_id)
    return track.clips if track else []


def snapshot_clips(clips) -> list[Clip]:
    """片段列表的独立深拷贝快照（B-01 修复底座）。

    禁止拿 ``list(track.clips)`` 当快照：外壳复制了，元素仍是同一批 ``Clip``
    对象，调用方随后就地改字段，旧快照跟着一起变，``undo()`` 就成了空命令。
    这里统一走 ``Clip.from_dict(c.to_dict())``，保证快照与实时对象互不影响。
    """
    return [Clip.from_dict(c.to_dict()) for c in clips]


class TrackEditCommand(Command):
    """把若干轨道的片段列表整体替换为 after 状态。"""

    def __init__(self, project: Project, label: str) -> None:
        self.project = project
        self.label = label
        self._before: dict[str, list[Clip]] = {}
        self._after: dict[str, list[Clip]] = {}

    def record(self, track_id: str, before: list[Clip],
               after: list[Clip] | None = None) -> None:
        """记录一条轨道的变更。

        ``before`` 必须是"任何字段被改写之前"取得的独立快照（用
        :func:`snapshot_clips`），否则提交上来的已经是变更后的状态。
        ``after`` 省略时自动读取该轨道当前内容，避免调用方再传一次浅拷贝。
        两个入参在这里都会再深拷贝一次，命令自身持有副本，与调用方解耦。
        """
        if after is None:
            after = _clip_list(self.project, track_id)
        self._before[track_id] = copy.deepcopy(before)
        self._after[track_id] = copy.deepcopy(after)

    @property
    def empty(self) -> bool:
        return not self._before

    def _apply(self, state: dict[str, list[Clip]]) -> None:
        for track_id, clips in state.items():
            track = self.project.track(track_id)
            if track is not None:
                track.clips = copy.deepcopy(clips)
                track.clips.sort(key=lambda c: c.start)

    def redo(self) -> None:
        self._apply(self._after)

    def undo(self) -> None:
        self._apply(self._before)


class LambdaCommand(Command):
    def __init__(self, label: str, do: Callable[[], None], undo: Callable[[], None]) -> None:
        self.label = label
        self._do = do
        self._undo = undo

    def redo(self) -> None:
        self._do()

    def undo(self) -> None:
        self._undo()


class ClipPropertyCommand(Command):
    """片段属性修改（时间/音量/变速/不透明度/调色），可撤销。

    只记录真正变化的字段，避免撤销时把未编辑的属性一起还原。
    """

    def __init__(self, project: Project, label: str = "修改片段属性") -> None:
        self.project = project
        self.label = label
        self._changes: list[tuple[str, dict, dict]] = []

    def record(self, clip_id: str, before: dict, after: dict) -> None:
        diff_before, diff_after = {}, {}
        for key, value in after.items():
            old = before.get(key)
            if old != value:
                diff_before[key] = old
                diff_after[key] = value
        if diff_after:
            self._changes.append((clip_id, diff_before, diff_after))

    @property
    def empty(self) -> bool:
        return not self._changes

    def _apply(self, index: int) -> None:
        for clip_id, before, after in self._changes:
            clip = find_clip(self.project, clip_id)
            if clip is None:
                continue
            for key, value in (before if index == 0 else after).items():
                setattr(clip, key, value)

    def redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._apply(0)


class TrackStateCommand(Command):
    """轨道开关（静音/隐藏/锁定/高度/名称）修改，可撤销。"""

    FIELDS = ("muted", "hidden", "locked", "height", "name")

    def __init__(self, project: Project, label: str = "修改轨道") -> None:
        self.project = project
        self.label = label
        self._changes: list[tuple[str, dict, dict]] = []

    def record(self, track_id: str, before: dict, after: dict) -> None:
        diff_before, diff_after = {}, {}
        for key in self.FIELDS:
            if before.get(key) != after.get(key):
                diff_before[key] = before.get(key)
                diff_after[key] = after.get(key)
        if diff_after:
            self._changes.append((track_id, diff_before, diff_after))

    @property
    def empty(self) -> bool:
        return not self._changes

    @staticmethod
    def snapshot(track: Track) -> dict:
        return {key: getattr(track, key) for key in TrackStateCommand.FIELDS}

    def _apply(self, index: int) -> None:
        for track_id, before, after in self._changes:
            track = self.project.track(track_id)
            if track is None:
                continue
            for key, value in (before if index == 0 else after).items():
                setattr(track, key, value)

    def redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._apply(0)


def snapshot_tracks(project: Project) -> list[dict]:
    """轨道结构快照（含名字/顺序/开关/片段本体），必须深拷贝（B-27）。"""
    return [track.to_dict() for track in project.tracks]


class TrackListCommand(Command):
    """轨道结构变更（新增/删除/移动）：整张轨道表快照，可一步撤销。

    轨道增删改名原本只 `project_edited` 标脏、不进撤销栈（B-27），
    用户删错轨道无法回退。这里记录 before/after 两张完整的轨道表
    （`to_dict` 已是深拷贝语义），`_apply` 用 `Track.from_dict` 重建对象。
    """

    def __init__(self, project: Project, label: str = "修改轨道结构") -> None:
        self.project = project
        self.label = label
        self._before: list[dict] | None = None
        self._after: list[dict] | None = None

    def record(self, before: list[dict], after: list[dict] | None = None) -> None:
        if before == (after if after is not None else snapshot_tracks(self.project)):
            return
        self._before = copy.deepcopy(before)
        self._after = copy.deepcopy(
            snapshot_tracks(self.project) if after is None else after)

    @property
    def empty(self) -> bool:
        return self._before is None or self._after is None

    def _apply(self, index: int) -> None:
        data = self._before if index == 0 else self._after
        if data is None:
            return
        self.project.tracks = [Track.from_dict(item) for item in data]

    def redo(self) -> None:
        self._apply(1)

    def undo(self) -> None:
        self._apply(0)


# ---------------------------------------------------------------------------
# 纯函数：剪辑计划（可单测，不直接改数据）
# ---------------------------------------------------------------------------

def plan_split(clip: Clip, at: int, min_len: int = 1) -> tuple[Clip, Clip] | None:
    """在 at 处把片段切成左右两段；返回 (left, right)。"""
    if not clip.covers(at):
        return None
    if at - clip.start < min_len or clip.end - at < min_len:
        return None
    left = copy.deepcopy(clip)
    left.duration = at - clip.start
    right = copy.deepcopy(clip)
    right.id = new_id()
    right.start = at
    right.source_in = clip.source_in + int((at - clip.start) * clip.speed)
    right.duration = clip.end - at
    return left, right


def split_track(track: Track, at: int) -> list[tuple[Clip, Clip]] | None:
    """切割轨道上位于 at 的片段，直接修改 track.clips。"""
    if track.locked:
        return None
    for index, clip in enumerate(track.clips):
        plans = plan_split(clip, at)
        if plans:
            left, right = plans
            track.clips[index] = left
            track.clips.insert(index + 1, right)
            track.clips.sort(key=lambda c: c.start)
            return [(left, right)]
    return None


def find_clip(project: Project, clip_id: str) -> Clip | None:
    _, clip = project.locate(clip_id)
    return clip


def remove_clip(project: Project, track_id: str, clip_id: str) -> bool:
    track = project.track(track_id)
    if track is None or track.locked:
        return False
    clip = track.find(clip_id)
    if clip is None:
        return False
    track.clips.remove(clip)
    return True


def ripple_shift(track: Track, from_time: int, delta: int) -> None:
    """把 from_time 及之后的片段整体平移 delta 毫秒。"""
    for clip in track.clips:
        if clip.start >= from_time:
            clip.start = max(0, clip.start + delta)
    track.clips.sort(key=lambda c: c.start)


def sort_tracks(project: Project) -> None:
    for track in project.tracks:
        track.clips.sort(key=lambda c: c.start)


def clip_overlaps(track: Track, start: int, duration: int,
                  ignore_id: str | None = None) -> bool:
    end = start + duration
    for clip in track.clips:
        if ignore_id and clip.id == ignore_id:
            continue
        if start < clip.end and clip.start < end:
            return True
    return False


def overwrite_layout(existing: list[Clip], incoming: Clip) -> list[Clip]:
    """计算覆盖式插入后的片段列表（纯函数，便于单测）。

    与 incoming 重叠的旧片段会被切成左右两段保留，被完全覆盖的则移除。
    """
    new_start, new_end = incoming.start, incoming.end
    result: list[Clip] = []
    for old in existing:
        old_start, old_end = old.start, old.end
        if old_end <= new_start or old_start >= new_end:
            result.append(Clip.from_dict(old.to_dict()))
            continue
        if old_start < new_start:
            head = Clip.from_dict(old.to_dict())
            head.duration = new_start - old_start
            result.append(head)
        if old_end > new_end:
            tail = Clip.from_dict(old.to_dict())
            tail.id = new_id()
            tail.start = new_end
            tail.source_in = old.source_in + int((new_end - old_start) * old.speed)
            tail.duration = old_end - new_end
            result.append(tail)
    result.append(Clip.from_dict(incoming.to_dict()))
    return sorted(result, key=lambda c: c.start)
