"""命令系统模块 - 可扩展的命令注册与分发"""

import json

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


def _server_client(chat_manager):
    llm = chat_manager._get_llm()
    if all(hasattr(llm, attr) for attr in ("health", "tools", "pending_tools")):
        return llm

    from .api_client import ServerClient
    return ServerClient()


def _format_error(exc):
    return f"服务端请求失败: {exc}"


def _cmd_server(args, chat_manager):
    try:
        client = _server_client(chat_manager)
        health = client.health()
        tools = ", ".join(health.get("tools", []))
        return f"Server: {health.get('status')}\nTools: {tools or '无'}"
    except Exception as exc:
        return _format_error(exc)


def _cmd_tools(args, chat_manager):
    try:
        client = _server_client(chat_manager)
        tools = client.tools().get("tools", [])
        if not tools:
            return "暂无工具"
        lines = ["工具列表:"]
        for tool in tools:
            marker = "!" if tool.get("requires_confirmation") else "-"
            lines.append(f"{marker} {tool['name']} [{tool.get('permission')}]")
        return "\n".join(lines)
    except Exception as exc:
        return _format_error(exc)


def _cmd_pending(args, chat_manager):
    try:
        client = _server_client(chat_manager)
        pending = client.pending_tools().get("pending", [])
        if not pending:
            return "没有待审批工具调用"
        lines = ["待审批工具:"]
        for item in pending:
            lines.append(f"{item['id'][:8]} {item['tool_name']} {item.get('reason', '')}")
        lines.append("使用 /approve <id> 或 /reject <id>")
        return "\n".join(lines)
    except Exception as exc:
        return _format_error(exc)


def _resolve_pending_id(client, short_id):
    pending = client.pending_tools().get("pending", [])
    matches = [item for item in pending if item["id"].startswith(short_id)]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("ID 前缀不唯一，请输入更长的 pending id")
    return matches[0]["id"]


def _cmd_approve(args, chat_manager):
    pending_id = args.strip()
    if not pending_id:
        return "用法: /approve <pending_id>"
    try:
        client = _server_client(chat_manager)
        full_id = _resolve_pending_id(client, pending_id)
        if not full_id:
            return "没有找到这个 pending id"
        result = client.approve_tool(full_id)
        if result.get("ok"):
            return f"已批准并执行: {result.get('tool')}"
        return f"批准失败: {result.get('error')}"
    except Exception as exc:
        return _format_error(exc)


def _cmd_reject(args, chat_manager):
    pending_id = args.strip()
    if not pending_id:
        return "用法: /reject <pending_id>"
    try:
        client = _server_client(chat_manager)
        full_id = _resolve_pending_id(client, pending_id)
        if not full_id:
            return "没有找到这个 pending id"
        result = client.reject_tool(full_id)
        if result.get("ok"):
            return f"已拒绝: {result.get('tool')}"
        return f"拒绝失败: {result.get('error')}"
    except Exception as exc:
        return _format_error(exc)


def _cmd_trace(args, chat_manager):
    trace_id = args.strip()
    if not trace_id:
        return "用法: /trace <trace_id>"
    try:
        client = _server_client(chat_manager)
        response = client.trace(trace_id)
        events = response.get("events", [])
        if not events:
            return "没有找到 trace"
        lines = [f"Trace {trace_id[:8]}:"]
        for event in events[-8:]:
            payload = event.get("payload", {})
            summary = payload.get("status") or payload.get("tool") or payload.get("decision") or ""
            lines.append(f"- {event.get('event')} {summary}")
        return "\n".join(lines)
    except Exception as exc:
        return _format_error(exc)


def _cmd_tool(args, chat_manager):
    parts = args.strip().split(None, 1)
    if not parts:
        return "用法: /tool <name> [json_arguments]"

    name = parts[0]
    raw_arguments = parts[1] if len(parts) > 1 else "{}"
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return f"JSON 参数错误: {exc}"
    if not isinstance(arguments, dict):
        return "JSON 参数必须是对象"

    try:
        client = _server_client(chat_manager)
        result = client.execute_tool(name, arguments)
        trace_id = result.get("trace_id", "")
        trace_hint = f"\nTrace: {trace_id}" if trace_id else ""
        if result.get("requires_approval"):
            pending_id = result.get("pending_tool_call_id", "")
            return f"需要审批: {name}\nID: {pending_id[:8]}{trace_hint}\n使用 /approve {pending_id[:8]}"
        if result.get("ok"):
            preview = str(result.get("result", ""))[:300]
            return f"工具完成: {name}{trace_hint}\n{preview}"
        error_type = result.get("error_type", "error")
        return f"工具失败: {error_type}{trace_hint}\n{result.get('error')}"
    except Exception as exc:
        return _format_error(exc)


