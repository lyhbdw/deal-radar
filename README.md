# FeedSentinel · 订阅哨兵

> 面向个人、技术爱好者与小型团队的轻量级多源订阅监控系统。把分散在多个论坛、社区、博客和公告站的新内容，集中为可靠、可筛选、可追溯的 Telegram 实时提醒。

FeedSentinel 是一个自托管的 RSS / Atom 监控与 Telegram 告警工具。它会持续抓取多个订阅源，在新内容匹配你设置的关键词时，自动推送包含来源、分类、作者、时间、命中词和原帖链接的通知。

项目专为低配置 VPS 设计：只使用 Python 标准库、SQLite、curl 和 systemd，不依赖 Docker、Redis、第三方数据库或 Python 包管理器。在约 1GB 内存的服务器上也能长期稳定运行。

当前内置源：

- 🛰 **NodeSeek**：技术、服务器、交易与社区讨论
- 🥞 **烧饼论坛（sb.sb）**：主机、优惠、交易、AI 与综合讨论
- 🔥 **IDC Flare**：主机、交易、测评、福利和技术社区内容

FeedSentinel 不局限于这些站点。项目的多源架构为后续添加博客、公告板、优惠站、状态页、价格页或任何公开 RSS / Atom 源预留了空间。

---

## 为什么使用 FeedSentinel？

信息源越多，错过重要内容的概率越高：限量 VPS 补货、优惠活动、二手交易、供应商公告、故障信息、某个关键词相关的讨论，往往只在帖子发布后的几分钟内最有价值。

FeedSentinel 的目标不是做一个普通 RSS 阅读器，而是做一个**低噪音、可控、可恢复的实时信息哨兵**：

- 只推送命中关键词的新内容，而不是把全部帖子刷到 Telegram。
- 多个网站共享一套关键词，也可以独立决定哪个网站开启监控。
- 单个网站发生 Cloudflare、网络波动或源站故障，不应拖慢其他网站。
- Telegram 临时失败、429 限流或网络异常时，通知要进入持久化队列并重试，而不是直接丢失。
- 运行状态、历史记录、队列、分类和源开关都保存在 SQLite，服务重启后仍可恢复。

---

## 核心能力

### 多源独立监控

- 同时监控多个 RSS / Atom 源。
- 每个源独立开启、关闭和分类过滤。
- 支持只开启一个站点、任意组合两个站点，或全部开启。
- 单个源抓取失败时采用独立退避，不影响其他源继续检查。
- 当前默认按高频模式运行；实际检查频率会受到源站响应时间、网络状态和源站限制影响。

### 关键词实时匹配

- 关键词默认对所有**已启用**的源共享。
- 匹配范围包括标题和正文摘要。
- 支持一次添加多个关键词，自动去重并忽略大小写差异。
- 关键词长度、单次数量和总数量均有保护，避免误操作和无效规则。
- 每条通知会展示实际命中的关键词，便于确认提醒原因。

### Telegram 交互控制台

无需 SSH 或手动编辑数据库。向 Bot 发送 `/start` 即可打开控制台：

- `🗂 源管理`：站点开关、仅开某站、全开、全关、单源刷新
- `➕ 添加关键词`：新增关注词
- `📋 关键词列表`：查看、删除、清空关键词
- `📂 分类过滤`：按站点关闭不想看的板块
- `📊 监控状态`：查看抓取健康、最近成功、错误和处理量
- `📜 推送历史`：查看最近已发送提醒
- `📅 今日摘要`：聚合近 24 小时命中情况
- `📥 推送队列`：查看待发送、发送中、已发送或待重试消息
- `⏸ 暂停监控`：临时停止抓取和推送；恢复后继续运行

### 可靠投递与失败恢复

- 已见内容按 `source + guid` 去重，避免多源 GUID 冲突。
- 新命中内容先写入 SQLite 通知队列，再调用 Telegram API。
- Telegram 发送失败、网络错误和 HTTP 429 限流会触发延迟重试。
- 发送中的任务如果进程异常退出，会在超时后被回收并重新投递。
- 每个源独立记录最近成功时间、连续错误、最近错误、请求耗时和条目数量。

