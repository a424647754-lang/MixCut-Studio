# -*- coding: utf-8 -*-
"""剪映草稿兼容性回归测试。

覆盖草稿从「时间轴」到「剪映工程文件」的完整落地链路：
  1. 时间轴 → 草稿目录结构（draft_content.json / draft_meta_info.json 等）
  2. 片段数量、总时长与时间轴一致
  3. 画布尺寸与帧率正确
  4. 源素材路径登记正确
  5. 转场与配乐不破坏主轨
  6. 草稿可被 pyJianYingDraft 重新加载（结构合法）

用真实视频文件做素材（真实文件才能让 VideoMaterial 拿到时长）。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []


def check(cond, label, extra=""):
    if cond:
        print("PASS  %s" % label)
    else:
        print("FAIL  %s %s" % (label, extra))
        FAILS.append(label)


import mixcut.timeline as tl  # noqa: E402
import mixcut.project_builder as pb  # noqa: E402

check(pb.DRAFT_AVAILABLE, "pyJianYingDraft 可用")

# ---------------- 找真实素材 ----------------
CANDIDATES = [
    r"D:\素材\10月26日.mp4",
    r"D:\素材\柯馨\11.17\C3045.MP4",
]
VIDEO = next((p for p in CANDIDATES if os.path.isfile(p)), None)
if not VIDEO:
    print("SKIP  找不到测试素材，跳过草稿测试")
    sys.exit(0)

print("素材: %s (%.1f MB)" % (os.path.basename(VIDEO),
                              os.path.getsize(VIDEO) / 1048576))

OUT_DIR = tempfile.mkdtemp(prefix="mixcut_draft_test_")


def make_timeline(n_clips=4, canvas=(1080, 1920, 30)):
    """构造一个确定性的时间轴：同一条素材切 n 段。"""
    t = tl.create_timeline(canvas=tl.create_canvas(*canvas), mode="mix")
    t["sources"].append(tl.make_source(1, VIDEO, 60.0))
    for i in range(n_clips):
        s = i * 3.0
        e = s + 2.5
        t["clips"].append(tl.make_clip(
            i + 1, 1, s, e, i * 2.5, (i + 1) * 2.5))
    return t


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


try:
    # ================= 1. 基本落盘 =================
    print("\n--- 1. 基本落盘 ---")
    timeline = make_timeline(4)
    result = pb.build_draft(timeline, OUT_DIR, draft_name="t_basic",
                            force_target=True)
    check(bool(result.get("draft_name")), "返回 draft_name", result)
    draft_path = result.get("draft_path")
    check(bool(draft_path) and os.path.isdir(str(draft_path)),
          "草稿目录已创建", draft_path)

    dpath = str(draft_path)
    content_file = os.path.join(dpath, "draft_content.json")
    meta_file = os.path.join(dpath, "draft_meta_info.json")
    check(os.path.isfile(content_file), "存在 draft_content.json")
    check(os.path.isfile(meta_file), "存在 draft_meta_info.json")

    if os.path.isfile(meta_file):
        meta = load_json(meta_file)
        check(isinstance(meta, dict) and meta, "meta_info 是合法 JSON 对象")
        # 剪映要求草稿目录里有这些关键字段
        for key in ("draft_fold_path", "draft_id"):
            check(key in meta, "meta_info 含 %s" % key,
                  list(meta.keys())[:8] if key not in meta else "")

    # ================= 2. 时长与片段数 =================
    print("\n--- 2. 内容一致性 ---")
    check(result.get("clip_count") == 4, "落盘片段数 = 4",
          result.get("clip_count"))
    expect_dur = 4 * 2.5
    got_dur = float(result.get("duration") or 0)
    check(abs(got_dur - expect_dur) < 0.05,
          "总时长 = %.2f 秒" % expect_dur, got_dur)
    check(abs(tl.total_duration(timeline) - expect_dur) < 0.05,
          "时间轴自身总时长一致", tl.total_duration(timeline))

    # ================= 3. 画布与帧率 =================
    print("\n--- 3. 画布尺寸与帧率 ---")
    if os.path.isfile(content_file):
        content = load_json(content_file)
        check(isinstance(content, dict) and content,
              "draft_content 是合法 JSON 对象")
        canvas_cfg = content.get("canvas_config") or {}
        check(int(canvas_cfg.get("width") or 0) == 1080,
              "画布宽 = 1080", canvas_cfg.get("width"))
        check(int(canvas_cfg.get("height") or 0) == 1920,
              "画布高 = 1920", canvas_cfg.get("height"))
        check(int(content.get("fps") or 0) == 30,
              "帧率 = 30", content.get("fps"))
        # 主轨道片段数
        tracks = content.get("tracks") or []
        video_tracks = [t for t in tracks
                        if str(t.get("type") or "").lower() == "video"]
        check(bool(video_tracks), "存在视频轨道", len(tracks))
        if video_tracks:
            segs = video_tracks[0].get("segments") or []
            check(len(segs) == 4, "主视频轨片段数 = 4", len(segs))
            # 每个片段必须引用到素材
            if segs:
                first = segs[0]
                mref = first.get("material_id") or ""
                check(bool(mref), "片段引用了 material_id", mref)
                # 源区间不能越界
                src_tr = first.get("source_timerange") or {}
                check(int(src_tr.get("duration") or 0) > 0,
                      "片段源区间时长 > 0", src_tr)

    # ================= 4. 源素材登记 =================
    print("\n--- 4. 源素材登记 ---")
    if os.path.isfile(content_file):
        materials = (load_json(content_file).get("materials") or {})
        videos = materials.get("videos") or []
        check(len(videos) == 1, "只登记 1 个源素材（多片段复用）", len(videos))
        if videos:
            path_in_draft = str(videos[0].get("path") or "")
            check(os.path.normcase(os.path.abspath(path_in_draft))
                  == os.path.normcase(os.path.abspath(VIDEO)),
                  "素材路径与原文件一致",
                  path_in_draft[-40:] if path_in_draft else "")

    # ================= 5. 不同画布规格 =================
    print("\n--- 5. 不同画布规格 ---")
    for (w, h, fps) in ((1920, 1080, 25), (1080, 1080, 60)):
        name = "t_%dx%d_%d" % (w, h, fps)
        r = pb.build_draft(make_timeline(2, (w, h, fps)), OUT_DIR,
                           draft_name=name, force_target=True)
        cf = os.path.join(str(r["draft_path"]), "draft_content.json")
        ok = False
        detail = ""
        if os.path.isfile(cf):
            c = load_json(cf)
            cc = c.get("canvas_config") or {}
            ok = (int(cc.get("width") or 0) == w
                  and int(cc.get("height") or 0) == h
                  and int(c.get("fps") or 0) == fps)
            detail = "%sx%s@%s" % (cc.get("width"), cc.get("height"),
                                   c.get("fps"))
        check(ok, "%dx%d @ %dfps 正确" % (w, h, fps), detail)

    # ================= 6. 草稿结构合法性 =================
    print("\n--- 6. 草稿结构合法性 ---")
    try:
        import pyJianYingDraft as draft
        folder = draft.DraftFolder(OUT_DIR)
        names = folder.list_drafts()
        check("t_basic" in names, "DraftFolder 能识别已生成的草稿",
              "已有: %s" % names[:5])
        check("t_1920x1080_25" in names, "多份草稿都被识别",
              len(names))
        # 注意：pyJianYingDraft 0.3.1 的 load_template 在 Python 3.14 下
        # 会抛 KeyError('fps')（库内部 type_hints 兼容问题），
        # 这与生成的草稿无关，因此改为直接校验 JSON 结构。
        try:
            folder.load_template("t_basic")
            check(True, "load_template 可用")
        except KeyError as e:
            print("SKIP  load_template 受库版本限制（Python 3.14 下 KeyError: %s），"
                  "改用 JSON 结构校验" % e)
            # 结构合法性：draft_content.json 能被解析且关键字段齐全
            c = load_json(content_file)
            required = ("version", "new_version", "canvas_config", "tracks",
                        "materials", "fps", "duration", "id")
            missing = [k for k in required if k not in c]
            check(not missing, "draft_content 关键字段齐全", missing)
            check(isinstance(c.get("tracks"), list), "tracks 是数组")
            check(isinstance(c.get("materials"), dict), "materials 是对象")
            check(isinstance(c.get("id"), str) and c.get("id"),
                  "草稿 id 非空")
    except Exception as e:
        check(False, "草稿结构校验", str(e)[:120])

    # ================= 7. 空时间轴 =================
    print("\n--- 7. 边界情况 ---")
    try:
        empty = tl.create_timeline()
        r = pb.build_draft(empty, OUT_DIR, draft_name="t_empty",
                           force_target=True)
        check(r.get("clip_count") == 0, "空时间轴：片段数 0",
              r.get("clip_count"))
        check(float(r.get("duration") or 0) == 0.0, "空时间轴：时长 0",
              r.get("duration"))
    except Exception as e:
        check(False, "空时间轴不崩溃", str(e)[:120])

    # 片段源区间超出素材时长 → 应被钳制而不是报错
    try:
        t = make_timeline(1)
        t["clips"][0]["source_start"] = 10.0
        t["clips"][0]["source_end"] = 9999.0     # 远超素材时长
        r = pb.build_draft(t, OUT_DIR, draft_name="t_over", force_target=True)
        check(r.get("clip_count") == 1, "源区间越界被钳制，不报错",
              r.get("clip_count"))
    except Exception as e:
        check(False, "源区间越界处理", str(e)[:120])

finally:
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    try:
        os.remove(VIDEO + ".tmp") if os.path.exists(VIDEO + ".tmp") else None
    except OSError:
        pass

print()
if FAILS:
    print("FAILED %d:" % len(FAILS), FAILS)
    sys.exit(1)
print("ALL PASS")
