# Trigger tests

Should trigger:
- “检查当前 venv 的 VectorBT 是否能用。”
- “验证 DuckDB 的 bt_ready 视图能被 VectorBT 读取。”

Should not trigger:
- “创建新环境并把密钥写入 .env。”
- “运行 Backtrader 事件驱动策略。”

