# 点子发芽 MVP

这是一个个人自用的新知孵化工作流。它只依赖本地 Markdown 文件、少量 PowerShell 脚本和 Codex automation 提示词，用来支撑每天的轻量输入、晚间整理、旧内容复盘和手动确认。

第一版的原则很简单：白天随手记，晚上看一份短日报，最后由你亲手决定什么保留、什么合并、什么升权或降权。

## 每天怎么用

你可以继续直接编辑 Markdown，也可以启动本地网页作为日常入口。

### 网页入口

本项目新增了一个零依赖的本地 Python Web 服务。它只服务本机浏览器，不需要数据库，也不使用 OpenAI API。

启动：

```powershell
python app.py
```

也可以用脚本启动，效果相同，会明确面向手机访问：

```powershell
.\scripts\start-web.ps1
```

电脑本机访问：

```text
http://127.0.0.1:3000
```

手机访问：

1. 确保手机和电脑在同一个 Wi-Fi 或局域网里。
2. 启动服务后，看终端里打印的“手机访问链接”。
3. 在手机浏览器里打开类似下面的地址：

```text
http://电脑局域网IP:3000
```

例如本机当前 WLAN 地址可能显示为：

```text
http://10.180.77.192:3000
```

注意：手机上不能打开 `http://127.0.0.1:3000`，因为手机里的 `127.0.0.1` 指的是手机自己，不是电脑。

如果 3000 端口被占用，可以临时换端口：

```powershell
$env:IDEA_SPROUT_PORT="3001"; python app.py
```

如果只想让电脑本机能访问，不开放给同一局域网的手机：

```powershell
$env:IDEA_SPROUT_HOST="127.0.0.1"; python app.py
```

如果手机打不开，但电脑本机能打开，通常是 Windows 防火墙拦截了 Python 或 3000 端口。允许 Python 通过当前专用网络，或在防火墙里放行 3000 端口后再试。

如果电脑正在开 VPN，也可能出现手机打不开的情况。优先检查 VPN 客户端里是否有“允许局域网访问”“Allow LAN”“Bypass local network”之类的开关；如果有，打开它。仍然打不开时，可以临时暂停 VPN 再试。除非手机也接入同一个 VPN，否则不要优先使用 VPN 分配的 IP，优先使用 `WLAN` 或 `Ethernet` 对应的 IPv4 地址。

如果你连接的是校园网、公司网或 `WPA2-Enterprise` Wi-Fi，例如 `SJTU`，即使手机和电脑连着同一个 Wi-Fi，也可能因为网络启用了“客户端隔离”而无法互相访问。这种情况下网页服务本身已经启动成功，防火墙也可能没问题，但手机请求到不了电脑。

校园网下更稳的做法：

1. 让电脑连接手机热点，然后重新运行 `.\scripts\start-web.ps1`，用脚本新打印的手机访问链接。
2. 或者让电脑开启 Windows 移动热点，手机连接电脑热点，再访问脚本打印的地址。
3. 如果必须使用校园网，只能看学校网络是否允许同网设备互访；项目代码无法绕过校园网的客户端隔离。

排查脚本：

```powershell
.\scripts\diagnose-phone-access.ps1
```

如果诊断显示服务监听正常、本机访问正常，但手机打不开，优先怀疑校园网客户端隔离。若你确认不是校园网隔离，而是 Windows 防火墙，可以用管理员 PowerShell 运行：

```powershell
.\scripts\allow-phone-firewall.ps1
```

安装依赖：不需要额外安装依赖，只需要本机有 Python 3。

### 设置或修改本地密码

真实密码配置文件是 `config/local_auth.json`。第一次启动 `python app.py` 时，如果该文件不存在，服务会自动创建一个本地配置文件，默认密码是 `change-me`。

建议第一次启动后立刻编辑：

```json
{
  "password": "你的本地密码",
  "session_secret": "一段较长的随机字符串"
}
```

也可以先复制示例文件：

```powershell
Copy-Item .\config\local_auth.example.json .\config\local_auth.json
```

然后修改其中的 `password` 和 `session_secret`。`config/local_auth.json` 已写入 `.gitignore`，不要把真实密码提交进 Git。

### 登录状态

输入正确密码后，服务会写入一个带签名的 `httpOnly` cookie：`idea_sprout_session`。cookie 和服务端签名内容都会记录过期时间，默认 30 天。

30 天后需要重新登录，是为了让本地门禁不会永久有效。点击网页右上角“退出登录”会清除 cookie，再次访问会回到密码页。

### 网页里能做什么

