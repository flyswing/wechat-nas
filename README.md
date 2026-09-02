# WeChat NAS 文件归档

将微信接收的文件自动归档到 NAS 指定目录，并在归档成功后通过微信发送确认消息。

## 组成

| 服务 | 作用 |
| --- | --- |
| `wechat-selkies` | 提供浏览器可访问的 Linux 微信客户端。 |
| `wechat-memory-sync` | 解密并同步微信本地消息、文件与媒体信息。 |
| `wechat-nas-agent` | 查找已下载文件，校验后保存到 NAS 归档根目录，并创建回复任务。 |
| `wechat-reply-worker` | 通过微信界面自动发送归档确认。 |

`nas-agent/` 通过 Docker Compose 以只读目录挂载到两个 Python 容器中，不构建或拉取自定义 GHCR 镜像。

## 前置条件

- 群晖 Container Manager / Docker Compose。
- NAS 能访问 Docker Hub、GHCR 与 GitHub。
- 至少 4 GB 内存；建议 8 GB 以上。
- 可从局域网访问 NAS 的 3000 端口，以登录微信。

## 配置

复制示例配置：

```sh
cp .env.example .env
```

在 `.env` 中至少填写：

```env
PASSWORD=替换为强密码
WECHAT_ACCOUNT_DIR_NAME=wxid_xxx_yyyy
NAS_ARCHIVE_DIR=/volume1/WechatArchive
```

- `WECHAT_ACCOUNT_DIR_NAME` 是登录后微信数据目录名称。归档脚本会自动移除最后一个下划线后缀，以得到本机微信 ID，从而避免对自己发送的文件回复。
- `NAS_ARCHIVE_DIR` 是文件保存根目录；不再按联系人或月份创建子目录。
- `ALLOWED_CHAT_USERNAMES` 留空时不限制自动回复对象；填写多个 username 时使用英文逗号分隔。
- 局域网访问时保持 `HTTP_BIND=0.0.0.0`；建议将 `WECHAT_SELKIES_PLATFORM` 留空，由 Docker 按 NAS CPU 架构选择镜像。特殊环境才手动指定 `linux/amd64` 或 `linux/arm64`。

不要提交 `.env`、`config/` 中的真实微信数据或 `runtime/` 中的数据库与密钥。

## 使用步骤

### 1. 获取项目并同步依赖目录

在群晖终端进入项目根目录后，下载脚本并执行：

```sh
curl -fsSL https://raw.githubusercontent.com/flyswing/wechat-nas/main/install.sh | bash
```

该命令会先强制更新当前项目，再把上游项目所需的目录（如 `agent_console/`、`tools/`、`scripts/`）同步到当前目录。它会丢弃当前项目未提交的已跟踪改动，因此应在修改本项目文件前运行。

### 2. 创建并填写本地配置

```sh
cp .env.example .env
```

至少设置 `PASSWORD` 与 `NAS_ARCHIVE_DIR`。首次登录前保留 `WECHAT_ACCOUNT_DIR_NAME` 为空；登录后再按第 4 步检测并填写。

### 3. 启动微信并扫码登录

```sh
docker compose --profile core up -d wechat-selkies
```

在浏览器打开 `http://NAS_IP:3000`，使用手机扫码登录微信。登录后打开需要同步的聊天，等待微信把账号数据与数据库写入 `config/`。

### 4. 检测账号目录并提取数据库密钥

在项目根目录执行：

```sh
./scripts/detect-wechat-account.sh
./scripts/extract-wechat-keys.sh
```

第一个脚本会识别微信账号目录并写入 `.env`；第二个脚本会生成解密所需的 key 与配置。执行提取时应保持微信在线。

### 5. 启动完整归档与回复服务

```sh
docker compose up -d
```

这会启动消息同步、文件归档和自动确认回复。首次建议先观察同步和归档是否正常，再启用或扩大自动回复范围。

### 6. 查看运行状态

```sh
docker compose ps
docker compose logs -f wechat-memory-sync
docker compose logs -f wechat-nas-agent
docker compose logs -f wechat-reply-worker
```

更新项目与上游目录时，再次运行 `./install.sh`；更新后重启受影响服务：

```sh
docker compose up -d --force-recreate wechat-nas-agent wechat-reply-worker
```
## 归档与回复行为

- 仅处理微信数据库中识别为文件的消息。
- 每个消息 ID 只处理一次；处理记录保存在 `runtime/nas-agent/archive.sqlite`。
- 文件复制完成后执行 SHA-256 校验。
- 根目录中已有同名、同内容文件时直接复用；同名但内容不同会追加 12 位哈希，例如 `报告__a1b2c3d4e5f6.pdf`。
- 每个成功归档的文件都会创建一条确认回复任务。
- 回复失败或不确定时标记为 `uncertain`，默认不会自动重试，避免误发重复消息。

## 同步项目与上游文件夹

根目录脚本 `install.sh` 会执行两项强制操作：

1. 将当前项目已跟踪文件硬重置为 `flyswing/wechat-nas` 的 `main` 分支。
2. 从 `xiaoguiwucan/linux-wechat-agent` 稀疏拉取顶级目录，并覆盖同名目录；上游根目录文件不会复制。

运行：

```sh
./install.sh
```

> 警告：该脚本会丢弃当前项目未提交的已跟踪改动，并替换同名上游目录。`.env`、`config/` 和其他被 Git 忽略的运行数据不会被 Git 重置，但仍应在运行前备份。

## 目录说明

```text
nas-agent/                         Python 归档与回复脚本
config/                            微信登录与本地数据（仅保留 .gitkeep）
runtime/                           运行数据库、解密 key 与缓存（不提交）
install.sh 强制同步项目与上游目录的脚本
```