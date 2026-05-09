# Windows Test Plan

这份文档记录需要在 Windows 上验证的客户端行为。Mac 本地主要负责服务端和通用逻辑验证。

## 准备

在 Windows 上拉取最新代码后：

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

另开一个终端：

```bash
cd client
uv sync
uv run python main.py
```

## Tool Use 命令测试

在桌宠输入框里依次输入：

```text
/server
/tools
/tool read_text_file {"path":"README.md","max_chars":200}
/tool run_shell_command {"command":"dir","timeout_seconds":3}
/pending
/approve <pending_id>
```

预期：

- `/server` 显示服务端状态为 `ok`
- `/tools` 显示工具列表，`run_shell_command` 应标记为需要确认
- `read_text_file` 直接执行
- `run_shell_command` 不直接执行，而是返回 pending id
- `/pending` 能看到待审批调用
- `/approve` 后才真正执行命令

## Trace 测试

如果聊天响应或服务端日志里拿到了 `trace_id`：

```text
/trace <trace_id>
```

预期：

- 能看到 turn、tool、approval 等事件摘要

## 注意

- 当前客户端只是通过命令展示审批和 trace，后续会做更自然的 UI。
- 如果客户端提示服务端请求失败，先确认 `server` 是否运行在 `http://127.0.0.1:8000`。
- 如果使用自定义地址，设置 `WISPERA_SERVER_URL`。