def _cmd_email(args, chat_manager):
    parts = args.strip().split()
    action = parts[0].lower() if parts else "report"

    try:
        client = _server_client(chat_manager)
        if action == "report":
            result = client.execute_tool("email_report_important", _email_options(parts[1:]))
            return _format_email_report(result)
        if action == "ignored":
            result = client.execute_tool("email_list_ignored", _email_options(parts[1:]))
            return _format_email_ignored(result)
        if action == "detail":
            if len(parts) < 2:
                return "用法: /email detail <email_id>"
            result = client.execute_tool("email_get_detail", {"email_id": parts[1], "max_body_chars": 1200})
            return _format_email_detail(result)
        if action == "classify":
            if len(parts) < 2:
                return "用法: /email classify <email_id>"
            result = client.execute_tool("email_classify", {"email_id": parts[1]})
            return _format_email_classification(result)
        return "用法: /email report|ignored|detail <id>|classify <id>"
    except Exception as exc:
        return _format_error(exc)


def _email_options(tokens):
    options = {}
    if "--unread" in tokens:
        options["unread_only"] = True
    for token in tokens:
        if token.startswith("--limit="):
            try:
                options["limit"] = int(token.split("=", 1)[1])
            except ValueError:
                pass
    return options


def _format_email_report(response):
    trace_hint = _trace_hint(response)
    if not response.get("ok"):
        return f"邮件报告失败{trace_hint}\n{response.get('error')}"

    result = response.get("result", {})
    important = result.get("important", [])
    lines = [
        f"重要邮件: {result.get('important_count', len(important))}",
        f"已忽略: {result.get('ignored_count', 0)}",
    ]

    ignored_summary = result.get("ignored_summary", {})
    if ignored_summary:
        summary = ", ".join(f"{key}:{value}" for key, value in ignored_summary.items())
        lines.append(f"忽略分类: {summary}")

    for index, item in enumerate(important[:6], start=1):
        reasons = "; ".join(item.get("reasons", []))
        lines.extend(
            [
                "",
                f"{index}. [{item.get('importance')}/{item.get('category')}] {item.get('from_name')}",
                f"主题: {item.get('subject')}",
                f"原因: {reasons or '无'}",
                f"建议: {item.get('suggested_action')}",
                f"ID: {item.get('email_id')}",
            ]
        )

    if not important:
        lines.append("暂无需要汇报的重要邮件")
    if trace_hint:
        lines.append(trace_hint.strip())
    return "\n".join(lines)


def _format_email_ignored(response):
    trace_hint = _trace_hint(response)
    if not response.get("ok"):
        return f"忽略列表失败{trace_hint}\n{response.get('error')}"

    result = response.get("result", {})
    ignored = result.get("ignored", [])
    lines = [f"忽略邮件: {result.get('ignored_count', len(ignored))}"]
    for index, item in enumerate(ignored[:8], start=1):
        reasons = "; ".join(item.get("reasons", []))
        lines.extend(
            [
                "",
                f"{index}. [{item.get('category')}] {item.get('from_name')}",
                f"主题: {item.get('subject')}",
                f"原因: {reasons or '无'}",
                f"ID: {item.get('email_id')}",
            ]
        )
    if not ignored:
        lines.append("暂无低优先级邮件")
    if trace_hint:
        lines.append(trace_hint.strip())
    return "\n".join(lines)


def _format_email_detail(response):
    trace_hint = _trace_hint(response)
    if not response.get("ok"):
        return f"邮件详情失败{trace_hint}\n{response.get('error')}"

    result = response.get("result", {})
    email = result.get("email", {})
    classification = result.get("classification", {})
    body = str(email.get("body", "")).strip()
    if len(body) > 500:
        body = body[:500] + "..."
    reasons = "; ".join(classification.get("reasons", []))
    lines = [
        f"邮件: {email.get('id')}",
        f"来自: {email.get('from_name')} <{email.get('from_email')}>",
        f"主题: {email.get('subject')}",
        f"分类: {classification.get('importance')}/{classification.get('category')}",
        f"原因: {reasons or '无'}",
        f"建议: {classification.get('suggested_action')}",
        "",
        body or email.get("snippet", ""),
    ]
    if trace_hint:
        lines.append(trace_hint.strip())
    return "\n".join(lines)


def _format_email_classification(response):
    trace_hint = _trace_hint(response)
    if not response.get("ok"):
        return f"邮件分类失败{trace_hint}\n{response.get('error')}"

    result = response.get("result", {})
    email = result.get("email", {})
    classification = result.get("classification", {})
    reasons = "; ".join(classification.get("reasons", []))
    lines = [
        f"邮件: {email.get('id')}",
        f"主题: {email.get('subject')}",
        f"分类: {classification.get('importance')}/{classification.get('category')}",
        f"原因: {reasons or '无'}",
        f"建议: {classification.get('suggested_action')}",
    ]
    if trace_hint:
        lines.append(trace_hint.strip())
    return "\n".join(lines)


def _trace_hint(response):
    trace_id = response.get("trace_id", "")
    return f"\nTrace: {trace_id[:8]}" if trace_id else ""


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
register("/server", "查看服务端状态", _cmd_server)
register("/tools", "查看服务端工具", _cmd_tools)
register("/tool", "手动执行工具", _cmd_tool)
register("/email", "邮件分拣报告", _cmd_email)
register("/pending", "查看待审批工具", _cmd_pending)
register("/approve", "批准工具调用", _cmd_approve)
register("/reject", "拒绝工具调用", _cmd_reject)
register("/trace", "查看 trace 摘要", _cmd_trace)
