# NodeSeek Radar

一个轻量、可自托管的 NodeSeek RSS 关键词监控机器人。

它持续读取 NodeSeek RSS，在新帖标题或摘要命中你设置的关键词时，通过 Telegram Bot 自动推送提醒。项目使用 Python 标准库和 SQLite，不依赖 Docker、数据库服务或 pip 第三方包，适合运行在低配置 VPS 上。

> 默认每 2 秒拉取一次 RSS（0.5 QPS），低于 RSS 服务的 1 QPS 限制。

## 功能

- 每 2 秒轮询 NodeSeek RSS
- 标题和摘要关键词匹配
- Telegram 交互 Bot 与命令菜单
- Telegram 左下角菜单按钮
- 关键词添加、查看、删除、清空
- 支持一次输入多个关键词
- 关键词长度、数量和去重限制
- NodeSeek 分类过滤
- 暂停 / 恢复监控
- 推送记录查看
- 监控状态与 RSS 健康检查
- Telegram 与 RSS 的 HTTP 429 退避处理
- RSS 故障告警与恢复通知
- SQLite 持久化：关键词、去重记录、推送历史、健康状态
- 每日 SQLite 在线备份，默认保留最近 7 份
- 适合 1GB 内存的小型 VPS

## 工作方式

```text
NodeSeek RSS
    ↓ 每 2 秒拉取
RSS 解析与去重
    ↓
分类过滤 + 关键词匹配
    ↓
Telegram 推送
    ↓
SQLite 保存已处理帖子、推送历史与健康状态
```

每条 RSS 项目会依次经过以下步骤：

1. 解析标题、摘要、链接、作者、分类和发布时间；
2. 根据 GUID 判断帖子是否已处理；
3. 检查帖子分类是否启用；
4. 在标题和摘要中查找关键词；
5. 命中后发送 Telegram 推送；
6. 只有推送成功后才标记为已处理；
7. 保存推送历史和最近一次匹配时间。

服务重启不会把已有 RSS 项目重新静默标记为已处理，因此不会因为重启而漏掉停机期间的新帖子。

## Telegram Bot

向 Bot 发送：

```text
/start
```

可打开主菜单。菜单中可以：

- 添加关键词
- 查看关键词列表
- 删除关键词
- 查看监控状态
- 配置分类过滤
- 查看最近推送记录
- 发送推送样式预览
- 暂停或恢复监控
- 查看使用说明

Bot 还会注册 Telegram 命令菜单；在输入框输入 `/` 或点击菜单按钮即可看到可用命令。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/start` | 打开主菜单 |
| `/status` | 查看监控状态、RSS 健康状态和推送统计 |
| `/keywords` | 查看关键词列表 |
| `/add 词1 词2` | 添加一个或多个关键词 |
| `/add` | 进入手动输入关键词状态 |
| `/del 词1 词2` | 删除关键词，并显示不存在的词 |
| `/clear confirm` | 清空全部关键词，需要明确确认 |
| `/help` | 查看帮助 |

### 添加关键词

点击 Bot 中的「添加关键词」后，Bot 会直接进入等待输入状态。你可以直接发送：

```text
dedirock 家人
```

也可以用命令：

```text
/add dedirock 家人
```

关键词规则：

- 单次最多添加 10 个关键词；
- 单个关键词最长 40 个字符；
- 总关键词数量最多 100 个；
- 自动去除首尾空格；
- 大小写不敏感去重，例如 `VPS` 和 `vps` 不会重复添加；
- 关键词删除按钮使用 SQLite 的稳定数字 ID，不受超长关键词或相同前缀影响。

## 分类过滤

NodeSeek RSS 常见分类包括：

```text
trade       交易
review      测评
daily       日常
tech        技术
info        信息
dev         开发
carpool     拼车
expose      曝光
photo-share 图片分享
```

你可以在 Telegram 的「分类过滤」页面逐项开关分类。

默认所有分类启用。只有明确关闭的分类不会触发推送。分类设置保存在 SQLite 中，Bot 修改设置与 Monitor 读取设置不会产生 JSON 半写入或配置读取损坏的问题。

## 推送样式

命中关键词后，Telegram 会收到类似消息：

```text
🛰 NodeSeek 命中
━━━━━━━━━━━━
帖子标题

帖子摘要内容

