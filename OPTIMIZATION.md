# MixCut Studio 性能改造说明

原版保留在 `MixCut-Studio-main`，这一份是照着瓶颈重做的。

## 一、改了什么

### 1. 新增 `mixcut/frame_pipe.py`

顺序帧管道：用 ffmpeg **解码一遍**，边解边按目标帧率缩小，通过 stdout 管道逐帧交给 Python。

- `iter_frames(path, fps=0.6, max_side=960, start=None, duration=None)` —— 生成 `(时间戳, BGR 帧)`
- `probe_media(path)` —— 宽高与时长
- `count_frames(...)` —— 预估帧数，给进度条用
- `scaled_size(...)` —— 按最长边算输出尺寸，保证偶数

### 2. 重写 `BadShotDetector.analyze_video`

原实现每个采样点都做一次：

```python
cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)   # 随机 seek
ok, frame = cap.read()
```

每次 seek 都要从最近的关键帧重新解码到目标帧。一条 1080p 十分钟的素材、
每 5 秒 3 帧，就是 360 次 seek，等于把片子解码几十遍。

现在改成一次顺序流：

```python
for timestamp, frame in frame_pipe.iter_frames(...):
    index = int(timestamp // window)
    q, gray = self._eval_frame(frame, previous_gray)
```

- 采样密度不变：`fps = sample_count / window`，默认 0.6fps，等价于每 5 秒 3 帧
- 帧在 ffmpeg 里就缩到最长边 960，Python 侧少搬一次像素
- `motion` 仍然用相邻采样帧计算，语义没变
- 对外接口 `analyze_video(...)` 签名与返回结构完全一致，上层不用改
- 解码异常不再拖垮整批分析，已采集到的窗口照常返回

## 二、怎么验证

先把 ffmpeg 放回项目根目录（原版 zip 里没带）：

```
ffmpeg-20200831-4a11a6f-win64-static\bin\ffmpeg.exe
ffmpeg-20200831-4a11a6f-win64-static\bin\ffprobe.exe
```

或者设置环境变量 `MIXCUT_FFMPEG` / `MIXCUT_FFPROBE` 指向可执行文件。

跑对比脚本：

```bat
py -3.14 tools\benchmark_frames.py 你的素材.mp4
```

输出会给出两种方式各自的耗时和加速比。

## 三、还没动的（按收益排序）

1. **OpenCLIP 抽帧**：`openclip_embed.py` 里仍然是逐点 `cap.set(CAP_PROP_POS_MSEC)`。
   已有磁盘缓存（按文件签名 + 时间戳），第二次跑能命中；但第一次仍会逐点 seek。
   正确做法是把待抽时间点排序后合并成若干区间，一次顺序流抽完再按时间匹配。
2. **批量推理**：`openclip_worker.py` 目前一帧一次 `encode_image`，
   GPU 利用率上不去。应该攒成 batch 再送进模型，或至少用 `torch.no_grad` + 更大的 batch size。
3. **librosa 整段 load**：`musicstudio.py` 里如果对整段音频做 BPM/节拍，
   一小时的 44.1kHz 就是六百多兆浮点。改成按 `offset/duration` 分段分析、
   或者用 soundfile 流式读，内存峰值能压下来一大截。
4. **分段处理长素材**：`frame_pipe.iter_frames` 已经支持 `start/duration`，
   超长素材可以按 60～120 秒切段跑，每段结束释放，内存更稳。

## 四、关于 Rust / C++ 重写

结论：现阶段不值得。

- 同一算法下，Rust 与 C++ 差距很小，语言本身只占几个百分点
- 现在的瓶颈在架构（随机 seek、单帧推理、整段加载），换语言一样慢
- 真要下沉，Rust 的优势是 PyO3 写 Python 扩展顺手、打包干净；
  C++ 的优势是能直接调 OpenCV 与 libav 原生接口，少一层绑定开销

建议顺序：先把上面三条架构问题改完、加计时和 profile，再判断有没有必要下沉。
