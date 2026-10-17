# 云服务器部署（演示与少量用户）

把 Core 和 Gateway 部署到一台 Ubuntu 24.04 云服务器上，手机上的 App 通过 HTTPS 访问。适用于演示和少量用户，不是生产银行系统；账户和交易仍全部是虚构数据。

## 结构

```text
手机 App ──HTTPS 443──> Nginx ──> 127.0.0.1:8000  Core（账本、授权、风控）
                           └────> 127.0.0.1:8100  Gateway（对话、审批、SSE）
```

- 两个服务只监听本机，公网只开放 443。
- 没有域名时用 IP 访问：安装脚本在服务器上生成一个私有 CA，并签发 IP 证书。打包 App 时把这个 CA 打进去，只有服务器地址信任它，其他网站仍用系统证书。这样不需要买域名，也不涉及 ICP 备案。
- 数据放在 `/var/lib/anyan`（账本、设备公钥、会话、审计），配置在 `/etc/anyan/anyan.env`，重启不丢。
- Nginx 按来源 IP 限流，对话、推荐和语音这些会调用模型的接口限制更严，防止被刷造成流量费和模型费用。

## 需要你在阿里云上做的

1. **安全组放行 443**：ECS 控制台 → 实例 → 安全组 → 入方向，添加规则：TCP 443，来源 0.0.0.0/0。确认 22 端口（SSH）已放行；如果你的网络出口 IP 固定，建议把 22 的来源限制为该 IP。不要放行 8000、8100。
2. **添加部署公钥**：ECS 控制台 → 实例 → 远程连接 → Workbench 登录（root），执行：

   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ6ZpRm+dCLL2aB58lGIfpRQjNrMDBZj6RMJx0qbj4Qq anyan-deploy' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
   ```

   这把公钥只对应部署电脑上的 `~/.ssh/id_anyan_deploy`，不会给出任何密码。之后要收回权限，删掉 `authorized_keys` 里这一行即可。
3. **（可选）给 DeepSeek 设消费上限**：在 DeepSeek 开放平台设置余额提醒或限额。服务器会用你的密钥调用模型。

## 部署命令（在本仓库根目录，Git Bash）

```bash
deploy/deploy.sh server 47.107.49.125   # 上传已提交的代码，安装或更新
deploy/deploy.sh keys   47.107.49.125   # 把本机 .env 里的 DeepSeek、百炼密钥写到服务器（可选）
deploy/deploy.sh feishu 47.107.49.125   # 把本机 .env 里的飞书应用 ID 和密钥写到服务器并启用飞书机器人（可选）
deploy/deploy.sh app    47.107.49.125   # 打包连接服务器的 APK：build/deploy/anyan-47.107.49.125.apk
deploy/deploy.sh status 47.107.49.125   # 查看服务状态
```

- 只上传 `git` 已提交的文件，`.env`、日志和构建产物不会上传。
- `keys` 只传 `DEEPSEEK_API_KEY` 和 `DASHSCOPE_API_KEY` 两项，通过 SSH 标准输入传输，不出现在命令行参数里。不运行它，服务器就只用规则解析，也能完整演示。
- 更新代码：提交后再运行一次 `server`。密钥、证书和数据保留不变。
- APK 是 debug 签名的演示包，直接发给用户安装即可。服务器地址或证书变了，需要重新打包。

**飞书机器人**：服务器启用后，由服务器上的网关与飞书保持长连接。同一个飞书应用不要同时在本机开着启用飞书的网关，否则两个连接会争抢消息。飞书绑定依附于 App 的登录会话：退出登录、会话过期或服务器重启后，需要在 App 里重新获取绑定码再绑定。

## 服务器上的常用操作

```bash
systemctl status anyan-core anyan-gateway nginx
journalctl -u anyan-gateway -n 100 --no-pager
nano /etc/anyan/anyan.env && systemctl restart anyan-core anyan-gateway
```

**恢复演示数据**：停服务，删掉 `/var/lib/anyan/ledger.json` 和 `devices.json`，再启动。

## 安言运维台（自制后台）

专门为这套部署做的后台，分六页：

- **总览**：整体状态，24 小时内的登录、对话、模型调用、资金操作和安全拦截次数；三个服务的运行状态、内存和自动重启次数；服务器 CPU、内存、磁盘和网络流量；各项配置是否已设置（密钥只显示“已配置”，不显示内容）。
- **业务数据**：账户余额与锁定资金，以及交易流水、预约、AA、理财持仓等记录数（只读）。
- **访问与流量**：24 小时请求数、访问 IP 数、响应流量、被限流和服务端错误次数、每小时请求图，以及按功能、状态码、IP 的分布。
- **审计事件**：核心和网关的审计记录（中文事件名），可以只看拦截与失败。
- **服务日志**：查看三个服务最近的日志，可以只看警告和错误。
- **运维操作**：重启服务、恢复演示数据，以及操作记录。恢复演示数据时，旧数据移到 `/var/lib/anyan/backup/<时间>/`，不会删除；审计记录保留。

它只监听服务器本机的 9100 端口，公网访问不到；打开时还要输入访问令牌（`/etc/anyan/panel.token`，部署时随机生成）。使用方法：

1. 在部署这台电脑的 Git Bash 里运行下面这条命令。它会先显示访问令牌，再建立隧道；窗口保持打开。

   ```bash
   deploy/deploy.sh panel 47.107.49.125
   ```

   用 PowerShell 也可以：先用 `ssh -i $HOME\.ssh\id_anyan_deploy root@47.107.49.125 cat /etc/anyan/panel.token` 查看令牌，再运行 `ssh -i $HOME\.ssh\id_anyan_deploy -o IdentitiesOnly=yes -N -L 9100:127.0.0.1:9100 root@47.107.49.125`。
2. 浏览器打开 `http://localhost:9100`，粘贴令牌，登录。登录状态保持 12 小时。
3. 用完关掉命令窗口，或按 Ctrl+C。

