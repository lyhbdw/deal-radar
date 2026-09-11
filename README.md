# FeedSentinel 单用户版

FeedSentinel 是一个面向个人使用的自托管 RSS / Atom 关键词监控工具。它定时抓取多个公开订阅源，在新内容的标题或摘要命中关键词时，通过 Telegram Bot 发送提醒。

本仓库只包含程序代码、配置模板、服务模板和测试，不包含 Telegram Token、Chat ID、数据库、日志、备份或其他运行时隐私数据。

## 功能

- 多个 RSS / Atom 来源独立抓取
- 当前内置 NodeSeek、烧饼论坛和 IDC Flare
- 标题与摘要关键词匹配
- 关键词大小写不敏感、自动去重
- 单用户模式：仅配置的 Telegram Chat ID 可以操作 Bot
- Telegram 按钮控制台
- 添加、查看和删除关键词
- 开启或关闭监控来源
- SQLite 持久化已见内容、通知队列和运行状态
- Telegram 发送失败时自动重试
- RSS 来源独立退避，单个来源故障不影响其他来源
- SQLite 在线备份与恢复
- systemd 自动启动和异常重启
- 单实例锁，避免重复启动 Bot 或 Monitor

## 工作流程

```text
RSS / Atom 来源
      ↓
独立抓取与解析
      ↓
去重 → 关键词匹配
      ↓
SQLite 通知队列
      ↓
Telegram 投递与失败重试
```

## 目录说明

| 文件 | 作用 |
| --- | --- |
| `feedsentinel_monitor.py` | 抓取 RSS、匹配关键词、生成通知并投递 |
| `feedsentinel_bot.py` | Telegram 控制台和交互处理 |
| `singleuser.py` | 单用户 SQLite 数据层 |
| `rss_core.py` | RSS 来源定义和通用数据处理 |
| `rss_monitor.py` | RSS 解析、抓取和消息格式化 |
| `backup.py` | SQLite 在线备份工具 |
| `migrate_singleuser.py` | 从旧数据库结构迁移到单用户结构 |
| `systemd/feedsentinel-bot.service` | Bot 服务模板 |
| `systemd/feedsentinel-monitor.service` | Monitor 服务模板 |
| `test_*.py` | 单元测试 |
| `.env.example` | 环境变量模板，不含真实凭据 |

## 环境要求

- Linux
- Python 3.10 或更高版本
- `curl`
- `systemd`（仅使用 systemd 部署时需要）
- Telegram Bot Token
- Telegram Chat ID

程序只使用 Python 标准库，不需要安装第三方 Python 包。

## 配置

复制配置模板，并填写自己的值：

```bash
cp .env.example .env
chmod 600 .env
```

`.env` 示例：

```text
NODESEEK_BOT_TOKEN=填写你的_bot_token
NODESEEK_CHAT_ID=填写你的_chat_id
FEEDSENTINEL_DB=/opt/feedsentinel/feedsentinel.db
FEEDSENTINEL_OFFSET=/opt/feedsentinel/telegram_offset.txt
FEEDSENTINEL_ENV=/opt/feedsentinel/.env
```

注意：

- `.env` 只能保存在服务器本地，不要提交到 Git。
- `.env.example` 只能放占位符，不能放真实 Token 或 Chat ID。
- `NODESEEK_*` 是历史兼容命名，实际用于整个 FeedSentinel 服务。
- Chat ID 是单用户授权依据，不能公开发布。

## 手动运行

先确认配置文件存在，然后分别启动 Bot 和 Monitor：

```bash
./run-bot.sh
./run-monitor.sh
```

如果当前目录没有启动脚本，也可以直接运行：

```bash
python3 feedsentinel_bot.py
python3 feedsentinel_monitor.py
```

程序会使用单实例锁，重复启动同一组件会被拒绝。

## systemd 部署

先检查两个 service 文件中的路径是否与实际部署目录一致。默认目录为 `/opt/feedsentinel`：

```bash
sudo mkdir -p /opt/feedsentinel
sudo cp *.py /opt/feedsentinel/
sudo cp .env /opt/feedsentinel/
sudo chmod 600 /opt/feedsentinel/.env
sudo cp systemd/feedsentinel-bot.service /etc/systemd/system/
sudo cp systemd/feedsentinel-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now feedsentinel-bot.service feedsentinel-monitor.service
```

查看服务：

```bash
systemctl status feedsentinel-bot.service feedsentinel-monitor.service
```

查看日志：

```bash
journalctl -u feedsentinel-bot.service -f
journalctl -u feedsentinel-monitor.service -f
```

重启服务：

```bash
sudo systemctl restart feedsentinel-bot.service feedsentinel-monitor.service
```

## Telegram 使用

向 Bot 发送 `/start` 打开控制台。

主要功能包括：

- 添加关键词
- 查看关键词
- 删除关键词
- 选择监控网站
- 查看系统状态
- 查看推送历史
- 发送测试消息

只有 `.env` 中 `NODESEEK_CHAT_ID` 对应的用户可以操作控制台，其他用户的消息和按钮操作会被忽略。

## 数据文件

默认运行时会产生以下本地文件：

- SQLite 数据库：`feedsentinel.db`
- Telegram 更新游标：`telegram_offset.txt`
- Bot 单实例锁：`bot.lock`
- Monitor 单实例锁：`monitor.lock`
- 备份文件：由备份脚本指定的目录

这些文件可能包含个人关键词、Telegram 标识、消息历史和运行状态，均不应提交到公开仓库。

## 备份与恢复

SQLite 备份使用在线备份接口，避免直接复制 WAL 数据库文件：

```bash
./backup.sh /path/to/backup-dir
```

恢复前先停止两个服务：

```bash
sudo systemctl stop feedsentinel-bot.service feedsentinel-monitor.service
./restore.sh /path/to/backup-dir/feedsentinel-YYYYMMDD-HHMMSS.db
sudo systemctl start feedsentinel-bot.service feedsentinel-monitor.service
```

恢复后建议检查数据库：

```bash
python3 -c "import sqlite3; print(sqlite3.connect('feedsentinel.db').execute('PRAGMA integrity_check').fetchone()[0])"
```

正常结果应为：

```text
ok
```

## 从旧结构迁移

如果已有旧版数据库，先备份，再运行迁移脚本：

```bash
python3 migrate_singleuser.py
```

迁移前必须确认数据库路径和配置正确，并保留原数据库副本，以便回滚。

## 测试

运行全部测试：

```bash
python3 -m unittest discover -p 'test_*.py' -q
```

运行 Python 语法检查：

```bash
python3 -m py_compile \
  backup.py \
  feedsentinel_bot.py \
  feedsentinel_monitor.py \
  migrate_singleuser.py \
  rss_core.py \
  rss_monitor.py \
  singleuser.py
```

## 安全说明

- 不要把 `.env`、数据库、日志、备份、Telegram offset 或锁文件提交到 Git。
- 不要在 README、Issue、提交信息或截图中公开 Token、Chat ID、服务器 IP、个人邮箱或消息内容。
- 公开仓库只保留配置模板和占位符。
- 如果 Token 曾经泄露，应立即在 BotFather 中撤销并重新生成。
- 如果 Chat ID、服务器地址或日志内容已经公开，应删除公开内容并视需要更换相关凭据。

## License

MIT License，详见 `LICENSE`。
