"""聊天窗口模块 - 输入窗口 + 对话气泡"""

import ctypes
import math
import os
import threading
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from PIL import Image, ImageTk

from .utils import resource_path

# ============ 全局快捷键常量 ============
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_I = 0x49
WM_HOTKEY = 0x0312
HOTKEY_TOGGLE_CHAT = 1

# ============ 字体 ============
FR_PRIVATE = 0x10
_pixel_font = None


def _load_pixel_font():
    """加载 zpix 像素字体，失败回退到 Consolas"""
    font_path = resource_path("fonts/zpix.ttf")
    try:
        result = ctypes.windll.gdi32.AddFontResourceExW(font_path, FR_PRIVATE, 0)
        if result > 0:
            return "zpix"
    except Exception:
        pass
    return "Consolas"


def get_pixel_font():
    global _pixel_font
    if _pixel_font is None:
        _pixel_font = _load_pixel_font()
    return _pixel_font


# ============ 配色 ============
INPUT_BORDER_COLOR = "#89CFF0"   # 输入窗口边框 - 天蓝 
INPUT_BG_COLOR = "#FAF5FA"       # 输入窗口背景 - 浅粉白

SPEECH_BORDER_COLOR = "#C8B4DC"  # 对话气泡边框 - 薰衣草  
SPEECH_BG_COLOR = "#FFF5F8"      # 对话气泡背景 - 浅粉

TEXT_COLOR = "#5D4E6D"           # 文字颜色 - 深紫灰

TRANSPARENT_KEY = "#010101"      # 窗口透明色键

# ============ 尺寸 ============
BORDER_W = 6
PAD = 12
CORNER_R = 20
INPUT_MIN_W = 120
INPUT_MAX_W = 300
INPUT_LINE_H_BUFFER = 4  # 行高额外 buffer
INPUT_MAX_LINES = 6
SPEECH_MIN_W = 80
SPEECH_MAX_W = 300
ARROW_H = 8                     # 气泡三角高度
ARROW_W = 16                    # 气泡三角宽度
ARROW_OFFSET_X = 24             # 三角距左边距离
FOLLOW_INTERVAL = 30  # ms
AUTO_HIDE_DELAY = 5000  # 输入框无操作自动关闭 (ms)
PLACEHOLDER_TEXT = "/help "
PLACEHOLDER_COLOR = "#B0A0B8"  # 占位文字颜色 - 偏灰紫

# ============ 装饰图片 ============
DECO_DIR = "icons"
SPEECH_DECO_FILE = "deco_speech.png"
INPUT_DECO_FILE = "deco_input.png"
DECO_MARGIN = 16  # 装饰图溢出边框的空间（32x32 图片的一半）

_deco_cache = {}  # 缓存已加载的装饰图片


def _load_deco(filename):
    """加载装饰 PNG 图片，失败返回 None"""
    if filename in _deco_cache:
        return _deco_cache[filename]
    path = resource_path(os.path.join(DECO_DIR, filename))
    try:
        img = Image.open(path).convert("RGBA")
        tk_img = ImageTk.PhotoImage(img)
        _deco_cache[filename] = tk_img
        return tk_img
    except Exception:
        _deco_cache[filename] = None
        return None


