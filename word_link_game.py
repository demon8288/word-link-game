# -*- coding: utf-8 -*-
"""
单词连连看 - 趣味单词记忆游戏
功能：
  - 10 关递进难度，每关从词库随机抽 6/8/10/12/14 对单词（英文 vs 中文）
  - 鼠标连两条线，正确播放成功音效，错误播放错误音效，全部配对完成后进入下一关
  - 背景舒缓电子音乐（可静音切换）
  - 上传/重载词库入口
"""

import os
import sys
import json
import random
import wave
import struct
import math
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QPoint, QPointF, QRectF, QTimer, QPropertyAnimation, QEasingCurve,
    pyqtSignal, QObject, QSize, QUrl
)
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QLinearGradient, QPainterPath, QFont,
    QPalette, QIcon, QPolygonF, QRadialGradient, QCursor, QPixmap
)
from PyQt5.QtMultimedia import QSoundEffect, QMediaPlayer, QMediaContent
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFrame, QFileDialog, QMessageBox, QGraphicsDropShadowEffect,
    QSizePolicy, QSpacerItem, QStackedWidget, QGridLayout,
    QDialog, QTextEdit, QDialogButtonBox
)
from PyQt5.QtGui import QGuiApplication  # 用于剪贴板


# ============== 路径处理（兼容 PyInstaller 打包） ==============
def resource_path(rel: str) -> str:
    """获取资源绝对路径，兼容开发与打包后两种模式"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, rel)
    if os.path.exists(p):
        return p
    # 备选：脚本所在目录
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


APP_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = APP_DIR / "data"
SOUNDS_DIR = APP_DIR / "sounds"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SOUNDS_DIR.mkdir(parents=True, exist_ok=True)


# ============== 音效 / 音乐自动生成（无外部素材也能跑） ==============
def _write_wav(path: Path, samples, framerate=44100):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        for s in samples:
            v = int(max(-1, min(1, s)) * 32767)
            wf.writeframesraw(struct.pack("<h", v))


def _sine(freq, t, sr=44100):
    return math.sin(2 * math.pi * freq * t / sr)


def _envelope(t, attack=0.01, release=0.18, total=0.25, sr=44100):
    n = t
    dur = total
    if n < attack * sr:
        return n / (attack * sr)
    if n > (dur - release) * sr:
        return max(0, (dur - n / sr) / release)
    return 1.0


def gen_correct_sound(path: Path):
    """配对成功：明亮上扬双音（C5 -> G5）"""
    sr = 44100
    dur = 0.45
    n = int(dur * sr)
    out = []
    for i in range(n):
        t = i / sr
        # 上行琶音 C5(523.25) -> E5(659.25) -> G5(783.99)
        if t < 0.15:
            f = 523.25
            amp = 0.6
        elif t < 0.30:
            f = 659.25
            amp = 0.6
        else:
            f = 783.99
            amp = 0.7
        env = math.exp(-3 * (t - 0.30))
        out.append(amp * env * _sine(f, t, sr))
    _write_wav(path, out, sr)


def gen_wrong_sound(path: Path):
    """配对失败：低沉下行"""
    sr = 44100
    dur = 0.5
    n = int(dur * sr)
    out = []
    for i in range(n):
        t = i / sr
        f = 220.0 * (1 - 0.5 * t)  # 下行
        env = math.exp(-4 * t)
        out.append(0.55 * env * _sine(f, t, sr))
    _write_wav(path, out, sr)


def gen_click_sound(path: Path):
    """点击按钮：清脆短音"""
    sr = 44100
    dur = 0.08
    n = int(dur * sr)
    out = []
    for i in range(n):
        t = i / sr
        f = 880.0
        env = math.exp(-30 * t)
        out.append(0.45 * env * _sine(f, t, sr))
    _write_wav(path, out, sr)


def gen_win_sound(path: Path):
    """通关成功：上行音阶"""
    sr = 44100
    notes = [523.25, 659.25, 783.99, 1046.50]
    out = []
    for idx, f in enumerate(notes):
        n = int(0.18 * sr)
        for i in range(n):
            t = i / sr
            env = math.exp(-5 * t) * 0.55
            out.append(env * _sine(f, t, sr))
    _write_wav(path, out, sr)


def gen_bgm(path: Path, duration=48):
    """生成一段舒缓电子风 Lo-fi 背景音乐（自动循环）
       风格：Cmaj7 和弦铺底 + 缓慢琶音 + 轻垫低音
    """
    sr = 44100
    # Cmaj7: C E G B; Am7: A C E G; FM7: F A C E; G6: G B D E
    chords = [
        [261.63, 329.63, 392.00, 493.88],   # Cmaj7
        [220.00, 261.63, 329.63, 392.00],   # Am7
        [174.61, 220.00, 261.63, 329.63],   # FM7
        [196.00, 246.94, 293.66, 329.63],   # G6
    ]
    samples = []
    beat_samples = int(sr * 0.5)  # 每半秒换一个音
    total_samples = int(duration * sr)
    rng = random.Random(42)

    # 全局低通滤波效果（柔和）：简单指数滑动平均近似
    prev = 0.0
    alpha = 0.18
    bass_freq = 110.0

    for i in range(total_samples):
        t = i / sr
        # 当前和弦（每 4 秒换）
        chord_idx = int(t / 4) % len(chords)
        chord = chords[chord_idx]
        # 和弦铺底
        pad = 0.0
        for f in chord:
            pad += _sine(f, t, sr)
        pad = pad / len(chord) * 0.18
        # 琶音：每和弦中每 0.5s 奏下一个音（高八度）
        arp_step = int(t / 0.5) % 4
        arp_f = chord[arp_step] * 2
        arp_env = math.exp(-6 * (t * 2 % 0.5))
        arp = 0.22 * arp_env * _sine(arp_f, t, sr)
        # 低音：4 秒换一次根音
        root = chord[0] / 2
        bass_env = 0.5 + 0.5 * math.sin(2 * math.pi * t / 4.0)
        bass = 0.30 * bass_env * _sine(root, t, sr)
        # 总体
        sample = (pad + arp + bass) * 0.55
        # 低通柔化
        prev = prev + alpha * (sample - prev)
        samples.append(prev * 0.9)

        # 避免每帧换和弦开头爆音
        if i % beat_samples == 0:
            prev = prev * 0.6

    # 末尾淡出
    fade_n = int(sr * 1.5)
    for i in range(min(fade_n, len(samples))):
        samples[-(i + 1)] *= i / fade_n

    _write_wav(path, samples, sr)


def ensure_audio_assets():
    """确保音效文件存在。优先使用已存在于 sounds/ 的真实音效，
    如果某个文件缺失才用代码合成兜底。
    """
    fallback = {
        "correct.wav": gen_correct_sound,
        "wrong.wav": gen_wrong_sound,
        "click.wav": gen_click_sound,
        "win.wav": gen_win_sound,
        "bgm.wav": gen_bgm,
    }
    for name, fn in fallback.items():
        p = SOUNDS_DIR / name
        if not p.exists() or p.stat().st_size < 5000:
            # 文件不存在或太小（之前的合成音效只有几百字节）→ 用合成兜底
            try:
                fn(p)
                print(f"[info] 音效 {name} 缺失，已用合成音效替代")
            except Exception as e:
                print(f"[warn] 生成 {name} 失败：{e}")


# ============== 词库解析 ==============
def parse_words_file(path: Path):
    """解析 txt 词库，支持三种格式：
       1) JS 字面量风格：{ word: "x", meaning: "y", ... }
       2) 每行一个：英文 | 中文
       3) 每行一个：英文 | 中文 | 例句
       返回 list[dict(word, meaning, sentence, unit)]
    """
    words = []
    if not path.exists():
        return words
    text = path.read_text(encoding="utf-8", errors="ignore")
    # 尝试按 JS 字面量解析
    import re
    pattern = re.compile(
        r'word\s*:\s*["\']([^"\']+)["\']\s*,\s*meaning\s*:\s*["\']([^"\']+)["\'](?:\s*,\s*sentence\s*:\s*["\']([^"\']*)["\'])?(?:\s*,\s*unit\s*:\s*["\']([^"\']*)["\'])?',
        re.DOTALL,
    )
    matches = pattern.findall(text)
    if matches and len(matches) >= 5:
        for w, m, s, u in matches:
            words.append({"word": w.strip(), "meaning": m.strip(),
                          "sentence": s.strip(), "unit": u.strip()})
        return words

    # 否则按行解析
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                w = parts[0]
                m = parts[1]
                s = parts[2] if len(parts) >= 3 else ""
                words.append({"word": w, "meaning": m, "sentence": s, "unit": ""})
    return words


# ============== 关卡配置 ==============
def level_config(level: int):
    """返回 (单词对数, 行数, 列数, 限时秒数)
    每关固定 10 对 = 20 张卡（5x4 网格），难度由倒计时递增：
    L1 限时 120s（充裕），L10 限时 45s（紧张）。
    """
    PAIRS = 10
    ROWS, COLS = 5, 4   # 5x4 = 20 个卡位
    TIMES = [120, 110, 100, 90, 80, 72, 65, 58, 52, 45]
    secs = TIMES[max(0, min(level - 1, len(TIMES) - 1))]
    return PAIRS, ROWS, COLS, secs


# ============== UI 控件 ==============
class CardButton(QPushButton):
    """一个卡片：英文或中文"""
    def __init__(self, text, is_word=True, parent=None):
        super().__init__(text, parent)
        self.is_word = is_word
        self.pair_id = None  # 与之配对的卡片 id
        self.matched = False
        self.disappearing = False
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(58)
        font = QFont("Microsoft YaHei", 12)
        font.setBold(True)
        self.setFont(font)
        self._update_style()

    def set_matched(self, ok=True):
        self.matched = ok
        self.setChecked(False)
        self._update_style()

    def _update_style(self):
        if self.matched:
            # 配对成功：浅绿色，弱化
            self.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #C8F7C5, stop:1 #88D8A3);
                    border: 2px solid #4CAF50;
                    border-radius: 12px;
                    color: #2E7D32;
                    padding: 6px;
                }
            """)
        elif self.isChecked():
            # 选中：金色高亮
            self.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #FFE082, stop:1 #FFB300);
                    border: 2px solid #FF8F00;
                    border-radius: 12px;
                    color: #4E342E;
                    padding: 6px;
                }
            """)
        else:
            if self.is_word:
                # 英文卡：蓝紫
                self.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 #B3E5FC, stop:1 #4FC3F7);
                        border: 2px solid #0288D1;
                        border-radius: 12px;
                        color: #0D47A1;
                        padding: 6px;
                    }
                    QPushButton:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 #FFE0B2, stop:1 #FFB74D);
                        border: 2px solid #F57C00;
                        color: #BF360C;
                    }
                """)
            else:
                # 中文卡：粉橙
                self.setStyleSheet("""
                    QPushButton {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 #FFCDD2, stop:1 #F48FB1);
                        border: 2px solid #C2185B;
                        border-radius: 12px;
                        color: #880E4F;
                        padding: 6px;
                    }
                    QPushButton:hover {
                        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                            stop:0 #FFE0B2, stop:1 #FFB74D);
                        border: 2px solid #F57C00;
                        color: #BF360C;
                    }
                """)