### 数据安全与维护

- SQLite 使用 WAL 模式，支持 monitor 与 Bot 并发访问。
- 每日在线备份，不需要停止服务。
- 备份后执行 `PRAGMA integrity_check` 验证数据库可用性。
- 备份保留策略：最近 7 天完整备份、最近 4 周每周备份、最近 3 个月每月备份。
- systemd 提供自动重启、内存限制、私有临时目录和禁止权限提升。

---

## 工作原理

```text
多个 RSS / Atom 源
        │
        ├── NodeSeek
        ├── 烧饼论坛
        ├── IDC Flare
        └── 后续可扩展的任意订阅源
        │
        ▼
独立抓取、解析与健康检查
        │
        ▼
去重 → 分类过滤 → 关键词匹配
        │
        ▼
SQLite 持久化通知队列
        │
        ▼
Telegram 投递 / 429 退避 / 失败重试
        │
        ▼
推送历史、每日摘要、状态面板与备份
```

---

## Telegram 使用说明

### 启动控制台

向 Bot 发送：

```text
/start
```

Bot 会展示 FeedSentinel 的主控制台。所有操作都可以通过按钮完成，也保留命令方式供快速输入使用。

### 管理监控网站

进入 `🗂 源管理` 后，每个网站都能独立操作。

你可以：

- 开启或关闭某个具体网站。
- 只开启 NodeSeek、烧饼论坛或 IDC Flare 中的任意一个。
- 自由组合开启任意两个网站。
- 一键全部开启或全部关闭。
- 查看每个源最近成功抓取时间、请求耗时和连续错误次数。
- 对某个源提交立即刷新请求。

这些状态存储在 SQLite 中，重启系统、重启服务或更新程序后不会丢失。

### 管理关键词

点击 `➕ 添加关键词` 后，直接发送文本即可。

例如：

```text
甲骨文 VMISS 家宽 9929
```

这会添加四个关键词。只要启用源中的新内容标题或摘要出现其中之一，就会触发提醒。

也可以使用命令：

```text
/add 甲骨文 VMISS 家宽
/del 家宽
/keywords
/clear confirm
```

### 管理分类

进入 `📂 分类过滤`，选择一个源后可以关闭不关注的分类。例如：

- NodeSeek 只关注 `trade`、`promotion`
- 烧饼论坛关闭 `综合`
- IDC Flare 关闭 `求助`、`茶馆`

未明确关闭的分类默认允许通知；这样当网站新增板块时，不会因为静态白名单遗漏重要帖子。

### 查看状态、历史与队列

- `📊 监控状态`：查看每个源是否正常、最近成功时间和连续错误。
- `📜 推送历史`：查看最近成功发送的提醒。
- `📅 今日摘要`：查看近 24 小时不同源和分类的推送统计。
- `📥 推送队列`：确认是否有因 Telegram 或网络问题暂未发送的消息。

---

## 推送消息示例

```text
🥞 烧饼论坛 命中
━━━━━━━━━━━━
GreenCloud 东京优化套餐补货

库存恢复，适合 CN2 GIA / 9929 / CMIN2 使用场景……

主机 · 某用户 · 08-27 20:15
🏷 #绿云 #补货 #9929
🔗 打开原帖
```

消息包含：

- 信息来源及图标
- 标题与摘要
- 分类、作者、发布时间
- 实际命中的关键词
- 原帖跳转链接

---

## 部署

### 环境要求

- Linux VPS（推荐 Debian / Ubuntu）
- Python 3.10+（当前环境使用 Python 3.11）
- curl
- systemd
- 一个 Telegram Bot Token 和你的 Telegram Chat ID

不需要：

- Docker
- MySQL / PostgreSQL
- Redis
- Node.js
- pip 安装第三方依赖

### 1. 获取代码

