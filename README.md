# NodeSeek Radar

一个轻量的 NodeSeek RSS 关键词监控器：每 2 秒检查一次 RSS，在标题或摘要命中关键词后通过 Telegram 推送。

特点：

- Python 标准库实现，不需要 pip 依赖
- Telegram 交互 Bot：关键词、分类、暂停、状态、历史记录
- 关键词数据和监控状态保存在 SQLite
- RSS 与 Telegram 429 退避处理
- 断线恢复提醒、健康状态页、每日 SQLite 在线备份
- 适合低配置 VPS

## 安装

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/nodeseek-radar.git /opt/nodeseek-radar
cd /opt/nodeseek-radar
cp .env.example /root/.nodeseek_env
chmod 600 /root/.nodeseek_env
```

编辑 `/root/.nodeseek_env`：

```env
NODESEEK_BOT_TOKEN=你的_BotFather_Token
NODESEEK_CHAT_ID=你的_Telegram_Chat_ID
```

首次运行会在 `/root/nodeseek_monitor.db` 创建 SQLite 数据库。

## 运行

前台运行：

```bash
python3 nodeseek_monitor.py
python3 nodeseek_bot.py
```

测试：

```bash
python3 -m unittest -v test_nodeseek_core.py
```

## systemd 部署

将 `systemd/` 下单元文件复制到 `/etc/systemd/system/`。根据实际部署目录修改 `ExecStart` 和 `WorkingDirectory`。

```bash
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nodeseek-monitor.service nodeseek-bot.service nodeseek-backup.timer
```

## Telegram 操作

- `/start`：打开菜单
- `/status`：查看监控与 RSS 健康状态
- `/keywords`：查看关键词
- `/add 关键词1 关键词2`：添加关键词；不带参数会进入手动输入状态
- `/del 关键词1`：删除关键词
- `/clear confirm`：清空全部关键词
- `/help`：查看说明

## 数据文件

以下内容是本地隐私/运行状态，已被 `.gitignore` 排除：

- `.nodeseek_env`：Telegram Token 和 chat ID
- `nodeseek_monitor.db` 及 SQLite WAL 文件：关键词、历史和状态
- `backups/`：每日备份
- 日志、offset、暂停标记

请勿把这些文件上传到公开仓库。

## 许可证

MIT