class TitleBar(QFrame):
    """顶部标题"""
    def __init__(self, title="单词连连看", subtitle="鼠标把英文连到对应中文 ~ 闯 10 关即可通关！"):
        super().__init__()
        self.setObjectName("TitleBar")
        self.setStyleSheet("""
            #TitleBar {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4A148C, stop:0.5 #7B1FA2, stop:1 #E91E63);
                border-bottom: 4px solid #FFEB3B;
                border-radius: 0px;
            }
            QLabel#title { color: white; font-family: 'Microsoft YaHei'; font-weight: 900; }
            QLabel#subtitle { color: #FFE082; font-family: 'Microsoft YaHei'; }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 12, 20, 12)
        v = QVBoxLayout()
        t = QLabel(title)
        t.setObjectName("title")
        f = QFont("Microsoft YaHei", 22)
        f.setBold(True)
        t.setFont(f)
        s = QLabel(subtitle)
        s.setObjectName("subtitle")
        s.setFont(QFont("Microsoft YaHei", 10))
        v.addWidget(t)
        v.addWidget(s)
        lay.addLayout(v)
        lay.addStretch(1)


class LineOverlay(QWidget):
    """覆盖在卡片上层的连线层"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.lines = []  # list of (p1, p2, color, life_ms)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(30)

    def add_line(self, p1, p2, color=QColor("#FF6F00")):
        self.lines.append([p1, p2, color, 600])

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        new_lines = []
        for ln in self.lines:
            p1, p2, color, life = ln
            pen = QPen(color, 4, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(p1, p2)
            ln[3] -= 30
            if ln[3] > 0:
                new_lines.append(ln)
        self.lines = new_lines


class GameBoard(QFrame):
    """游戏主面板"""
    match_correct = pyqtSignal()
    match_wrong = pyqtSignal()
    level_complete = pyqtSignal(int)  # 关卡号
    game_complete = pyqtSignal()
    game_failed = pyqtSignal(int)     # 关卡号（倒计时归零）

    def __init__(self, words, parent=None):
        super().__init__(parent)
        self.words = words  # 当前关卡词库
        self.cards = []  # CardButton 列表
        self.selected = []  # 已选卡片
        self.current_level = 1
        self.score = 0
        self.time_left = 0

        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #FFF8E1, stop:1 #FFE0B2);
                border-radius: 8px;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(10)

        # 顶部状态行
        info = QHBoxLayout()
        self.level_label = QLabel("第 1 关")
        self.score_label = QLabel("得分：0")
        self.timer_label = QLabel("")
        self.status_label = QLabel("把英文卡连到对应的中文卡吧！")
        for lbl in (self.level_label, self.score_label, self.timer_label, self.status_label):
            f = QFont("Microsoft YaHei", 12)
            f.setBold(True)
            lbl.setFont(f)
            lbl.setStyleSheet("color: #4A148C;")
            info.addWidget(lbl)
        info.addStretch(1)
        outer.addLayout(info)

        # 卡片网格 + 连线层
        self.grid_widget = QWidget()
        self.grid_widget.setMinimumSize(820, 480)
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setContentsMargins(8, 8, 8, 8)
        self.overlay = LineOverlay(self.grid_widget)

        outer.addWidget(self.grid_widget, 1)

        # 倒计时
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.overlay.setGeometry(self.grid_widget.rect())

    def load_level(self, level: int):
        self.current_level = level
        pairs, rows, cols, secs = level_config(level)

        # 清空旧卡
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.cards = []
        self.selected = []
        self.lines = []

        if len(self.words) < pairs:
            QMessageBox.warning(self, "提示",
                                f"词库只有 {len(self.words)} 个单词，无法生成第 {level} 关（需要 {pairs} 个）\n请导入更多单词。")
            return

        chosen = random.sample(self.words, pairs)

        # 每对生成两张卡：英文+中文，打乱位置
        all_cards = []
        for idx, item in enumerate(chosen):
            cw = CardButton(item["word"], is_word=True)
            cm = CardButton(item["meaning"], is_word=False)
            cw.pair_id = idx
            cm.pair_id = idx
            cw.clicked.connect(lambda _, b=cw: self._on_click(b))
            cm.clicked.connect(lambda _, b=cm: self._on_click(b))
            self.cards.extend([cw, cm])
            all_cards.append(cw)
            all_cards.append(cm)

        random.shuffle(all_cards)

        for i, c in enumerate(all_cards):
            r, col = divmod(i, cols)
            self.grid_layout.addWidget(c, r, col)

        self.level_label.setText(f"第 {level} 关（{pairs}对 → 2×{pairs}卡）")
        self.status_label.setText(f"点击两张卡进行配对，本关共 {pairs} 对。")
        self.score_label.setText(f"得分：{self.score}")

        if secs > 0:
            self.time_left = secs
            self.timer_label.setText(f"⏱ {self.time_left}s")
            self.timer.start(1000)
        else:
            self.time_left = 0
            self.timer_label.setText("⏱ 自由模式")
            self.timer.stop()

    def _on_tick(self):
        self.time_left -= 1
        if self.time_left <= 0:
            self.timer.stop()
            self.timer_label.setText("⏱ 时间到！")
            self.status_label.setText("⏰ 倒计时结束，挑战失败！")
            self.game_failed.emit(self.current_level)
            return
        self.timer_label.setText(f"⏱ {self.time_left}s")
        # 倒计时进入最后 10 秒变红提示
        if self.time_left <= 10:
            self.timer_label.setStyleSheet("color: #D32F2F; font-weight: bold;")
        else:
            self.timer_label.setStyleSheet("color: #4A148C; font-weight: bold;")

    def _on_click(self, btn: CardButton):
        if btn.matched:
            return
        # 已选两张则忽略
        if len(self.selected) >= 2:
            return

        # 切换 check 状态
        btn.setChecked(True)
        btn._update_style()
        self.selected.append(btn)

        # 画连线预览
        if len(self.selected) == 2:
            a, b = self.selected
            if a is b:
                self.selected = []
                a.setChecked(False)
                a._update_style()
                return
            p1 = a.mapTo(self.grid_widget, QPoint(0, 0))
            p2 = b.mapTo(self.grid_widget, QPoint(0, 0))
            p1 = QPoint(p1.x() + a.width() // 2, p1.y() + a.height() // 2)
            p2 = QPoint(p2.x() + b.width() // 2, p2.y() + b.height() // 2)
            self.overlay.add_line(p1, p2, QColor("#FF6F00"))

            # 判断：必须一张英文 + 一张中文，且 pair_id 相同
            if a.is_word != b.is_word and a.pair_id == b.pair_id:
                QTimer.singleShot(200, lambda: self._do_correct(a, b))
            else:
                QTimer.singleShot(350, lambda: self._do_wrong(a, b))

    def _do_correct(self, a, b):
        # 配对成功 → 设置 matched 状态 → 播放"消失"动画
        a.set_matched(True)
        b.set_matched(True)
        self.score += 10
        self.score_label.setText(f"得分：{self.score}")
        self.status_label.setText("✅ 配对正确！")
        self.match_correct.emit()

        # 动画1：高亮闪烁
        QTimer.singleShot(150, lambda: self._animate_disappear(a, b))

        # 判断是否本关完成
        self.selected = []

    def _animate_disappear(self, a, b):
        """消失动画：先轻微高亮提示，然后缩小 + 透明度淡出"""
        a.disappearing = True
        b.disappearing = True

        # 1) 一闪提示：只保留一个动画，逐个启动避免重叠
        # 2) 同时缩放 + 透明度
        for w in (a, b):
            # 禁用交互
            w.setEnabled(False)

            # 几何动画：缩放到 60%
            start_rect = QRectF(w.geometry())
            end_rect = QRectF(
                start_rect.x() + start_rect.width() * 0.2,
                start_rect.y() + start_rect.height() * 0.2,
                start_rect.width() * 0.6,
                start_rect.height() * 0.6,
            )
            anim_geo = QPropertyAnimation(w, b"geometry")
            anim_geo.setDuration(420)
            anim_geo.setStartValue(start_rect)
            anim_geo.setEndValue(end_rect)
            anim_geo.setEasingCurve(QEasingCurve.InQuad)

            # 透明度动画
            anim_op = QPropertyAnimation(w, b"windowOpacity")
            anim_op.setDuration(420)
            anim_op.setStartValue(1.0)
            anim_op.setEndValue(0.0)
            anim_op.setEasingCurve(QEasingCurve.InQuad)

            anim_geo.start()
            anim_op.start()
            w._anim_geo = anim_geo
            w._anim_op = anim_op

        # 动画完成后隐藏卡
        def finish_disappear():
            self._remove_cards(a, b)
            # 检查本关是否完成：cards 列表只剩未配对的，为空说明完成了
            if len(self.cards) == 0:
                QTimer.singleShot(150, self._finish_level)

        QTimer.singleShot(450, finish_disappear)

    def _remove_cards(self, a, b):
        """卡片配对后：隐藏但保留占位，不移除布局，不改变其他卡位置"""
        for c in (a, b):
            c.hide()  # 只隐藏，从grid_layout移除会触发重排
        # 仍然从 cards 列表移除，避免影响通关检测(all matched)
        if a in self.cards:
            self.cards.remove(a)
        if b in self.cards:
            self.cards.remove(b)

    def _do_wrong(self, a, b):
        a.setChecked(False)
        a._update_style()
        b.setChecked(False)
        b._update_style()
        self.status_label.setText("❌ 配对错误，再试试！")
        self.score = max(0, self.score - 2)
        self.score_label.setText(f"得分：{self.score}")
        self.match_wrong.emit()
        self.selected = []

    def _finish_level(self):
        self.timer.stop()
        if self.current_level >= 10:
            self.game_complete.emit()
        else:
            self.level_complete.emit(self.current_level)


class WelcomePage(QFrame):
    """欢迎界面"""
    start_game = pyqtSignal()
    upload_words = pyqtSignal()
    open_words_dir = pyqtSignal()
    upload_bgm = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1A237E, stop:0.5 #4527A0, stop:1 #880E4F);
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)

        title = QLabel("🎮 单词连连看")
        title.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei", 38)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #FFEB3B;")

        subtitle = QLabel("闯 10 关英语单词，边玩边记！")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setFont(QFont("Microsoft YaHei", 16))
        subtitle.setStyleSheet("color: white;")

        intro = QLabel(
            "🖱 玩法：点一张英文卡，再点对应的中文卡 → 配对成功，卡片消失！\n"
            "⏱ 每关 10 对单词，倒计时内完成（120s → 45s）。超时即挑战失败。\n"
            "🎵 关卡有舒缓背景音乐，配对有成功 / 失败音效\n"
            "📚 上传自己的 txt 单词表（先看示例，按 英文 | 中文 格式填）"
        )
        intro.setAlignment(Qt.AlignCenter)
        intro.setFont(QFont("Microsoft YaHei", 12))
        intro.setStyleSheet("color: #B3E5FC;")
        intro.setWordWrap(True)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(18)

        def mkbtn(text, color, color2):
            b = QPushButton(text)
            b.setMinimumSize(180, 56)
            b.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 {color}, stop:1 {color2});
                    border: 2px solid #FFEB3B;
                    border-radius: 18px;
                    color: white;
                    padding: 10px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 {color2}, stop:1 {color});
                }}
            """)
            return b

        start_btn = mkbtn("🚀 开始游戏", "#43A047", "#2E7D32")
        start_btn.clicked.connect(self.start_game.emit)
        upload_btn = mkbtn("📋 上传单词表", "#1E88E5", "#1565C0")
        upload_btn.setToolTip("点一下可以查看单词表格式示例，再选择你的词表文件")
        upload_btn.clicked.connect(self.upload_words.emit)
        folder_btn = mkbtn("📂 打开词库目录", "#FB8C00", "#E65100")
        folder_btn.clicked.connect(self.open_words_dir.emit)
        music_btn = mkbtn("🎵 上传背景音乐", "#8E24AA", "#6A1B9A")
        music_btn.setToolTip("选择任意 wav/mp3 文件作为背景音乐，自动循环播放")
        music_btn.clicked.connect(self.upload_bgm.emit)

        btn_row.addWidget(start_btn)
        btn_row.addWidget(upload_btn)
        btn_row.addWidget(music_btn)
        btn_row.addWidget(folder_btn)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(20)
        layout.addWidget(intro)
        layout.addSpacing(30)
        layout.addLayout(btn_row)
        layout.addStretch(1)


# ============== 上传模板对话框 ==============
WORD_TABLE_TEMPLATE = """// 单词表模板：每行一个单词，英文 | 中文（管道符分隔）
// 井号或 // 开头的行为注释，会被忽略
// 示例 1：最简单格式（只含英文与中文）
fox | 狐狸
giraffe | 长颈鹿
eagle | 鹰
wolf | 狼
penguin | 企鹅
snake | 蛇
shark | 鲨鱼
whale | 鲸
elephant | 大象
tiger | 老虎

