@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ==========================================
echo        MixCut Studio Windows Builder
echo ==========================================
where py >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Python 启动器 py，请安装 Python 3.14 并勾选 Add Python to PATH。
  pause
  exit /b 1
)
set "FFDIR=%~dp0ffmpeg\bin"
if not exist "%FFDIR%\ffmpeg.exe" (
  echo [错误] 缺少 ffmpeg.exe，请确认 ffmpeg\bin 目录完整。
  pause
  exit /b 1
)
if not exist "%FFDIR%\ffprobe.exe" (
  echo [错误] 缺少 ffprobe.exe。
  pause
  exit /b 1
)

echo [0/4] 检查是否还有 MixCutStudio.exe 在运行...
tasklist /FI "IMAGENAME eq MixCutStudio.exe" 2>nul | find /I "MixCutStudio.exe" >nul
if not errorlevel 1 (
  echo [错误] 请先关闭正在运行的 MixCutStudio.exe 再打包，否则 dist 目录会被占用。
  pause
  exit /b 1
)

echo [1/4] 检查/安装依赖（使用 Python 3.14）...
py -3.14 -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo [2/4] 清理旧的字节码缓存与构建缓存...
rem 关键：Python 只按“源文件修改时间(秒)”判断 .pyc 是否过期。
rem 同一秒内的改动会出现“界面代码是新的、workers 代码是旧的”这种混合旧包，
rem 所以打包前必须先删掉 __pycache__ 和 build 目录。
if exist "mixcut\ui\__pycache__" rmdir /s /q "mixcut\ui\__pycache__"
if exist "mixcut\__pycache__" rmdir /s /q "mixcut\__pycache__"
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "build\MixCutStudio" rmdir /s /q "build\MixCutStudio"

echo [3/4] 打包 EXE（首次构建需要几分钟）...
set PYTHONDONTWRITEBYTECODE=1
py -3.14 -m PyInstaller --noconfirm --clean MixCutStudio.spec
if errorlevel 1 goto fail

echo [4/4] 校验打包产物与源码是否一致...
py -3.14 tests\check_frozen_build.py
if errorlevel 1 goto fail

echo.
echo [完成] 程序已生成：dist\MixCutStudio\MixCutStudio.exe
echo 整个 dist\MixCutStudio 文件夹都需要保留，不能只复制 exe。
pause
exit /b 0
:fail
echo.
echo [失败] 构建过程出错，请检查上方错误信息。
pause
exit /b 1
