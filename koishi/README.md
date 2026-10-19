# koishi/ — 中控说明

`yunding-bot` 服务直接使用 `koishijs/koishi:latest` 官方镜像，
配置与 [YundingTiangong](https://github.com/Tian-Gong-W/YundingTiangong) 的
`docker-compose.yml` 保持一致（端口 5140，数据卷 `/koishi`）。

- 插件/机器人配置：在 5140 的 Web UI 里配好后，会自动持久化到 `koishi-data` 命名卷
- 如需沿用 YundingTiangong 的品牌化脚本与现有 `/koishi` 数据，
  把老仓库的 `./data` 目录内容拷进命名卷，或改 compose 用 bind mount：
  ```yaml
  volumes:
    - ./koishi/data:/koishi
  ```
  然后把老 `./data` 拷到 `koishi/data/`
