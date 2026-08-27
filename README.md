# RSS Radar

轻量自托管的 Telegram 多源 RSS 关键词雷达，适合 1GB VPS。

当前源：NodeSeek、烧饼论坛（sb.sb）、IDC Flare。所有源默认共享关键词；分类和源开关独立管理。

## 架构

- `nodeseek_monitor.py`：每个源独立调度、独立退避；SQLite WAL 保存去重、健康和通知队列。
- `nodeseek_bot.py`：Telegram 长轮询控制面板。
- `nodeseek_core.py`：源注册、数据库迁移、通知队列、配置逻辑。
- `nodeseek_backup.py`：SQLite 在线备份并执行完整性检查；保留 7 日、4 周、3 月。

## Telegram 功能

`/start` 打开控制面板：
- 共享关键词的添加、删除、清空
- 每源独立启用/禁用与即时刷新
- 每源分类过滤
- 状态、延迟、错误、最近成功时间
- 推送历史、近 24 小时摘要、持久化推送队列状态
- 全局暂停/恢复

## 运维

```sh
systemctl status nodeseek-monitor nodeseek-bot
journalctl -u nodeseek-monitor -f
python3 /root/nodeseek_backup.py
python3 -m unittest discover -s /root -p 'test_*.py' -q
```

数据：`/root/nodeseek_monitor.db`；凭据：`/root/.nodeseek_env`（0600）。

## 恢复

1. 停止服务：`systemctl stop nodeseek-monitor nodeseek-bot`
2. 选取备份：`/root/backups/nodeseek/nodeseek-*.db`
3. 复制为 `/root/nodeseek_monitor.db`，删除同名 `-wal`/`-shm`
4. 执行 `python3 -c "import sqlite3; print(sqlite3.connect('/root/nodeseek_monitor.db').execute('PRAGMA integrity_check').fetchone())"`
5. 启动服务：`systemctl start nodeseek-monitor nodeseek-bot`

## 安全

systemd 限制内存为 128MB，禁止提升权限、启用私有临时目录，并将系统文件设为只读。不要将 `.nodeseek_env`、数据库、日志和备份提交到 Git。
