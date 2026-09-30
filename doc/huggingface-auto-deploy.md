# Hugging Face 自动部署

仓库已配置 `.github/workflows/deploy-huggingface.yml`。完成一次性授权后，GitHub `main` 分支每次更新都会自动同步到 Hugging Face Space，并触发 Docker 重建。

## 一次性设置

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中设置：

### Secret

- `HF_TOKEN`：具有创建和写入 Space 权限的 Hugging Face Access Token。

### Repository variable

- `HF_SPACE`：目标 Space 的完整名称，例如 `LinchenFur/dglab-sleepy`。Hugging Face 用户名和 GitHub 用户名可以不同，请填写真实的 Hugging Face 用户名。

如果目标 Space 不存在，工作流会自动创建一个公开的 Docker Space；如果已经存在，则直接同步代码。

## Space 运行变量

在 Hugging Face Space 的 **Settings → Variables and secrets** 中添加：

### Secrets

```text
SLEEPY_MAIN_SECRET=随机生成的长密码
DGLAB_API_URL=https://你的本地-DG-Lab-隧道域名/api/v2/game/all/action/fire
```

### Variables

```text
SLEEPY_MAIN_HOST=0.0.0.0
SLEEPY_MAIN_PORT=9010
SLEEPY_PAGE_NAME=麟瑞
SLEEPY_MAIN_CORS_ORIGINS=https://blog.cubland.space
SLEEPY_MAIN_DATABASE=sqlite:////data/data.db
```

其中 `/data` 是 Hugging Face 持久化存储的挂载目录。未购买或挂载持久化存储时，数据库在重建后仍可能丢失。

`DGLAB_API_URL` 必须是 Hugging Face 能访问的 HTTPS 地址，不能填写 `127.0.0.1`。建议把本地 Coyote Game Hub 通过受保护的 Cloudflare Tunnel 暴露，并限制来源与触发频率。

## 手动重新部署

可以在 GitHub 的 **Actions → Deploy to Hugging Face Space → Run workflow** 手动重新同步。