review · 作者名 · 08-23 19:12
🏷 #关键词1 #关键词2
🔗 打开原帖
```

推送包含「查看原帖」按钮。标题、摘要、作者和链接均经过 HTML 转义，避免 RSS 特殊字符导致 Telegram 消息发送失败。

## 监控状态与健康检查

发送 `/status` 或点击「监控状态」可以查看：

- Monitor 是否运行；
- 监控是否暂停；
- 当前关键词数量；
- 已处理帖子数量；
- 成功推送总数；
- 近 24 小时推送数；
- 启用的分类；
- RSS 最近一次成功拉取时间；
- RSS 连续错误次数；
- 最近一次关键词匹配时间。

RSS 连续失败达到阈值后会发送故障告警；恢复成功后会再发送恢复通知，并附带故障持续时长。

## 限流、重试与可靠性

### RSS

- 默认 2 秒轮询一次，即 0.5 QPS；
- HTTP 429 时读取 `Retry-After` 并等待；
- 其他 HTTP 或网络错误使用递增退避和随机抖动；
- 连续多次失败后会降低请求频率，避免继续对异常端点施压。

### Telegram

- 普通 Bot 交互固定使用 IPv4，避免部分网络环境中 IPv6 长轮询偶发卡顿；
- 命中推送同样使用稳定 IPv4 路径；
- Telegram HTTP 429 时读取 `parameters.retry_after`，等待后重试该条消息；
- 单轮最多推送 10 条，避免异常情况下刷屏。

## 安装

### 环境要求

- Debian / Ubuntu / 其他 Linux 系统
- Python 3.11 或更高版本
- systemd（推荐，但前台运行也支持）
- Telegram Bot Token
- Telegram Chat ID

项目仅使用 Python 标准库，没有第三方依赖：

```text
sqlite3
urllib
xml.etree
logging
unittest
```

### 克隆项目

```bash
git clone https://github.com/Tumb1er1376/nodeseek-radar.git /opt/nodeseek-radar
cd /opt/nodeseek-radar
```

### 配置 Telegram

复制环境变量模板：

```bash
cp .env.example /root/.nodeseek_env
chmod 600 /root/.nodeseek_env
```

编辑 `/root/.nodeseek_env`：

```env
NODESEEK_BOT_TOKEN=你的_BotFather_Token
NODESEEK_CHAT_ID=你的_Telegram_Chat_ID
```

> 不要把 `.nodeseek_env` 上传到 GitHub。它包含 Bot Token 与 chat ID。

### 前台运行

启动监控器：

```bash
python3 nodeseek_monitor.py
```

另开一个终端启动交互 Bot：

```bash
python3 nodeseek_bot.py
```

首次启动时，程序会建立 SQLite 数据库：

```text
/root/nodeseek_monitor.db
```

## systemd 部署

项目提供 systemd 示例单元文件：

```text
systemd/nodeseek-bot.service
systemd/nodeseek-monitor.service
systemd/nodeseek-backup.service
systemd/nodeseek-backup.timer
```

复制前，请根据你的实际项目目录修改其中的：

```text
ExecStart=
WorkingDirectory=
```

例如项目部署在 `/opt/nodeseek-radar`：

```bash
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nodeseek-monitor.service
sudo systemctl enable --now nodeseek-bot.service
sudo systemctl enable --now nodeseek-backup.timer
```

查看状态：

```bash
systemctl status nodeseek-monitor.service
systemctl status nodeseek-bot.service
systemctl list-timers nodeseek-backup.timer
```

查看日志：

```bash
journalctl -u nodeseek-monitor.service -f
journalctl -u nodeseek-bot.service -f
```

## 数据与备份

SQLite 数据库默认保存：

```text
/root/nodeseek_monitor.db
```

其中包括：

- 关键词；
- 分类开关；
- 已处理 RSS 帖子；
- 推送历史；
- 监控初始化状态；
- RSS 健康状态；
- 最近匹配信息。

`nodeseek_backup.py` 使用 SQLite 在线备份 API 创建一致性备份，不会中断监控进程。

默认备份目录：

```text
/root/backups/nodeseek/
```

默认保留最近 7 个备份。

手动执行备份：

```bash
python3 nodeseek_backup.py
```

## 测试

运行回归测试：

```bash
python3 -m unittest -v test_nodeseek_core.py
```

测试覆盖：

- 首次启动与重启基线逻辑；
- 旧关键词表迁移到稳定数字 ID；
- 分类配置 SQLite 持久化；
- Telegram / RSS 的 429 `Retry-After` 处理；
- 关键词去重、长度限制和单次添加上限。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `nodeseek_monitor.py` | RSS 轮询、去重、匹配、推送、限流处理与监控告警 |
| `nodeseek_bot.py` | Telegram 菜单、命令、关键词管理、分类设置和状态页 |
| `nodeseek_core.py` | SQLite schema migration、状态持久化、重试和关键词校验共享逻辑 |
| `nodeseek_backup.py` | SQLite 在线备份脚本 |
| `test_nodeseek_core.py` | 核心逻辑回归测试 |
| `systemd/` | 常驻服务与每日备份定时器示例 |
| `.env.example` | 环境变量模板，不含真实凭证 |

## 隐私与安全

以下文件已经在 `.gitignore` 中排除，不能上传到公开仓库：

```text
.nodeseek_env
nodeseek_monitor.db
nodeseek_monitor.db-wal
nodeseek_monitor.db-shm
nodeseek_offset.txt
nodeseek_paused
backups/
*.log
```

公开部署前请确认：

- 不要提交真实 Telegram Bot Token；
- 不要提交 Telegram Chat ID；
- 不要提交 SQLite 数据库；
- 不要提交推送历史、日志和备份；
- 不要把生产服务器 IP、域名或其他个人部署信息写进 README。

## 许可证

[MIT License](LICENSE)