// 示例 2：也可以加可选的第三列【例句】（不填也行）
apple | 苹果 | I eat an apple every day.
book | 书 | This is my book.
computer | 电脑 | I use a computer to work.

// 注意事项：
// 1. 每行格式：英文 | 中文（| 例句）（选填）
// 2. 至少 10 对单词才能完整支持 10 关游戏（每关随机抽 10 对）
// 3. 保存为 UTF-8 编码的 .txt 文件
"""


class UploadTemplateDialog(QDialog):
    """单词表上传模板对话框：展示格式说明 + 示例 + 快捷操作"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("上传单词表")
        # 复用主窗口图标
        if parent and parent.windowIcon():
            self.setWindowIcon(parent.windowIcon())
        self.setMinimumSize(720, 560)
        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #F3E5F5, stop:1 #E1F5FE);
            }
            QLabel { color: #1A237E; font-family: 'Microsoft YaHei'; }
            QTextEdit {
                background: white;
                border: 2px solid #7986CB;
                border-radius: 8px;
                font-family: 'Consolas', 'Microsoft YaHei';
                font-size: 13px;
                selection-background-color: #FFA726;
            }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #42A5F5, stop:1 #1976D2);
                border: 2px solid #0D47A1;
                border-radius: 10px;
                color: white;
                font-family: 'Microsoft YaHei';
                font-weight: bold;
                padding: 8px 14px;
                min-height: 36px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #1976D2, stop:1 #42A5F5);
            }
            QPushButton#primary {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #66BB6A, stop:1 #2E7D32);
                border: 2px solid #1B5E20;
            }
            QPushButton#primary:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #2E7D32, stop:1 #66BB6A);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # 标题
        title = QLabel("📋 单词表格式说明")
        title.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei", 20)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #4A148C;")
        layout.addWidget(title)

        # 格式说明
        hint = QLabel(
            "✅ 格式：每行一个单词，<b>英文 | 中文</b>（例句可选）<br>"
            "✅ 至少 <b>10 对</b> 单词，以保证 10 关都能随机抽到 10 对<br>"
            "✅ 以 <code>#</code> 或 <code>//</code> 开头的行视为注释<br>"
            "✅ 文件请保存为 <b>UTF-8 编码</b>的 <code>.txt</code> 文件"
        )
        hint.setFont(QFont("Microsoft YaHei", 11))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 模板内容
        self.template_edit = QTextEdit()
        self.template_edit.setPlainText(WORD_TABLE_TEMPLATE)
        layout.addWidget(self.template_edit, 1)

        # 按钮区
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_copy = QPushButton("📋 复制到剪贴板")
        btn_copy.clicked.connect(self._on_copy)
        btn_row.addWidget(btn_copy)

        btn_save = QPushButton("💾 保存为 .txt 文件")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        btn_upload = QPushButton("📁 选择我的词表")
        btn_upload.setObjectName("primary")
        btn_upload.clicked.connect(self._on_upload)
        btn_row.addWidget(btn_upload)

        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _on_copy(self):
        try:
            QGuiApplication.clipboard().setText(WORD_TABLE_TEMPLATE)
            QMessageBox.information(self, "已复制",
                                    "模板已复制到剪贴板！\n去记事本粘贴即可开始填单词～")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"复制失败：{e}")

    def _on_save(self):
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存单词表模板",
                str(Path.home() / "Desktop" / "我的单词表.txt"),
                "文本文件 (*.txt);;所有文件 (*.*)"
            )
            if not path:
                return
            Path(path).write_text(WORD_TABLE_TEMPLATE, encoding="utf-8")
            QMessageBox.information(self, "保存成功",
                                    f"模板已保存到：\n{path}\n\n打开后填入你的单词即可。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败：{e}")

    def _on_upload(self):
        # 调用方会在我们关闭后接着弹文件选择框
        self.accept()


