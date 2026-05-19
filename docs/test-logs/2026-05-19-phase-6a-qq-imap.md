# Phase 6A QQ/Foxmail IMAP Provider

日期：2026-05-19

## 目标

将真实邮箱接入方向从 Outlook / Microsoft Graph 改为个人 QQ/Foxmail 邮箱。第一阶段使用 IMAP 接入真实邮箱，支持读取、详情、搜索和 MIME/HTML 清洗；写操作仍走 dangerous approval。

## 已完成

- 新增 `server/app/qq_imap_provider.py`。
- `WISPERA_EMAIL_PROVIDER` 支持 `qq-imap` / `qq` / `foxmail` / `foxmail-imap`。
- 支持 `list_recent`、`get_detail`、`search`。
- 支持 IMAP `UNSEEN` 过滤。
- `email_id` 使用 `imap-<uid>`，底层 fetch/store/copy 走 IMAP UID。
- 支持 MIME subject/from/body 解码。
- 支持 HTML 到纯文本清洗。
- 支持 approval 后的 `mark_read`、`archive`、`star`、`create_draft`。
- `create_draft` 只追加到 IMAP 草稿箱，不发送邮件。

## 验证

```bash
python3 -m unittest tests.test_email_tools
```

结果：

```text
41 tests OK
```

```bash
python3 -m py_compile server/app/*.py server/evaluate_email.py client/aemeath/*.py tests/test_email_tools.py
```

结果：通过。

## 需要手动验证

- Foxmail/QQ 授权码能否登录 `imap.qq.com:993`。
- `Drafts` / `Archive` 文件夹名是否与当前账号实际一致。
- 归档行为是否符合 QQ/Foxmail 预期。
- 草稿追加后，网页版或客户端是否能看到草稿。
- 标记已读是否能在网页版同步。

## 结论

QQ/Foxmail IMAP provider 的自动化覆盖已完成。真实邮箱行为需要用户本地账号验证后再继续 UI polish 或更细的文件夹兼容。
