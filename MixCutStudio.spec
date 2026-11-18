# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
datas += collect_data_files('pyJianYingDraft')
datas += collect_data_files('librosa')
# TransNetV2（PyTorch 版）自带约 30MB 权重，必须一起打包，否则自检通过但推理时缺权重
datas += collect_data_files('transnetv2_pytorch')
# 界面资源（启动图、应用图标）与说明文档
datas += [
    ('assets', 'assets'),
    ('mixcut.ico', '.'),
]
import os as _os
if _os.path.isdir('功能说明'):
    datas += [('功能说明', '功能说明')]
if _os.path.isdir('docs'):
    datas += [('docs', 'docs')]

hiddenimports = []
# 本地核心包：显式列出，不再整体收集 mixcut 子模块，
# 避免把已删除的顺序粗剪 / 全自动渲染模块重新打进来。
hiddenimports += [
    'mixcut',
    'mixcut.analysis',
    'mixcut.analysis_cache',
    'mixcut.ffmpeg_tools',
    'mixcut.filter_engine',
    'mixcut.mixbuilder',
    'mixcut.musicstudio',
    'mixcut.project_builder',
    'mixcut.timeline',
    'mixcut.transitions',
    'mixcut.widgets',
    'mixcut.workers',
    # 新版四区界面
    'mixcut.ui',
    'mixcut.ui.theme',
    'mixcut.ui.components',
    'mixcut.ui.nav',
    'mixcut.ui.topbar',
    'mixcut.ui.shell',
    'mixcut.ui.library_page',
    'mixcut.ui.mix_page',
    'mixcut.ui.settings_page',
    'mixcut.ui.analyze_page',
    'mixcut.ui.splash',
    'mixcut.ui.engine_page',
    'mixcut.ui.output_page',
    # 新版 demo 界面（部分模块在函数内 import，显式兜底）
    'mixcut.ui.demo_shell',
    'mixcut.ui.demo',
    'mixcut.ui.demo.window',
    'mixcut.ui.demo.backend_bridge',
    'mixcut.ui.demo.pages_library',
    'mixcut.ui.demo.pages_work',
    'mixcut.ui.demo.pages_params',
    'mixcut.ui.demo.pages_settings',
    'mixcut.ui.demo.pages_analysis',
    'mixcut.ui.demo.pages_tasks_real',
    'mixcut.ui.demo.theme',
    'mixcut.ui.demo.ui_kit',
    'mixcut.ui.demo.mock',
    'mixcut.openclip_embed',
    'mixcut.preview_proxy',
]
# 音频链路（hooks-contrib 通常已覆盖，这里显式兜底）
hiddenimports += collect_submodules('librosa')
hiddenimports += ['soundfile']
# TransNetV2 推理链路：torch 由 PyInstaller 的 torch hook 负责收集 DLL，这里只补模块
hiddenimports += ['transnetv2_pytorch', 'ffmpeg', 'ffmpeg._run']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('ffmpeg\\bin\\ffmpeg.exe', '.'),
        ('ffmpeg\\bin\\ffprobe.exe', '.'),
    ],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'matplotlib', 'IPython', 'jupyter'],
    noarchive=False,
    optimize=2,   # 去 docstring / __debug__ 分支：产物只留必要字节码，无明文源码
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MixCutStudio',
    icon='assets\\app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MixCutStudio',
)
