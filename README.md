# FeedSentinel · 订阅哨兵

轻量自托管的 Telegram 多源 RSS 关键词监控，适合 1GB VPS。

多用户公测中：每位用户独立配置关键词、监控网站、推送历史。

## 监控源

- 🛰 NodeSeek — `https://rss.nodeseek.com/`
- 🥞 烧饼论坛 — `https://sb.sb/rss.xml`
- 🔥 IDC Flare — `https://idcflare.com/latest.rss`

## 架构

| 文件 | 职责 |
|------|------|
| `feedsentinel_multi_bot.py` | Telegram 交互端：按钮菜单、关键词管理、网站开关、状态查看 |
| `feedsentinel_multi_monitor.py` | RSS 监控端：多源并发拉取、关键词匹配、推送队列、Telegram 投递 |
| `feedsentinel_multiuser.py` | 多用户数据层：用户注册、关键词 CRUD、源开关、订阅匹配 |
| `rss_radar_v2_core.py` | 共享层：数据库 schema、源注册、状态管理 |
| `rss_radar_v2_monitor.py` | RSS 解析 + 消息格式化 |

两个 systemd 服务共享同一个 SQLite 数据库（WAL 模式），通过队列表实现解耦：
- monitor 拉取 RSS → 匹配关键词 → 写入 `user_notifications` 队列
- monitor 的 deliver 循环从队列取出 → 发送到用户 Telegram

## Telegram 功能

`/start` 打开控制面板：
- ➕ 添加关键词（多个用空格隔开，每人最多 30 个）
- 📋 我的关键词（查看 / 删除）
- 🗂 选择网站（NodeSeek / 烧饼论坛 / IDC Flare，任意组合）
- 📂 分类过滤（开发中）
- 📜 我的历史（最近 10 条推送）
- 📊 我的状态
- ❓ 帮助

## 运维

```sh
# 服务管理
systemctl status feedsentinel-bot feedsentinel-monitor
systemctl restart feedsentinel-bot feedsentinel-monitor
journalctl -u feedsentinel-bot -f
journalctl -u feedsentinel-monitor -f

# 数据库维护
python3 -c "
import sqlite3
db = sqlite3.connect('/root/nodeseek_monitor.db')
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('VACUUM')
print(db.execute('PRAGMA integrity_check').fetchone()[0])
db.close()
"
```

## 部署

```sh
# 1. 安装文件
cp feedsentinel_multi_bot.py feedsentinel_multi_monitor.py feedsentinel_multiuser.py rss_radar_v2_core.py rss_radar_v2_monitor.py /root/

# 2. 配置凭据
cat > /root/.nodeseek_env << 'EOF'
NODESEEK_BOT_TOKEN=<your_bot_token>
NODESEEK_CHAT_ID=<your_chat_id>
EOF
chmod 600 /root/.nodeseek_env

# 3. 安装 systemd 服务
cp systemd/feedsentinel-bot.service /etc/systemd/system/
cp systemd/feedsentinel-monitor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now feedsentinel-bot feedsentinel-monitor
```

## 安全

- systemd 限制内存（bot 128M / monitor 96M）
- 禁止提升权限、私有临时目录、系统文件只读
- SQLite WAL 模式 + busy_timeout=15s 防止并发锁冲突
- `.nodeseek_env`、数据库、日志不提交到 Git

## License

MIT