- 在“今日关键词”里一次输入多个关键词，每行一个。
- 为这批关键词写一段上下文。
- 可选填写权重 1-5。
- 提交后内容会追加到当天 `inbox/YYYY-MM-DD.md`，不会覆盖旧内容。
- 在“最近日报”里查看 `synthesis/daily_reports/` 中的日报；页面也会兼容读取旧目录 `reports/daily/`。
- 在“知识节点”里查看 `knowledge/` 中的知识卡片；页面也会兼容读取旧目录 `library/nodes/`。
- 在“点子种子”里查看 `synthesis/idea_seeds/` 中的点子；页面也会兼容读取旧目录 `library/seeds/`。

### 网页门禁的安全限制

这是本地自用的轻量门禁，不是正式产品级用户系统。它没有注册、多用户、找回密码、邮箱验证或短信验证。

当前方案适合自己电脑和同一局域网内的个人设备访问。手机访问时，局域网内其他设备也可能看到这个服务入口，所以请务必修改默认密码。不要把它直接暴露到公网；如果未来要公网访问，需要升级认证方式，例如 HTTPS、正式 session 存储、密码哈希策略、CSRF 防护、访问日志和更严格的权限边界。

1. 白天运行脚本创建当天输入文件：

```powershell
.\scripts\new-today.ps1
```

2. 打开 `inbox/YYYY-MM-DD.md`，在“今日关键词”里写 3-5 个触发词、链接、人物、问题或粗糙想法。
3. 晚上查看 `reports/daily/YYYY-MM-DD.md`。日报应该帮助你判断哪些内容值得留下、哪些只是噪音、哪些和旧内容有关。
4. 根据日报里的“待用户确认”，手动编辑相关 Markdown 文件。

## 晚上会发生什么

Codex automation 应在每天 23:00 Asia/Shanghai 读取当天 `inbox/YYYY-MM-DD.md`。

如果当天有输入，它会：

- 读当天关键词和备注。
- 对每个关键词做 3-6 行的基础理解。
- 检查 `library/nodes/` 中是否已有相近节点。
- 在日报中提出新建、合并、更新、升权、降权或继续追踪建议。
- 只在必要时建议保留来源或点子种子。

如果当天没有有效输入，它会：

- 读取 `tracking/topics.md`。
- 复盘高权重或久未更新的节点。
- 生成一份“无新输入日报”，给出明天一个小动作。

automation 的完整提示词在 `automation/nightly-codex-prompt.md`。如果需要手动创建 automation，就把该文件内容作为任务提示词，工作目录设为本仓库根目录，时间设为每天 23:00 Asia/Shanghai。

## 如何手动确认

日报里的建议不是最终决定。你可以直接编辑这些文件：

- 知识节点：`library/nodes/*.md`
- 来源记录：`library/sources/*.md`
- 点子种子：`library/seeds/*.md`
- 长期追踪主题：`tracking/topics.md`

权重 4 或 5 必须由你手动确认。不要让 automation 自动把一个节点升成核心主题。

## 权重 1-5

- 1：低价值存档，见过即可。
- 2：轻度关注，有意思但暂不投入。
- 3：稳定关注，和长期兴趣有关。
- 4：高优先级，近期反复出现或有行动价值。
- 5：核心主题，值得持续追踪，并可能转成项目、文章、研究或产品。

新节点默认权重 2。与重要主题有关时可以建议 3。同一主题 7 天内多次出现，或关联到多个 active 节点，可以建议 +1。30 天无更新且无行动，可以建议 -1。

## Helper 脚本

创建当天输入文件：

```powershell
.\scripts\new-today.ps1
```

检查目录和关键文件是否完整：

```powershell
.\scripts\validate-structure.ps1
```

脚本只辅助，不会替你修复内容。检查失败时，按输出的缺失项手动补齐。

## 第一版不做

- 不使用 OpenAI API。
- 不引入数据库、向量库或知识图谱引擎。
- 不做复杂或正式产品级 Web UI、移动端、多用户、注册登录体系或商业化功能。
- 不做复杂爬虫、自动订阅系统或复杂标签体系。
- 不自动替你做最终判断。
- 不把日报写成资讯简报。

## 文件位置

- `inbox/`：每日原始输入。
- `knowledge/`：网页入口优先读取的知识节点。
- `synthesis/daily_reports/`：网页入口优先读取的每日复盘日报。
- `synthesis/idea_seeds/`：网页入口优先读取的点子种子。
- `system/`：预留给本地系统说明和后续轻量配置。
- `library/nodes/`：长期知识节点。
- `library/sources/`：来源记录。
- `library/seeds/`：点子种子。
- `reports/daily/`：每日复盘日报。
- `tracking/topics.md`：长期追踪主题。
- `templates/`：可复制修改的模板。
- `automation/`：Codex automation 提示词。
- `scripts/`：本地辅助脚本。
