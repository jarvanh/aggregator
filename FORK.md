# Fork 同步指南

上游: https://github.com/wzdnzd/aggregator （本仓库是其 fork）

fork 相对上游的改动分两类，同步上游更新时只需关注第二类。

## 一、纯新增文件（同步零冲突，无需处理）

| 文件 | 作用 |
| --- | --- |
| `FORK.md` | 本文档 |
| `subscribe/config/full.json` | process.py 管道配置（gist id 用 `__GIST_ID__` 占位符，运行时由 workflow 从 secret 注入；注意 `subscribe/config/config.json` 在 .gitignore 中，故用此文件名） |
| `tools/prep_subs.py` | 公共节点列表预处理：下载 → 消毒 → 分块转换 → 毒行二分隔离 → base64 重打包（subconverter 该构建只接受 base64 列表，且部分公共链接会毒死整包） |

## 二、修改过的共享文件（同步冲突点，共 4 处）

### 1. `.github/workflows/collect.yaml`
- cron 改为每小时（上游为每周一次）
- Collect 命令去掉 `--skip`：推送前用 mihomo 对节点逐一测活，只推活节点

### 2. `.github/workflows/process.yaml`
整体定制：运行时生成配置（注入 gist id）、先跑 `tools/prep_subs.py`、`-n 128` 测活并发、独立并发组 `github.repository-process`（不与 Collect 互相取消）、每天 4 次（北京时间 05:05 / 10:05 / 15:05 / 20:05）

### 3. `subscribe/clash.py` — `filter_proxies()` 开头
带 `[fork-patch]` 注释：与 clash 内置策略（DIRECT/REJECT 等）或本配置分组同名的节点自动改名，否则 mihomo 拒绝加载整个配置（公共列表/Telegram 都会混进这种节点）

### 4. `subscribe/crawl.py` — `batch_crawl()` 的 token 去重
带 `[fork-patch]` 注释：`singlelink://` 聚合键豁免 token 去重。否则 Telegram 与 GitHub 的单链接聚合记录（token 均解析为空串）互相覆盖，后写入者吞掉先写入者的全部链接

## 同步操作建议

```bash
git fetch upstream && git merge upstream/main
# 冲突只会出现在上面 4 个文件；原则：保留 fork 逻辑，并入上游新逻辑
# 两个代码补丁用 grep -n "fork-patch" subscribe/clash.py subscribe/crawl.py 定位
```

订阅产物：Gist 中 `clash.yaml`/`v2ray.txt`/`singbox.json`（Collect 管道）与
`full-clash.yaml`/`full-v2ray.txt`/`full-singbox.json`（Process 管道），两套文件互不覆盖。