```sh
git clone https://github.com/Tumb1er1376/feed-sentinel.git
cd feed-sentinel
```

### 2. 准备 Telegram 凭据

```sh
cp .env.example /root/.nodeseek_env
chmod 600 /root/.nodeseek_env
```

填写文件：

```ini
NODESEEK_BOT_TOKEN=你的_Telegram_Bot_Token
NODESEEK_CHAT_ID=你的_Telegram_Chat_ID
```

> 变量名中的 `NODESEEK` 是历史兼容名称，实际上供 FeedSentinel 的所有监控源共同使用。不要将真实凭据提交到 GitHub。

### 3. 安装 systemd 服务

```sh
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nodeseek-monitor nodeseek-bot nodeseek-backup.timer
```

> systemd unit 名称保留 `nodeseek-*`，用于兼容原有部署与无停机升级；服务展示名称和实际功能均为 FeedSentinel。

---

## 运维命令

```sh
# 查看服务状态
systemctl status nodeseek-monitor nodeseek-bot

# 跟踪实时抓取日志
journalctl -u nodeseek-monitor -f

# 查看 Bot 日志
journalctl -u nodeseek-bot -f

# 手动创建并验证备份
python3 nodeseek_backup.py

# 查看备份定时任务
systemctl list-timers | grep nodeseek

# 运行所有测试
python3 -m unittest discover -p 'test_*.py' -q
```

默认运行路径：

| 内容 | 默认路径 |
| --- | --- |
| SQLite 数据库 | `/root/nodeseek_monitor.db` |
| Telegram 凭据 | `/root/.nodeseek_env` |
| Bot offset | `/root/nodeseek_offset.txt` |
| 备份目录 | `/root/backups/nodeseek/` |
| monitor 日志 | `/root/nodeseek_monitor.log` |

上述路径保留旧名称是为了与已有 NodeSeek Radar 部署兼容；新安装项目可以按需要自行修改为 FeedSentinel 专用路径。

---

## 数据恢复

1. 停止服务：

   ```sh
   systemctl stop nodeseek-monitor nodeseek-bot
   ```

2. 从 `/root/backups/nodeseek/` 选择一个备份。
3. 将备份复制为 `/root/nodeseek_monitor.db`。
4. 删除数据库同目录下的 `nodeseek_monitor.db-wal` 与 `nodeseek_monitor.db-shm`。
5. 执行完整性检查：

   ```sh
   python3 -c "import sqlite3; print(sqlite3.connect('/root/nodeseek_monitor.db').execute('PRAGMA integrity_check').fetchone())"
   ```

6. 输出应为 `('ok',)`；然后启动服务：

   ```sh
   systemctl start nodeseek-monitor nodeseek-bot
   ```

---

## 开发、测试与安全

### 测试

项目使用 Python 标准库 `unittest`：

```sh
python3 -m unittest discover -p 'test_*.py' -q
```

测试覆盖关键词校验、数据库迁移、分类隔离、源开关、通知队列、重试逻辑、HTML 清理和源注册表完整性。

### 安全原则

- 不提交 `.nodeseek_env`、SQLite 数据库、offset、日志、备份或回滚目录。
- 使用 `.env.example` 作为凭据模板。
- 运行服务设置 `MemoryMax=128MB`，避免异常时耗尽低内存 VPS。
- 启用 `NoNewPrivileges=true`、`PrivateTmp=true` 和 `ProtectSystem=full`。
- GitHub 仓库只包含代码、文档、测试和 service 模板，不包含任何实际 token 或个人数据。

---

## 路线图

FeedSentinel 未来会持续增强：

- 更多 RSS / Atom 源与可配置源模板
- 更丰富的历史分页、筛选与导出
- 更精细的关键词规则：排除词、标题优先、组合规则
- 每源独立轮询间隔与限流策略
- 通知静默时段、突发合并与日报推送
- 加密异地备份与自动恢复演练

欢迎提交 Issue、功能建议和新的订阅源适配方案。

## 许可证

MIT License。详见 [LICENSE](LICENSE)。
