# DealRadar (原 FeedSentinel)

DealRadar 是一个面向个人服务器玩家、极客和技术爱好者的轻量级 RSS / Atom 关键词监控工具。专注于二手出鸡、特惠补货、降价与社区交易监控，在新帖标题或摘要命中关键词时，通过 Telegram Bot 进行秒级极简推送。

本仓库仅包含程序源代码、配置模板、systemd 单元模板与自动化测试，不包含任何 Telegram Token、Chat ID、数据库内容、日志或备份等运行时敏感数据。

## 核心功能

- **多源独立监控**：内置 NodeSeek、烧饼论坛 (sb.sb) 与 IDC Flare 等主流出鸡与特惠信息源。
- **实时关键词匹配**：支持标题与正文摘要匹配，不区分大小写，自动去重，低延迟推送。
- **极简 Telegram 控制台**：通过 `/start` 即可管理关键词（增删查）、切换监控网站、查看系统状态与历史记录。
- **高并发与防死锁**：SQLite 采用 WAL 模式，轻量事务控制与连接复用，无锁冲突与界面卡顿。
- **高可用重试与退避**：网络波动或 Telegram 429 限流时自动持久化重试，单源故障不影响其他站点。
- **单用户权限控制**：仅授权指定 Telegram Chat ID 访问与交互，其他用户操作自动忽略。
- **自动化备份与验证**：使用 SQLite Online Backup API 每日自动压缩备份并校验 integrity，自动轮转清理旧备份。
- **零外部依赖**：基于 Python 3 标准库构建，无需安装额外第三方包即可运行。

## 工作流程

```text
RSS / Atom 订阅源 (NodeSeek / 烧饼 / IDC Flare)
            ↓
      并发抓取与解析
            ↓
      去重 → 关键词匹配
            ↓
      SQLite 通知任务队列
            ↓
      Telegram 秒级推送 (失败自动退避重试)
```

## 目录结构

| 文件 / 目录 | 说明 |
| --- | --- |
| `dealradar_monitor.py` | 后台监控核心：抓取源站、匹配关键词、投递 Telegram 通知 |
| `dealradar_bot.py` | Telegram 交互机器人：极简控制台、关键词管理与状态查询 |
| `singleuser.py` | 单用户 SQLite 数据层与并发配置 |
| `rss_core.py` | 监控源站定义与格式通用工具 |
| `rss_monitor.py` | RSS 解析与极简卡片格式化 |
| `backup.py` | SQLite 在线备份与归档校验工具 |
| `backup.sh` / `restore.sh` | 运维备份与恢复执行脚本 |
| `run-bot.sh` / `run-monitor.sh` | 本地测试与手动运行脚本 |
| `systemd/` | 生产环境 systemd 守护服务与定时备份模板 |
| `test_*.py` | 单元测试套件 |
| `.env.example` | 环境变量模板（含占位符） |

## 环境要求

- Linux (Debian / Ubuntu / CentOS 等主流发行版)
- Python 3.10 或更高版本
- `curl`
- `systemd` (推荐用于后台持久化运行)
- Telegram Bot Token 与 Chat ID

## 快速配置

复制配置模板并修改权限：

```bash
cp .env.example .env
chmod 600 .env
```

在 `.env` 中配置你的凭据：

```text
NODESEEK_BOT_TOKEN=你的Telegram_Bot_Token
NODESEEK_CHAT_ID=你的Telegram_Chat_ID
DEALRADAR_DB=/opt/deal-radar/dealradar.db
DEALRADAR_OFFSET=/opt/deal-radar/telegram_offset.txt
DEALRADAR_ENV=/opt/deal-radar/.env
```

> 注：`.env` 仅保存在本地服务器，严禁提交至版本控制系统。

## systemd 部署（推荐）

默认推荐部署在 `/opt/deal-radar` 目录下：

```bash
sudo mkdir -p /opt/deal-radar
sudo cp *.py *.sh /opt/deal-radar/
sudo cp .env /opt/deal-radar/
sudo chmod 600 /opt/deal-radar/.env
sudo chmod +x /opt/deal-radar/*.sh

# 安装服务文件
sudo cp systemd/dealradar-*.service systemd/dealradar-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dealradar-bot.service dealradar-monitor.service dealradar-backup.timer
```

管理与排查命令：

```bash
# 查看服务状态
systemctl status dealradar-bot.service dealradar-monitor.service

# 实时追踪日志
journalctl -u dealradar-bot.service -f
journalctl -u dealradar-monitor.service -f

# 查看自动备份定时器
systemctl list-timers | grep dealradar
```

## 数据备份与恢复

手动执行一次在线热备份：

```bash
/opt/deal-radar/backup.sh /opt/deal-radar/backups
```

从备份恢复（需先停止运行服务）：

```bash
sudo systemctl stop dealradar-bot.service dealradar-monitor.service
/opt/deal-radar/restore.sh /opt/deal-radar/backups/dealradar-YYYYMMDD-HHMMSS.db
sudo systemctl start dealradar-bot.service dealradar-monitor.service
```

## 测试

运行单元测试套件：

```bash
python3 -m unittest discover -p 'test_*.py' -q
# 或使用 pytest
pytest -q
```

## 安全与隐私准则

- 严禁将 `.env`、SQLite 数据库文件（`*.db` / `*-wal` / `*-shm`）、Telegram offset 或备份压缩包提交到 Git。
- 请勿在公开平台泄漏你的 Bot Token 或 Chat ID。
- 如 Token 发生意外泄漏，请立即在 Telegram `@BotFather` 中重置并撤回旧凭据。

## License

MIT License，详见 `LICENSE`。
