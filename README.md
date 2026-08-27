# FeedSentinel · 订阅哨兵

FeedSentinel 是一个轻量、自托管的多源 RSS / Atom 监控与 Telegram 告警系统。

它持续抓取多个信息源，在新内容命中你的关键词时发送结构化 Telegram 提醒。项目采用 Python 标准库、SQLite 和 systemd，无需 Docker、Redis、外部数据库或第三方 Python 依赖，适合 1GB 内存 VPS。

当前内置源：

- 🛰 NodeSeek
- 🥞 烧饼论坛（sb.sb）
- 🔥 IDC Flare

## 能力

- 多源 RSS 监控，关键词默认对全部启用源共享
- 每个源可独立启用、禁用；可只开一个、任意两个或全部开启
- 每个源独立分类过滤
- 高优先级源持续 1 秒级检查；单源抓取失败不会影响其它源
- Telegram 交互控制台：源管理、关键词、分类、健康状态、历史、摘要、推送队列
- SQLite WAL 持久化：去重、关键词、分类、源开关、历史、健康状态和待发送消息
- 失败推送重试、HTTP 429 退避、故障健康指标
- 每日在线 SQLite 备份、完整性检查及 7 日 / 4 周 / 3 月保留
- systemd 自动重启、内存上限、私有临时目录和最小权限加固

## 工作方式

```text
RSS / Atom Sources
        │
        ├── NodeSeek
        ├── 烧饼论坛
        └── IDC Flare
        │
独立抓取与解析 → 去重 / 分类过滤 / 关键词匹配
        │
持久化通知队列 → Telegram 投递与失败重试
        │
SQLite: 已见内容 / 历史 / 健康 / 源配置 / 队列
```

## Telegram 使用

向 Bot 发送 `/start`，打开 FeedSentinel 控制台。

### 源管理

进入 `🗂 源管理` 后可以：

- 分别启用或禁用 NodeSeek、烧饼论坛、IDC Flare
- 直接选择“仅开某站”
- 全部开启或全部关闭
- 查看每个源最近成功时间、请求延迟和连续错误
- 对单个源发起立即刷新

源状态保存在 SQLite，重启不会丢失。

### 关键词与分类

- `➕ 添加关键词`：发送单词，多个关键词用空格分隔
- `📋 关键词列表`：删除或清空关键词
- `📂 分类过滤`：按源关闭不需要的分类
- 新发现的分类默认启用，避免漏掉新板块

### 运行状态

- `📊 监控状态`：查看每个源健康状况
- `📜 推送历史`：查看最近命中
- `📅 今日摘要`：按源和分类聚合近 24 小时推送
- `📥 推送队列`：查看待重试、发送中或已发送消息

## 部署

### 1. 准备凭据

```sh
cp .env.example /root/.nodeseek_env
chmod 600 /root/.nodeseek_env
```

填写：

```ini
NODESEEK_BOT_TOKEN=你的_Telegram_Bot_Token
NODESEEK_CHAT_ID=你的_Telegram_Chat_ID
```

变量名为兼容旧部署而保留；它们同时服务于 FeedSentinel 的所有源。

### 2. 安装 systemd 服务

```sh
cp systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nodeseek-monitor nodeseek-bot nodeseek-backup.timer
```

> 服务名保留 `nodeseek-*`，是为了让现有部署无停机升级；它们实际运行的是 FeedSentinel。

## 运维命令

```sh
# 服务状态
systemctl status nodeseek-monitor nodeseek-bot

# 查看实时监控日志
journalctl -u nodeseek-monitor -f

# 手动备份
python3 nodeseek_backup.py

# 运行测试
python3 -m unittest discover -p 'test_*.py' -q
```

默认运行数据：

- 数据库：`/root/nodeseek_monitor.db`
- 凭据：`/root/.nodeseek_env`（0600）
- 备份：`/root/backups/nodeseek/`

这些路径因兼容旧部署而保留，均不应提交到 Git。

## 恢复

1. 停止服务：

   ```sh
   systemctl stop nodeseek-monitor nodeseek-bot
   ```

2. 选择一个备份，复制为 `/root/nodeseek_monitor.db`。
3. 删除同名 `-wal` 和 `-shm` 文件。
4. 验证数据库：

   ```sh
   python3 -c "import sqlite3; print(sqlite3.connect('/root/nodeseek_monitor.db').execute('PRAGMA integrity_check').fetchone())"
   ```

5. 启动服务：

   ```sh
   systemctl start nodeseek-monitor nodeseek-bot
   ```

## 开发与安全

- 不提交 `.nodeseek_env`、SQLite 数据库、offset、日志、备份或回滚目录。
- 使用 `.env.example` 作为凭据模板。
- systemd 服务限制内存至 128MB，禁止权限提升，启用私有 `/tmp`，并把系统层设为只读。
- 提交前应运行完整 unittest 套件。

## 许可证

MIT License。详见 [LICENSE](LICENSE)。
