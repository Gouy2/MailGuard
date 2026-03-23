import tkinter as tk

from PIL import Image, ImageTk

from .utils import resource_path


def show_about_dialog(parent, current_version):
    """显示关于信息"""
    about_window = tk.Toplevel(parent)
    about_window.title("Aemeath")
    about_window.geometry("700x400")
    about_window.resizable(False, False)
    about_window.attributes("-topmost", True)

    # 设置窗口图标
    try:
        icon_image = Image.open(resource_path("gifs/aemeath.gif"))
        icon_image = icon_image.resize((64, 64), Image.Resampling.LANCZOS)
        icon_pil = icon_image.convert("RGBA")
        app_icon = ImageTk.PhotoImage(icon_pil)
        about_window.iconphoto(True, app_icon)
    except Exception:
        pass

    # 居中显示
    about_window.update_idletasks()
    screen_w = about_window.winfo_screenwidth()
    screen_h = about_window.winfo_screenheight()
    x = (screen_w - 700) // 2
    y = (screen_h - 400) // 2
    about_window.geometry(f"+{x}+{y}")

    # 主内容 Frame
    content_frame = tk.Frame(about_window)
    content_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=20)

    # 显示 aemeath.gif
    try:
        gif_image = Image.open(resource_path("gifs/aemeath.gif"))
        gif_image = gif_image.resize((100, 100), Image.Resampling.LANCZOS)
        gif_photo = ImageTk.PhotoImage(gif_image)
        gif_label = tk.Label(content_frame, image=gif_photo, border=0)
        gif_label.image = gif_photo  # type: ignore[attr-defined]
        gif_label.pack(pady=(0, 15))
    except Exception as e:
        print(f"加载关于窗口GIF失败: {e}")

    # 标题
    tk.Label(
        content_frame,
        text="Aemeath",
        font=("Microsoft YaHei UI", 20, "bold"),
    ).pack(pady=(0, 20))

    # 版本号
    tk.Label(
        content_frame,
        text=f"版本: {current_version}",
        font=("Microsoft YaHei UI", 12),
    ).pack(pady=(0, 25))

    # 关闭按钮
    tk.Button(
        content_frame,
        text="确定",
        command=about_window.destroy,
        width=12,
        font=("Microsoft YaHei UI", 11),
    ).pack(pady=(10, 0))
