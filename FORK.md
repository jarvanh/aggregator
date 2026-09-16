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

## 同步操作

无需手动操作：`.github/workflows/pull.yaml` 每天自动把上游 `main` 合并进来，
再用 `tools/resolve_conflicts.py` 重放下面的 fork 补丁，然后 push。
也可以在 Actions 里手动 `workflow_dispatch` 触发。

本地手动同步等价命令：

```bash
git fetch upstream && git merge upstream/main     # 冲突只会出现在下面的文件
python3 tools/resolve_conflicts.py .              # 自动重放 fork 补丁
# 该脚本会断言所有补丁都已生效，失败即退出，不会静默丢补丁
git commit && git push
```

`tools/resolve_conflicts.py` 是补丁的唯一事实来源：合并时冲突解决和补丁重放都由它完成，
新增 fork 改动时应在里面同步登记，否则下次同步会被上游覆盖。

## 同步冲突点

上游把节点校验逻辑拆到了 `subscribe/outbound/` 包，本 fork 已跟随该结构，
因此 `subscribe/clash.py` 只保留一处 fork 补丁。

### 1. `.github/workflows/collect.yaml`
- cron 改为每小时（上游为每周一次）
- Collect 命令去掉 `--skip`：推送前用 mihomo 对节点逐一测活，只推活节点

### 2. `.github/workflows/process.yaml`
整体定制（冲突时整份保留 fork 版本）：运行时生成配置（注入 gist id）、先跑
`tools/prep_subs.py`、`-n 128` 测活并发、独立并发组 `github.repository-process`
（不与 Collect 互相取消）、每天 4 次（北京时间 05:05 / 10:05 / 15:05 / 20:05）

### 3. `subscribe/clash.py` — `filter_proxies()` 开头
带 `[fork-patch]` 注释：与 clash 内置策略（DIRECT/REJECT 等）或本配置分组同名的节点自动改名，
否则 mihomo 拒绝加载整个配置（公共列表/Telegram 都会混进这种节点）

### 4. `subscribe/crawl.py` — `batch_crawl()` 的 token 去重
带 `[fork-patch]` 注释：`singlelink://` 聚合键豁免 token 去重。否则 Telegram 与 GitHub
的单链接聚合记录（token 均解析为空串）互相覆盖，后写入者吞掉先写入者的全部链接

定位补丁：`grep -rn "fork-patch" subscribe/`

## 兼容性说明

现在 `subscribe/` 下的业务代码与上游逐行一致（仅上述 4 处补丁），
`tools/resolve_conflicts.py` 负责在每次合并时把补丁重新贴回去并断言成功。

订阅产物：Gist 中 `clash.yaml`/`v2ray.txt`/`singbox.json`（Collect 管道）与
`full-clash.yaml`/`full-v2ray.txt`/`full-singbox.json`（Process 管道），两套文件互不覆盖。
