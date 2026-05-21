# 测试日志

主测试和评估方法见 [测试与评估](../testing-and-evaluation.md)。这个目录只保留必要的验证快照，不再保存按阶段展开的长日志。

记录原则：

- 只记录关键命令、结果和结论。
- 不记录 API key、授权码、真实邮箱正文、真实标签文件、完整 trace 或大段命令输出。
- 当前真实邮箱方向只记录 QQ/Foxmail IMAP；其他 provider 计划不再作为当前路线。

当前验证快照：

- `python3 -m unittest tests.test_email_tools`：57 tests OK。
- `python3 -m py_compile server/app/*.py server/evaluate_email.py server/email_cli.py client/aemeath/*.py tests/test_email_tools.py`：通过。
- QQ/Foxmail recent/detail/search、mark read、archive、star、create draft 已完成本地手测。
- `server/data/real_email_labels.json` 是本地真实标签文件，已加入 `.gitignore`，不提交。
