# ADR-0008 · package-lock 必须指向公共 registry

- **日期**：2026-06-08
- **状态**：已采纳
- **依据**：提交 `fix(deps): lockfile resolved URL 改回公共 registry（根治 CI/Windows npm 崩溃）`

## 背景

v1.0.0 发布后，CI 和 Windows 用户机上的 `npm install` 大面积崩溃，报
`Exit handler never called`。这个报错和真实原因毫无关系，排查绕了很久。

真实原因：开发机的 `~/.npmrc` 配了腾讯云内网镜像，`npm install` 会把 `package-lock.json` 里
每个包的 `resolved` 字段写死成 `mirrors.tencentyun.com`。那个地址**只有腾讯云内网能访问**，
CI 和用户机解析不到，npm 在超时路径上崩溃。

## 决策

`package-lock.json` 里的 `resolved` 一律指向 `registry.npmjs.org`。

开发机想用镜像加速可以，但**不能让镜像地址进 lockfile**。

## 理由

- lockfile 是要提交、要分发给所有人的，里面不能有只在某个网络里成立的地址
- 这类故障的报错信息完全误导（看起来像 npm 版本 bug 或 `--include=dev` 的问题），
  排查成本极高，必须从源头堵死

## 后果

- 安装脚本里的国内加速改成运行时切镜像，而不是把镜像烙进 lockfile
- 每次更新依赖后要检查 lockfile 里有没有混进内网地址
- 与 npm 版本、`--include=dev` 都无关 —— 曾经怀疑过这两条，是错的