class ResultPage(QFrame):
    """通关结算 / 失败结算（可切换外观）"""
    def __init__(self):
        super().__init__()
        self.mode = "win"   # "win" / "fail"
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #FFD54F, stop:0.5 #FF7043, stop:1 #D81B60);
                border-radius: 10px;
            }
            QLabel { color: white; font-family: 'Microsoft YaHei'; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)

        self.title = QLabel("🏆 通关！")
        self.title.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei", 44)
        f.setBold(True)
        self.title.setFont(f)

        self.msg = QLabel("你已成功闯过全部 10 关！")
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setFont(QFont("Microsoft YaHei", 18))

        self.score_label = QLabel("得分：0")
        self.score_label.setAlignment(Qt.AlignCenter)
        self.score_label.setFont(QFont("Microsoft YaHei", 16))

        btn_row = QHBoxLayout()
        self.replay_btn = QPushButton("🔁 再玩一次")
        self.replay_btn.setMinimumSize(180, 56)
        self.replay_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        self.replay_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #43A047, stop:1 #2E7D32);
                border: 2px solid white;
                border-radius: 18px;
                color: white;
            }
        """)
        self.replay_btn.clicked.connect(lambda: None)

        self.restart_btn = QPushButton("🔂 重新挑战本关")
        self.restart_btn.setMinimumSize(180, 56)
        self.restart_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        self.restart_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #FB8C00, stop:1 #E65100);
                border: 2px solid white;
                border-radius: 18px;
                color: white;
            }
        """)
        self.restart_btn.clicked.connect(lambda: None)
        self.restart_btn.hide()  # 默认隐藏，失败时显示

        exit_btn = QPushButton("🚪 退出")
        exit_btn.setMinimumSize(180, 56)
        exit_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        exit_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #757575, stop:1 #424242);
                border: 2px solid white;
                border-radius: 18px;
                color: white;
            }
        """)
        exit_btn.clicked.connect(QApplication.instance().quit)
        self.exit_btn = exit_btn

        btn_row.addStretch(1)
        btn_row.addWidget(self.replay_btn)
        btn_row.addWidget(self.restart_btn)
        btn_row.addWidget(exit_btn)
        btn_row.addStretch(1)

        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addWidget(self.msg)
        layout.addWidget(self.score_label)
        layout.addLayout(btn_row)
        layout.addStretch(1)

    def show_win(self, score):
        self.mode = "win"
        self.title.setText("🏆 通关！")
        self.msg.setText("你已成功闯过全部 10 关！")
        self.score_label.setText(f"最终得分：{score}")
        self.replay_btn.setText("🔁 再玩一次")
        self.replay_btn.show()
        self.restart_btn.hide()

    def show_fail(self, level, score):
        self.mode = "fail"
        self.title.setText("💥 挑战失败！")
        self.msg.setText(f"倒计时结束，未能完成第 {level} 关。再接再厉！")
        self.score_label.setText(f"本关得分：{score}")
        self.replay_btn.setText("🔁 从第 1 关再来")
        self.replay_btn.show()
        self.restart_btn.show()


# ============== 主窗口 ==============
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("单词连连看")
        # 窗口图标：画一个 "连连看🔗" 风格的简单图标
        icon_pix = QPixmap(64, 64)
        icon_pix.fill(Qt.transparent)
        p = QPainter(icon_pix)
        p.setRenderHint(QPainter.Antialiasing)
        # 背景圆
        p.setBrush(QBrush(QColor(0x7B, 0x1F, 0xA2)))
        p.setPen(QPen(QColor(0xFF, 0xEB, 0x3B), 3))
        p.drawEllipse(2, 2, 60, 60)
        # 文字 "词"
        p.setPen(QColor(0xFF, 0xEB, 0x3B))
        f = QFont("Microsoft YaHei", 26, QFont.Bold)
        f.setStyleStrategy(QFont.PreferAntialias)
        p.setFont(f)
        p.drawText(QRectF(0, 0, 64, 64), Qt.AlignCenter, "词")
        p.end()
        self.setWindowIcon(QIcon(icon_pix))

        self.resize(1100, 760)
        self.setMinimumSize(900, 640)

        # 状态
        self.all_words = []
        self.bgm_enabled = True

        # 加载音效 & 音乐
        ensure_audio_assets()

        # 音效
        self.fx = {
            "correct": QSoundEffect(self),
            "wrong":   QSoundEffect(self),
            "click":   QSoundEffect(self),
            "win":     QSoundEffect(self),
        }
        for k, e in self.fx.items():
            e.setVolume(0.8)
            e.setSource(QUrl.fromLocalFile(str(SOUNDS_DIR / f"{k}.wav")))

        # 背景音乐（用 QMediaPlayer，自己实现循环：播完一次再重置 position）
        self.player = QMediaPlayer(self)
        self.player.setVolume(40)
        bgm_path = SOUNDS_DIR / "bgm.wav"
        if bgm_path.exists():
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(bgm_path))))
            self.player.mediaStatusChanged.connect(self._on_media_status)
            self.player.play()

        # 构造 UI
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TitleBar())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.welcome = WelcomePage()
        self.board = GameBoard(self.all_words)
        self.result = ResultPage()

        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.board)
        self.stack.addWidget(self.result)

        # 信号
        self.welcome.start_game.connect(self._on_start)
        self.welcome.upload_words.connect(self._on_upload)
        self.welcome.open_words_dir.connect(self._on_open_folder)
        self.welcome.upload_bgm.connect(self._upload_bgm)
        self.board.match_correct.connect(lambda: self._play("correct"))
        self.board.match_wrong.connect(lambda: self._play("wrong"))
        self.board.level_complete.connect(self._on_level_complete)
        self.board.game_complete.connect(self._on_game_complete)
        self.board.game_failed.connect(self._on_game_failed)
        self.result.replay_btn.clicked.connect(self._on_restart)
        self.result.restart_btn.clicked.connect(self._on_restart_level)

        # 加载词库（UI 构造完成后才有 self.board）
        self._load_default_words()

    def _on_media_status(self, status):
        # EndOfMedia 时自动循环
        try:
            from PyQt5.QtMultimedia import QMediaPlayer as _MP
            if status == _MP.EndOfMedia:
                self.player.setPosition(0)
                self.player.play()
        except Exception:
            pass

    def _upload_bgm(self):
        """用户上传背景音乐文件，支持 wav / mp3"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景音乐文件", str(APP_DIR),
            "音频文件 (*.wav *.mp3 *.ogg *.flac);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            # 先释放播放器对 bgm.wav 的占用
            self.player.stop()
            self.player.setMedia(QMediaContent())  # 清除媒体
            from shutil import copy2
            target = SOUNDS_DIR / "bgm.wav"
            copy2(path, target)
            # 重新加载播放
            QTimer.singleShot(200, lambda: self._reload_bgm(target))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"背景音乐切换失败：{e}")

    def _reload_bgm(self, target):
        try:
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(str(target))))
            self.player.play()
        except Exception:
            pass

    # ---- 业务方法 ----
    def _load_default_words(self):
        default_path = DATA_DIR / "words_default.txt"
        custom_path = DATA_DIR / "words_custom.txt"
        words = []
        if custom_path.exists():
            words = parse_words_file(custom_path)
            if words:
                print(f"[info] 加载自定义词库：{len(words)} 个")
        if not words and default_path.exists():
            words = parse_words_file(default_path)
            print(f"[info] 加载默认词库：{len(words)} 个")
        if not words:
            QMessageBox.warning(self, "提示",
                                "未找到任何词库。\n请把单词表放到 data/words_default.txt 或上传你的词库。")
        self.all_words = words
        if hasattr(self, 'board'):
            self.board.words = self.all_words

    def _play(self, key):
        try:
            self.fx.get(key) and self.fx[key].play()
        except Exception:
            pass

    def _on_start(self):
        self._play("click")
        if not self.all_words:
            QMessageBox.warning(self, "提示", "词库为空，请先导入单词表。")
            return
        self.stack.setCurrentWidget(self.board)
        self.board.load_level(1)

    def _on_upload(self):
        """先弹模板对话框，用户点击「选择我的词表」后才跳出文件选择框"""
        dlg = UploadTemplateDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return  # 用户关闭或点「关闭」，不进入选文件流程
        # 接着打开文件选择
        path, _ = QFileDialog.getOpenFileName(
            self, "选择单词表文件", str(APP_DIR),
            "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            target = DATA_DIR / "words_custom.txt"
            target.write_text(Path(path).read_text(encoding="utf-8"), encoding="utf-8")
            parsed = parse_words_file(target)
            if not parsed:
                QMessageBox.warning(self, "解析失败",
                    "词表解析为空，请检查格式。\n\n"
                    "推荐格式（每行一个）：\n"
                    "    英文 | 中文\n"
                    "或：\n"
                    "    英文 | 中文 | 例句\n\n"
                    "也可以用点 “📋 上传单词表” 里的 “📋 复制到剪贴板” 看一下模板示例。")
                return
            self.all_words = parsed
            self.board.words = self.all_words
            QMessageBox.information(self, "上传成功",
                                    f"已成功导入 {len(parsed)} 个单词！\n点击「开始游戏」即可生效。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败：{e}")

    def _on_open_folder(self):
        """打开 EXE 同目录下的 data 文件夹，PyInstaller 打包后也能正确工作"""
        # 取 EXE 所在目录（相对于临时解压目录的同级 data 目录）
        exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else APP_DIR
        target = exe_dir / "data"
        try:
            os.startfile(str(target))
        except Exception:
            QMessageBox.information(self, "目录", f"请手动打开：{target}")

    def _on_level_complete(self, level):
        self._play("win")
        QMessageBox.information(self, "闯关成功",
                                f"🎉 第 {level} 关通过！\n进入下一关！")
        self.board.load_level(level + 1)

    def _on_game_complete(self):
        self._play("win")
        self.result.show_win(self.board.score)
        self.stack.setCurrentWidget(self.result)

    def _on_game_failed(self, level):
        # 失败音效：复用 wrong.wav
        self._play("wrong")
        # 禁用所有卡交互
        for c in self.board.cards:
            c.setEnabled(False)
        # 弹提示
        QMessageBox.information(self, "挑战失败",
                                f"⏰ 倒计时结束！\n第 {level} 关未能完成。\n可选择重新挑战本关或从第 1 关开始。")
        self.result.show_fail(level, self.board.score)
        self.stack.setCurrentWidget(self.result)

    def _on_restart(self):
        self._play("click")
        self.board.score = 0
        self.stack.setCurrentWidget(self.welcome)

    def _on_restart_level(self):
        """在失败界面选「重新挑战本关」"""
        self._play("click")
        self.stack.setCurrentWidget(self.board)
        self.board.load_level(self.board.current_level)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_M:
            self.bgm_enabled = not self.bgm_enabled
            if self.bgm_enabled:
                self.player.play()
            else:
                self.player.pause()
        elif ev.key() == Qt.Key_F:
            self.fx["click"].play()
            self.fx["correct"].play()
            self.fx["wrong"].play()
            self.fx["win"].play()

    def closeEvent(self, ev):
        try:
            self.player.stop()
        except Exception:
            pass
        super().closeEvent(ev)


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName("单词连连看")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
