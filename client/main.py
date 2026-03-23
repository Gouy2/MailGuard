import ctypes
import sys
import tkinter as tk

from dotenv import load_dotenv

load_dotenv()

# 启用 Windows DPI 感知（解决高DPI屏幕模糊问题）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    from aemeath.pet import DesktopGif
    from aemeath.utils import get_version

    VERSION = get_version()

    root = tk.Tk()
    # 立即隐藏窗口，避免闪烁
    root.withdraw()

    # 尝试导入pystray
    try:
        from aemeath.tray import create_tray

        app = DesktopGif(root)

        # 初始化聊天系统（Ctrl+Shift+I 唤起）
        from aemeath.chat import ChatManager

        chat = ChatManager(app)
        app.chat = chat

        icon = create_tray(app, VERSION)
        app.app = icon

        # 延迟启动托盘，让窗口完全初始化后再显示
        root.update_idletasks()
        root.deiconify()  # 显示窗口（避免边框闪烁）
        root.after(500, lambda: icon.run_detached())

        root.mainloop()

    except ImportError:
        # 没有pystray时正常运行窗口
        print("未安装pystray，将只显示窗口。可运行: pip install pystray")
        root.deiconify()  # 显示窗口
        app = DesktopGif(root)

        from aemeath.chat import ChatManager

        chat = ChatManager(app)
        app.chat = chat

        root.mainloop()


if __name__ == "__main__":
    main()
