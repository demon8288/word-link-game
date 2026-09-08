"""单独打包脚本，避免 .bat 的奇怪行为"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(r"C:\Users\demonchan\Desktop\单词记忆\word_link_game")
os.chdir(ROOT)

print("[1/4] 生成音效/音乐...")
subprocess.check_call([sys.executable, "-c",
    "import sys; sys.path.insert(0, '.'); from word_link_game import ensure_audio_assets; ensure_audio_assets()"])

print("[2/4] 清理旧的 build/ 与 dist/...")
for d in ("build", "dist"):
    p = ROOT / d
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)

print("[3/4] 调用 PyInstaller 打包...")
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--onefile",
    "--windowed",
    "--name", "单词连连看",
    "--add-data", "data;data",
    "--add-data", "sounds;sounds",
    "--hidden-import", "PyQt5.sip",
    "word_link_game.py",
]
subprocess.check_call(cmd)

print("[4/4] 拷贝 data 和 sounds 到 dist/ (单文件 exe 旁边)...")
dist = ROOT / "dist"
d_data = dist / "data"; d_sound = dist / "sounds"
d_data.mkdir(parents=True, exist_ok=True)
d_sound.mkdir(parents=True, exist_ok=True)
for src_dir, dst_dir in [(ROOT / "data", d_data), (ROOT / "sounds", d_sound)]:
    for f in src_dir.iterdir():
        shutil.copy2(f, dst_dir / f.name)

print("\n✅ 打包完成：dist/单词连连看.exe")