# ============ Canvas 圆角矩形 ============
def _rounded_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    """在 Canvas 上绘制圆角矩形"""
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class ChatInputWindow:
    """输入窗口 - 位于桌宠右侧，宽度和高度随文本动态变化

    坐标系统：
    _win_w / _win_h = 圆角框的逻辑尺寸
    实际画布/窗口 = (_win_w + DECO_MARGIN) x (_win_h + DECO_MARGIN)
    框绘制在画布的 (0, DECO_MARGIN) 处，上方和右侧留出装饰图溢出空间
    """

    def __init__(self, pet, on_submit=None):
        self.pet = pet
        self.root = pet.root
        self.visible = False
        self.on_submit = on_submit
        self._placeholder_showing = False
        self._auto_hide_timer = None
        font_name = get_pixel_font()
        self._font = (font_name, 12)
        self._font_obj = tkfont.Font(family=font_name, size=12)
        # 动态计算行高：字体实际渲染高度 + buffer
        self._line_h = self._font_obj.metrics("linespace") + 4

        dm = DECO_MARGIN

        # 当前框尺寸（逻辑尺寸，不含装饰溢出）
        self._win_w = INPUT_MIN_W
        self._win_h = 2 * (BORDER_W + PAD) + self._line_h

        # 创建窗口（透明背景实现圆角）
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=TRANSPARENT_KEY)
        self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.win.withdraw()

        # Canvas（含装饰溢出空间）
        self.canvas = tk.Canvas(
            self.win,
            width=self._win_w + dm,
            height=self._win_h + dm,
            highlightthickness=0,
            bg=TRANSPARENT_KEY,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._draw_bg()

        # 文本输入框
        self.text = tk.Text(
            self.win,
            font=self._font,
            bg=INPUT_BG_COLOR,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            spacing1=0,
            spacing3=0,
            wrap=tk.WORD,
            undo=True,
        )
        self._place_text()

        # 按键绑定
        self.text.bind("<Return>", self._handle_submit)
        self.text.bind("<KeyRelease>", self._on_text_change)
        self.text.bind("<KeyPress>", self._on_key_press)
        self.text.bind("<Escape>", lambda e: self.hide())

        # 跟随循环
        self._follow()

    # ---- placeholder ----

    def _show_placeholder(self):
        """显示占位文字"""
        self._placeholder_showing = True
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", PLACEHOLDER_TEXT)
        self.text.config(fg=PLACEHOLDER_COLOR)

    def _clear_placeholder(self):
        """清除占位文字，恢复正常颜色"""
        if self._placeholder_showing:
            self._placeholder_showing = False
            self.text.delete("1.0", tk.END)
            self.text.config(fg=TEXT_COLOR)

    def _on_key_press(self, event):
        """按键按下时清除 placeholder，并重置自动隐藏计时"""
        # 忽略修饰键和功能键
        if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                            "Alt_L", "Alt_R", "Escape", "Return"):
            return
        self._clear_placeholder()
        self._reset_auto_hide()

    # ---- 自动隐藏 ----

    def _reset_auto_hide(self):
        """重置自动隐藏计时器"""
        if self._auto_hide_timer:
            self.root.after_cancel(self._auto_hide_timer)
        self._auto_hide_timer = self.root.after(AUTO_HIDE_DELAY, self._auto_hide)

    def _auto_hide(self):
        """自动隐藏（仅当输入框为空或仅有 placeholder 时）"""
        content = self.text.get("1.0", "end-1c").strip()
        if not content or self._placeholder_showing:
            self.hide()
        else:
            # 有内容时不关闭，但继续倒计时
            self._auto_hide_timer = self.root.after(AUTO_HIDE_DELAY, self._auto_hide)

    # ---- 透明度 ----

    def set_alpha(self, alpha):
        """设置窗口透明度"""
        self.win.attributes("-alpha", alpha)

    # ---- 绘制 ----

    def _draw_bg(self):
        """重绘圆角背景 + 装饰图"""
        dm = DECO_MARGIN
        cw = self._win_w + dm
        ch = self._win_h + dm

        self.canvas.delete("all")
        self.canvas.config(width=cw, height=ch)

        # 框区域：上方留 dm 空间给装饰图溢出
        bx2 = self._win_w
        by1 = dm

        # 外层：边框
        _rounded_rect(
            self.canvas, 0, by1, bx2, ch, CORNER_R,
            fill=INPUT_BORDER_COLOR, outline="",
        )
        # 内层：背景
        b = BORDER_W
        inner_r = max(2, CORNER_R - b)
        _rounded_rect(
            self.canvas, b, by1 + b, bx2 - b, ch - b, inner_r,
            fill=INPUT_BG_COLOR, outline="",
        )
        # 装饰图片（骑在右上角边框上）
        deco = _load_deco(INPUT_DECO_FILE)
        if deco:
            self.canvas.create_image(bx2, by1, image=deco, anchor=tk.CENTER)

    def _place_text(self):
        """放置/更新 Text 控件位置和大小"""
        tx = BORDER_W + PAD
        ty = DECO_MARGIN + BORDER_W + PAD
        tw = self._win_w - 2 * (BORDER_W + PAD)
        th = self._win_h - 2 * (BORDER_W + PAD)
        self.text.place(x=tx, y=ty, width=max(20, tw), height=max(self._line_h, th))

    def _on_text_change(self, event=None):
        """文本变化时重新计算窗口尺寸"""
        if self._placeholder_showing:
            return

        content = self.text.get("1.0", "end-1c")

        text_pixel_w = self._font_obj.measure(content) if content else 0
        inner_pad = 2 * (BORDER_W + PAD)

        # 1) 宽度
        needed_w = text_pixel_w + inner_pad + 10
        new_w = max(INPUT_MIN_W, min(INPUT_MAX_W, needed_w))

        # 2) 行数
        max_text_w = INPUT_MAX_W - inner_pad
        if max_text_w > 0 and text_pixel_w > max_text_w:
            lines = math.ceil(text_pixel_w / max_text_w)
        else:
            lines = 1
        newline_count = content.count("\n") + 1
        lines = max(lines, newline_count)
        lines = min(lines, INPUT_MAX_LINES)

        # 3) 高度
        new_h = 2 * (BORDER_W + PAD) + self._line_h * lines

        # 4) 有变化才更新
        if new_w != self._win_w or new_h != self._win_h:
            self._win_w = new_w
            self._win_h = new_h
            self._draw_bg()
            self._place_text()
            dm = DECO_MARGIN
            self.win.geometry(f"{new_w + dm}x{new_h + dm}")

    def _follow(self):
        if self.visible:
            dm = DECO_MARGIN
            actual_w = self._win_w + dm
            px, py = int(self.pet.x), int(self.pet.y)
            x = px + self.pet.w + 10
            y = py + (self.pet.h - self._win_h) // 2 - dm
            screen_w = self.root.winfo_screenwidth()
            if x + actual_w > screen_w:
                x = px - actual_w - 10
            self.win.geometry(f"+{x}+{y}")
        self.root.after(FOLLOW_INTERVAL, self._follow)

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self):
        self.visible = True
        dm = DECO_MARGIN
        # 重置为初始尺寸
        self._win_w = INPUT_MIN_W
        self._win_h = 2 * (BORDER_W + PAD) + self._line_h
        self._draw_bg()
        self._place_text()
        # 显示 placeholder
        self._show_placeholder()
        # 定位并显示
        actual_w = self._win_w + dm
        actual_h = self._win_h + dm
        px, py = int(self.pet.x), int(self.pet.y)
        x = px + self.pet.w + 10
        y = py + (self.pet.h - self._win_h) // 2 - dm
        screen_w = self.root.winfo_screenwidth()
        if x + actual_w > screen_w:
            x = px - actual_w - 10
        self.win.geometry(f"{actual_w}x{actual_h}+{x}+{y}")
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.text.focus_set()
        # 启动自动隐藏计时
        self._reset_auto_hide()

    def hide(self):
        self.visible = False
        self.win.withdraw()
        self.text.delete("1.0", tk.END)
        if self._auto_hide_timer:
            self.root.after_cancel(self._auto_hide_timer)
            self._auto_hide_timer = None

    def _handle_submit(self, event=None):
        if self._placeholder_showing:
            return "break"
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            return "break"
        self.text.delete("1.0", tk.END)
        # 重置尺寸
        self._win_w = INPUT_MIN_W
        self._win_h = 2 * (BORDER_W + PAD) + self._line_h
        self._draw_bg()
        self._place_text()
        dm = DECO_MARGIN
        self.win.geometry(f"{self._win_w + dm}x{self._win_h + dm}")
        # 发送后重置计时
        self._reset_auto_hide()
        if self.on_submit:
            self.on_submit(text)
        return "break"


