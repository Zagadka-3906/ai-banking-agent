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
deploy/deploy.sh app    47.107.49.125   # 打包连接服务器的 APK：build/deploy/anyan-47.107.49.125.apk
deploy/deploy.sh status 47.107.49.125   # 查看服务状态
```

- 只上传 `git` 已提交的文件，`.env`、日志和构建产物不会上传。
- `keys` 只传 `DEEPSEEK_API_KEY` 和 `DASHSCOPE_API_KEY` 两项，通过 SSH 标准输入传输，不出现在命令行参数里。不运行它，服务器就只用规则解析，也能完整演示。
- 更新代码：提交后再运行一次 `server`。密钥、证书和数据保留不变。
- APK 是 debug 签名的演示包，直接发给用户安装即可。服务器地址或证书变了，需要重新打包。

## 服务器上的常用操作

```bash
systemctl status anyan-core anyan-gateway nginx
journalctl -u anyan-gateway -n 100 --no-pager
nano /etc/anyan/anyan.env && systemctl restart anyan-core anyan-gateway
```

**恢复演示数据**：停服务，删掉 `/var/lib/anyan/ledger.json` 和 `devices.json`，再启动。

## 注意

- 演示账号口令 `Bank@2026` 在仓库里是公开的，拿到 App 的人都能登录，看到的都是虚构数据。所以只把 APK 发给需要演示的人。
- 演示验证码在审批卡上获取（`BANK_MFA_DYNAMIC_OTP=1`），不发送真实短信。
- 证书由脚本生成：CA 有效期 10 年，服务器证书 825 天。私钥只保存在服务器的 `/etc/anyan/tls`。
