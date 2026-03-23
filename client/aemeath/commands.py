"""命令系统模块 - 可扩展的命令注册与分发"""

# 命令注册表
COMMANDS = {}  # name -> {"desc": str, "handler": callable}


def register(name, desc, handler):
    """注册一个命令"""
    COMMANDS[name] = {"desc": desc, "handler": handler}


def dispatch(cmd_text, chat_manager):
    """解析并执行命令，返回要显示的回复文本"""
    parts = cmd_text.split(None, 1)
    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if name in COMMANDS:
        return COMMANDS[name]["handler"](args, chat_manager)
    else:
        return f"未知命令: {name}\n输入 /help 查看可用命令"


# --- 内置命令 ---

def _cmd_help(args, chat_manager):
    lines = ["可用命令:"]
    for name, info in COMMANDS.items():
        lines.append(f"  {name} - {info['desc']}")
    return "\n".join(lines)


def _cmd_cmd(args, chat_manager):
    import subprocess
    subprocess.Popen(["cmd.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    return "已打开命令行"



# --- 宠物控制命令 ---

def _cmd_pin(args, chat_manager):
    pet = chat_manager.pet
    if pet.is_pinned:
        pet.unpin()
        return "꒰ঌ(ˊᗜˋ*)໒꒱"
    else:
        pet.pin()
        return "小爱立正了！"


def _cmd_opacity(args, chat_manager):
    from .constants import TRANSPARENCY_OPTIONS
    pet = chat_manager.pet
    if not args.strip():
        current = int(TRANSPARENCY_OPTIONS[pet.transparency_index] * 100)
        return f"当前透明度: {current}%"
    try:
        value = int(args.strip())
    except ValueError:
        return "用法: /opacity <30-100>"
    if value < 30 or value > 100:
        return "透明度范围: 30-100"
    # 找最近的档位
    target = value / 100.0
    best_index = min(
        range(len(TRANSPARENCY_OPTIONS)),
        key=lambda i: abs(TRANSPARENCY_OPTIONS[i] - target),
    )
    pet.set_transparency(best_index)
    actual = int(TRANSPARENCY_OPTIONS[best_index] * 100)
    return f"透明度已设为 {actual}%"


def _cmd_size(args, chat_manager):
    from .constants import SCALE_OPTIONS
    pet = chat_manager.pet
    if not args.strip():
        return f"当前缩放: {pet.scale}x"
    try:
        value = float(args.strip())
    except ValueError:
        return "用法: /size <0.3-1.9>"
    # 支持百分比写法 (如 90 -> 0.9)
    if value > 10:
        value = value / 100.0
    if value < 0.3 or value > 1.9:
        return "缩放范围: 0.3-1.9"
    # 找最近的档位
    best_index = min(
        range(len(SCALE_OPTIONS)),
        key=lambda i: abs(SCALE_OPTIONS[i] - value),
    )
    pet.set_scale(best_index)
    return f"缩放已设为 {SCALE_OPTIONS[best_index]}x"


def _cmd_pos(args, chat_manager):
    pet = chat_manager.pet
    if not args.strip():
        return f"当前位置: ({int(pet.x)}, {int(pet.y)})"

    arg = args.strip().lower()
    sw = pet.screen_w
    sh = pet.screen_h
    pw, ph = pet.w, pet.h

    positions = {
        "center": (sw // 2 - pw // 2, sh // 2 - ph // 2),
        "top-left": (0, 0),
        "top-right": (sw - pw, 0),
        "bottom-left": (0, sh - ph),
        "bottom-right": (sw - pw, sh - ph),
    }

    if arg in positions:
        pet.x, pet.y = positions[arg]
    else:
        parts = args.strip().split()
        if len(parts) == 2:
            try:
                pet.x = int(parts[0])
                pet.y = int(parts[1])
            except ValueError:
                return "用法: /pos <center|top-left|...> 或 /pos <x> <y>"
        else:
            return "用法: /pos <center|top-left|...> 或 /pos <x> <y>"

    pet.root.geometry(f"+{int(pet.x)}+{int(pet.y)}")
    return f"已移动到 ({int(pet.x)}, {int(pet.y)})"


def _cmd_sleep(args, chat_manager):
    pet = chat_manager.pet
    if pet.is_paused:
        return "Zzz..."
    pet.toggle_pause()
    return "Zzz..."


def _cmd_wake(args, chat_manager):
    pet = chat_manager.pet
    if not pet.is_paused:
        return "我醒着呐！"
    pet.toggle_pause()
    return "Ciallo～ (∠・ω< )⌒★"


def _cmd_clear(args, chat_manager):
    llm = chat_manager._get_llm()
    llm.clear_history()
    return "对话已清空"


def _cmd_model(args, chat_manager):
    if not args.strip():
        import os
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return f"当前模型: {model}"
    return "通过环境变量 OPENAI_MODEL 设置模型"


# --- 注册内置命令 ---
register("/help", "显示可用命令", _cmd_help)
register("/cmd", "打开命令行", _cmd_cmd)
register("/pin", "固定位置", _cmd_pin)
register("/opacity", "设置透明度 (30-100)", _cmd_opacity)
register("/size", "设置缩放 (0.3-1.9)", _cmd_size)
register("/pos", "移动到指定位置", _cmd_pos)
register("/sleep", "休眠", _cmd_sleep)
register("/wake", "唤醒", _cmd_wake)
register("/clear", "清空对话历史", _cmd_clear)
register("/model", "查看当前模型", _cmd_model)
