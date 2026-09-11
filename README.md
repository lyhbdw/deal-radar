# FeedSentinel 单用户版

这是一个单用户 Telegram RSS 关键词监控服务。

## 组成

- `feedsentinel_bot.py`：Telegram 控制机器人。
- `feedsentinel_monitor.py`：RSS 抓取、关键词匹配、通知队列和重试。
- `singleuser.py`：单用户 SQLite 数据层。
- `rss_core.py`：RSS 源定义和消息长度处理。
- `rss_monitor.py`：RSS 解析、抓取和消息格式化。
- `feedsentinel.db`：当前数据文件。
- `telegram_offset.txt`：Telegram 更新游标。
- `run-bot.sh`、`run-monitor.sh`：手动启动脚本。
- `migrate_singleuser.py`：从旧多用户数据库迁移到单用户数据库。
- `backup.sh`、`restore.sh`：一致性备份和恢复。

## 配置

```bash
cp .env.example .env
chmod 600 .env
```

`.env` 至少需要：

```text
NODESEEK_BOT_TOKEN=...
NODESEEK_CHAT_ID=...
```

可选：

```text
FEEDSENTINEL_DB=/opt/feedsentinel/feedsentinel.db
FEEDSENTINEL_OFFSET=/opt/feedsentinel/telegram_offset.txt
FEEDSENTINEL_ENV=/opt/feedsentinel/.env
```

## 启动

```bash
./run-bot.sh
./run-monitor.sh
```

两个进程都有单实例锁，不允许同一目录启动多个 Bot 或 Monitor。

## systemd

将两个 service 文件中的 `/opt/feedsentinel` 改为实际目录，然后安装：

```bash
cp feedsentinel-bot.service feedsentinel-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now feedsentinel-bot.service feedsentinel-monitor.service
```

## 备份与恢复

备份使用 SQLite 在线 backup API，不直接复制 WAL 主文件：

```bash
./backup.sh /path/to/backup-dir
./restore.sh /path/to/backup-dir/feedsentinel-YYYYMMDD-HHMMSS.db
```

恢复前必须停止 Bot 和 Monitor。旧数据库和当前整合目录的原始副本均保留在相邻备份文件中。