## 网页管理面板（Cockpit）

服务器装了 Ubuntu 官方的 Cockpit，可以在网页里查看服务、启停服务、看日志、用终端。它只监听服务器本机的 9090 端口，公网访问不到，要通过 SSH 隧道打开：

1. **只需做一次：** 在 Workbench 里以 root 登录，执行 `passwd panel`，为面板用户 `panel` 设置密码（输入两次，屏幕上不显示）。`panel` 有 sudo 权限，面板里可以切换到管理员模式。
2. **每次使用：** 在部署这台电脑的 PowerShell 里执行下面这条命令，窗口保持打开：

   ```powershell
   ssh -i $HOME\.ssh\id_anyan_deploy -o IdentitiesOnly=yes -N -L 9090:127.0.0.1:9090 root@47.107.49.125
   ```

   在 Git Bash 里也可以用 `deploy/deploy.sh cockpit 47.107.49.125`。
3. 浏览器打开 `https://localhost:9090`。证书是面板自签的，浏览器提示不安全时，选“高级 → 继续访问”。用户名填 `panel`，密码填第 1 步设置的密码。
4. 用完关掉命令窗口，或按 Ctrl+C。

面板里的「服务」页找 `anyan-core`、`anyan-gateway`、`nginx`，「日志」页可以按服务筛选。

## 注意

- 演示账号口令 `Bank@2026` 在仓库里是公开的，拿到 App 的人都能登录，看到的都是虚构数据。所以只把 APK 发给需要演示的人。
- 演示验证码在审批卡上获取（`BANK_MFA_DYNAMIC_OTP=1`），不发送真实短信。
- 证书由脚本生成：CA 有效期 10 年，服务器证书 825 天。私钥只保存在服务器的 `/etc/anyan/tls`。
