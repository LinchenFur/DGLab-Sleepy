# DG-Lab 插件

此插件将旧版 DGLab-Sleepy 的郊狼触发功能迁移到 Sleepy Project 5.2 插件架构。

## 功能

- 新版接口：`POST /api/dglab/lightning`
- 旧版兼容接口：`POST /dglab/lightning`
- 公开配置：`GET /api/dglab/config`
- 触发统计：`GET /api/dglab/stats`
- 内置按 IP 限流
- 可选 Cloudflare Turnstile
- 自动读取仓库根目录旧版 `DGLab.json`
- 首次启动时自动把旧版 `data.json` 状态和设备迁移到 v5 SQLite 数据库
- 兼容旧版根目录 `.env` 的常用配置名
- 在首页显示 DG-Lab 触发卡片，并在管理面板显示插件状态

## 配置

在 `data/config.toml` 中启用插件并填写配置：

```toml
plugins_enabled = ["v4_compatible", "theme_detect", "dglab"]

[plugin.dglab]
enabled = true
api_url = "http://127.0.0.1:8920/api/v2/game/all/action/fire"
public_trigger = true
show_index_card = true
request_timeout = 10
rate_limit_count = 5
rate_limit_window = 60
continuous_click = true

[plugin.dglab.strength]
min = 1
max = 30

[plugin.dglab.duration]
min = 1
max = 3

[plugin.dglab.cooldown]
min = 3
max = 8

[plugin.dglab.turnstile]
enabled = false
site_key = ""
secret_key = ""
```

如未设置 `[plugin.dglab]`，插件会尝试读取旧版 `DGLab.json`，因此旧配置可以直接迁移。

在 Hugging Face、Docker 等容器环境中，也可以使用 Secret `DGLAB_API_URL` 覆盖控制端地址。

## 安全提示

DG-Lab 会实际控制硬件。请把强度设在自己确认安全的范围内，并建议开启 Turnstile、反向代理访问控制或设置 `public_trigger = false`。关闭公开触发后，请通过 Sleepy 密钥调用接口。
