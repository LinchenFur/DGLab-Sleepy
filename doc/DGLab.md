# DG-Lab 集成

DGLab-Sleepy 已迁移到 Sleepy Project 5.2。DG-Lab 功能现在由 `plugins/dglab` 插件提供，不再修改 Sleepy 核心服务端。

完整配置、接口与安全说明请查看：[`plugins/dglab/README.md`](../plugins/dglab/README.md)。

旧版 `DGLab.json` 仍可继续使用；如同时提供 `data/config.toml` 中的 `[plugin.dglab]`，以新版配置为准。