class ChatSpeechWindow:
    """对话气泡窗口 - 位于桌宠上方，带底部三角尾巴

    坐标系统：
    box_w / box_h = 气泡矩形的逻辑尺寸
    实际画布/窗口 = (box_w + DECO_MARGIN) x (box_h + ARROW_H + DECO_MARGIN)
    框绘制在画布的 (0, DECO_MARGIN) 处
    """

    def __init__(self, pet):
        self.pet = pet
        self.root = pet.root
        self.visible = False
        self._font = (get_pixel_font(), 11)
        self._hide_timer = None
        self._box_w = 0  # 当前气泡矩形宽度（用于居中定位）

        # 创建窗口（透明背景实现圆角）
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.config(bg=TRANSPARENT_KEY)
        self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.win.withdraw()

        # Canvas
        self.canvas = tk.Canvas(
            self.win,
            highlightthickness=0,
            bg=TRANSPARENT_KEY,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 文字标签
        max_text_w = SPEECH_MAX_W - 2 * (BORDER_W + PAD)
        self.text_label = tk.Label(
            self.win,
            font=self._font,
            bg=SPEECH_BG_COLOR,
            fg=TEXT_COLOR,
            wraplength=max_text_w,
            justify=tk.LEFT,
            anchor=tk.NW,
            highlightthickness=0,
            borderwidth=0,
        )

        # 跟随循环
        self._follow()

    def _follow(self):
        if self.visible:
            self._update_position()
        self.root.after(FOLLOW_INTERVAL, self._follow)

    def _update_position(self):
        px, py = int(self.pet.x), int(self.pet.y)
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        # 以气泡矩形宽度居中（右侧 DECO_MARGIN 是装饰溢出空间）
        box_w = self._box_w or w
        x = px + (self.pet.w - box_w) // 2
        y = py - h - 5
        screen_w = self.root.winfo_screenwidth()
        x = max(0, min(screen_w - w, x))
        y = max(0, y)
        self.win.geometry(f"+{x}+{y}")

    def show_message(self, text, duration=5000):
        """显示消息，duration 毫秒后自动隐藏"""
        b = BORDER_W
        p = PAD
        dm = DECO_MARGIN

        self.text_label.config(text=text)
        self.text_label.update_idletasks()
        text_w = self.text_label.winfo_reqwidth()
        text_h = self.text_label.winfo_reqheight()

        # 气泡矩形部分的尺寸
        box_w = max(SPEECH_MIN_W, text_w + 2 * (b + p))
        box_w = min(SPEECH_MAX_W, box_w)
        box_h = text_h + 2 * (b + p)
        self._box_w = box_w

        # 总窗口尺寸（含装饰溢出 + 三角箭头）
        win_w = box_w + dm
        win_h = box_h + ARROW_H + dm

        self.canvas.config(width=win_w, height=win_h)
        self.win.geometry(f"{win_w}x{win_h}")

        # 重绘
        self.canvas.delete("all")

        # 框顶部 y 偏移（上方留装饰溢出空间）
        by1 = dm

        # 1) 外层圆角矩形（边框色）
        _rounded_rect(
            self.canvas, 0, by1, box_w, by1 + box_h, CORNER_R,
            fill=SPEECH_BORDER_COLOR, outline="",
        )

        # 2) 外层三角（边框色），与矩形底部重叠 1px 消除缝隙
        ax = ARROW_OFFSET_X
        self.canvas.create_polygon(
            ax, by1 + box_h - 1,
            ax + ARROW_W, by1 + box_h - 1,
            ax + ARROW_W // 2, by1 + box_h + ARROW_H,
            fill=SPEECH_BORDER_COLOR, outline="",
        )

        # 3) 内层圆角矩形（背景色）
        inner_r = max(2, CORNER_R - b)
        _rounded_rect(
            self.canvas, b, by1 + b, box_w - b, by1 + box_h - b, inner_r,
            fill=SPEECH_BG_COLOR, outline="",
        )

        # 4) 内层三角（背景色），缩进形成边框效果
        inset = b + 1
        self.canvas.create_polygon(
            ax + inset, by1 + box_h - b,
            ax + ARROW_W - inset, by1 + box_h - b,
            ax + ARROW_W // 2, by1 + box_h + ARROW_H - inset - 1,
            fill=SPEECH_BG_COLOR, outline="",
        )

        # 5) 装饰图片（骑在右上角边框上）
        deco = _load_deco(SPEECH_DECO_FILE)
        if deco:
            self.canvas.create_image(box_w, by1, image=deco, anchor=tk.CENTER)

        # 放置文字标签
        self.text_label.place(
            x=b + p, y=by1 + b + p,
            width=box_w - 2 * (b + p),
            height=text_h,
        )

        self.visible = True
        self.win.deiconify()
        self._update_position()

        if self._hide_timer:
            self.root.after_cancel(self._hide_timer)
            self._hide_timer = None
        if duration > 0:
            self._hide_timer = self.root.after(duration, self.hide)

    def hide(self):
        self.visible = False
        self.win.withdraw()

    def update_text(self, text):
        """流式更新气泡文本（尺寸变化时才重绘）"""
        # 取消自动隐藏计时
        if self._hide_timer:
            self.root.after_cancel(self._hide_timer)
            self._hide_timer = None

        if not self.visible:
            # 首次显示，走全量绘制
            self.show_message(text, duration=0)
            return

        self.text_label.config(text=text)
        self.text_label.update_idletasks()
        text_w = self.text_label.winfo_reqwidth()
        text_h = self.text_label.winfo_reqheight()

        b = BORDER_W
        p = PAD
        new_box_w = max(SPEECH_MIN_W, min(SPEECH_MAX_W, text_w + 2 * (b + p)))
        new_box_h = text_h + 2 * (b + p)

        # 尺寸变化时才重绘
        if new_box_w != self._box_w or new_box_h != getattr(self, '_box_h', 0):
            self._box_h = new_box_h
            self.show_message(text, duration=0)
        else:
            # 仅更新标签内容和位置
            self.text_label.place(
                x=b + p, y=DECO_MARGIN + b + p,
                width=new_box_w - 2 * (b + p),
                height=text_h,
            )

    def finish_stream(self, duration=8000):
        """流式输出结束后，启动自动隐藏计时"""
        if self._hide_timer:
            self.root.after_cancel(self._hide_timer)
        self._hide_timer = self.root.after(duration, self.hide)

    def set_alpha(self, alpha):
        """设置窗口透明度"""
        self.win.attributes("-alpha", alpha)


class ChatManager:
    """聊天系统管理器"""

    def __init__(self, pet):
        self.pet = pet
        self.root = pet.root
        self.speech = ChatSpeechWindow(pet)
        self.input_win = ChatInputWindow(pet, on_submit=self._on_message)
        self.llm = None  # 延迟初始化

        # 注册全局快捷键
        _register_hotkey(self.root, self.input_win.toggle)

        # 同步当前透明度
        from .constants import TRANSPARENCY_OPTIONS
        alpha = TRANSPARENCY_OPTIONS[pet.transparency_index]
        self.set_alpha(alpha)

    def set_alpha(self, alpha):
        """同步设置两个窗口的透明度"""
        self.input_win.set_alpha(alpha)
        self.speech.set_alpha(alpha)

    def _get_llm(self):
        if self.llm is None:
            try:
                from .api_client import ServerClient
                client = ServerClient()
                client.health()
                self.llm = client
            except Exception:
                from .llm import LLMClient
                self.llm = LLMClient()
        return self.llm

    def _on_message(self, text):
        """处理用户输入"""
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._chat_with_llm(text)

    def _chat_with_llm(self, text):
        llm = self._get_llm()
        # 取消上一个未完成的请求
        llm.cancel()
        # 显示等待占位
        self.speech.update_text("...")

        def on_chunk(accumulated):
            self.root.after(0, lambda: self.speech.update_text(accumulated))

        def on_done(full):
            self.root.after(0, lambda: self.speech.finish_stream())

        def on_error(msg):
            self.root.after(0, lambda: self.speech.show_message(f"出错了: {msg}"))

        llm.chat_stream(text, on_chunk, on_done, on_error)

    def _handle_command(self, text):
        from .commands import dispatch
        result = dispatch(text, self)
        self.speech.show_message(result)


def _register_hotkey(root, callback):
    """注册全局快捷键 Ctrl+Shift+I"""

    def _thread():
        result = ctypes.windll.user32.RegisterHotKey(
            None, HOTKEY_TOGGLE_CHAT, MOD_CONTROL | MOD_SHIFT, VK_I
        )
        if not result:
            return

        msg = wintypes.MSG()
        while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_TOGGLE_CHAT:
                root.after(0, callback)

    t = threading.Thread(target=_thread, daemon=True)
    t.start()
