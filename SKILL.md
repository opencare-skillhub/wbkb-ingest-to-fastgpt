---
name: wbkb-ingest-to-fastgpt
description: 把 WorkBuddy 知识库（资料库）分享链接里的全部文件下载到本地，PDF 用 MinerU 转成 Markdown，清洗掉署名/编辑/审核/广告/出版编务等无关信息，再增量同步到 FastGPT 知识库。当用户给出 /space/d/{id} 分享链接要求「全部下载 + PDF 转 md + 去掉广告审核信息 + 同步 FastGPT/灌知识库」时使用。触发词：知识库下载、分享链接批量下载、PDF批量转md、去掉编辑审核信息、同步FastGPT、灌知识库、wbkb ingest。
agent_created: true
---

# WorkBuddy 知识库 → 下载 → MinerU → 清洗 → FastGPT 同步

## 何时用本技能
一条龙处理「WorkBuddy 资料库分享文件夹」：下载全部文件 → PDF 转 MD → 清洗无关信息 → 灌入 FastGPT 知识库。也支持单独跑某一步。

## 前置依赖
| 依赖 | 用途 | 检查 |
|---|---|---|
| WorkBuddy `library` 技能 | 按 node-id 读节点、下载 drive、导出 doc | 内置，脚本自动定位 |
| `mineru-open-api` CLI | PDF→MD | `mineru-open-api version`；无则 `npm i -g mineru-open-api` |
| MinerU token | `extract` 模式必需（flash 免 token 但限 10MB/20 页） | `export MINERU_TOKEN=...` 或 `--token` |
| `fastgpt-kb-sync` 技能 | FastGPT 增量同步（三层去重） | `~/.workbuddy/skills/fastgpt-kb-sync/scripts/fgkb.py` |

调用脚本：`python3 ~/.workbuddy/skills/wbkb-ingest-to-fastgpt/scripts/wbkb_ingest.py <子命令> ...`

## FastGPT 凭据（重要）
**默认来源：`~/.agents/skills/fastgpt-content-processor/.env`**（本机既有配置，不随技能包分发）。
脚本读取 `FASTGPT_BASE_URL` / `FASTGPT_API_KEY` / `FASTGPT_DATASET_ID`，映射成 fgkb 需要的 `FGKB_*` 环境变量再调用同步，无需手工 export。

- 换凭据文件：`--env-file /path/to/.env` 或环境变量 `FASTGPT_ENV_FILE`。
- 换目标知识库：`--dataset-id <ID>`（覆盖凭据文件里的默认库）。
- 换扫描目录：`--source-dir <路径>`（覆盖 fgkb 默认 `./output`）。
- 只允许上传 md：`--only '**/*.md'`（推荐，避免混入原始 PDF）。
- 该 `.env` 不属于本技能，更新凭据请改那一处；不要把真实 Key 写进本技能任何文件。

## 四步流程

### 1. download — 下载全部文件
```bash
python3 .../wbkb_ingest.py download --url "https://www.workbuddy.cn/space/d/xxxx?share_code=..." --out ~/Downloads/小巢宝子
```
- 从链接解析 nodeId，递归读 `node-info.nodes[]`（**不要用 `list-nodes`**：分享场景常报 `permission denied`，按 node-id 反而可读）。
- `kind=drive` → `get_download_link.py` + curl；`kind=doc/web/database` → `export/export_node.py` 导出 md。
- 导出有频率限制（50001 提交过于频繁）：**间隔 8 秒重试，最多 6 次**；不可并发导出。
- 下载可并发（4 线程）；导出必须串行。

### 2. convert — PDF 转 MD
```bash
export MINERU_TOKEN="sk-..."
python3 .../wbkb_ingest.py convert --dir ~/Downloads/小巢宝子 --model pipeline --pages 100
```
- 用 **`extract` + token**（`flash-extract` 仅适合 <10MB 且 <20 页）。默认 `pipeline` 模型（零幻觉）；复杂版式可 `--model vlm`（可能幻觉）。
- **>200 页必须分段**：`extract` 单次硬上限 200 页，超限报 `number of pages exceeds limit`。
- **每段（含第 1 段）都要带 `--pages`**：漏给第 1 段会仍按整本解析而失败——这是最容易踩的坑。
- 建议每段 100 页（`--pages`）；**串行执行**，并发会撞 API 批处理冲突（`timeout waiting for batch ...`）。
- 每段单独输出目录，合并 md 并把各段 `images/` 拷到统一目录，MD 落在 `<dir>/MD/<文件名>/<文件名>.md`。
- 单段 100 页给 2100s 超时、4 次重试；API 偶发空输出超时，重试即可。
- MinerU 服务端故障时（`timeout waiting for batch` 连 10 页小任务都超时），不要反复等；稍后重跑本命令，已成功文件自动 SKIP（断点续跑）。

### 3. clean — 清洗无关信息
```bash
python3 .../wbkb_ingest.py clean --dir ~/Downloads/小巢宝子
```
删除内容：作者/编辑/审核/校对/插画/来源/公众号/署名、鸣谢/主办方、定价、出版编务（出版者/版次/书号/印张）、CIP 数据块、整理说明/转录核对备注、带出版信息的 HTML 表格。
**安全设计**：
- 有界块删除（标题匹配后最多 30 行，遇标题停）——绝不会像无界版本那样吞掉整本书。
- 单文件删除量过大自动跳过并告警：>20000 字符的文件阈值 20%，短文档 40%。
- 清洗前脚本已自带保守规则；仍建议对 <100 行的在线文档抽查头尾。

### 4. sync — 同步 FastGPT
```bash
python3 .../wbkb_ingest.py sync --source-dir ~/Downloads/小巢宝子 --only '**/*.md' --dataset-id <目标库ID> --dry-run
python3 .../wbkb_ingest.py sync --source-dir ~/Downloads/小巢宝子 --only '**/*.md' --dataset-id <目标库ID>
```
复用 `fastgpt-kb-sync` 的 `fgkb.py`：三层去重（同名闸门 + 内容 md5 + 本地状态表）、增量写入。
- `--mode` 默认 `changed`；全量重扫用 `all`，指定日期用 `since`。
- 同名闸门：目标库已有同名文件会跳过；确需重灌加 `--allow-duplicate`（fgkb 原生参数，需直接调 fgkb.py）。
- 单文件网络超时失败属偶发，重跑即可（成功项会被状态表跳过）。

### 一键全流程
```bash
python3 .../wbkb_ingest.py all --url "..." --out ~/Downloads/小巢宝子 --token "sk-..." --source-dir ~/Downloads/小巢宝子 --only '**/*.md' --dataset-id <目标库ID>
```

## 关键坑速查
| 坑 | 处理 |
|---|---|
| `list-nodes` 报 permission denied | 改用 `node-info` 的 `nodes[]` 递归 |
| 导出报 50001 提交频繁 | 间隔 8s 重试，串行导出 |
| `extract` 报超 200 页 | 按 100 页 `--pages` 分段 |
| 分段只有第 1 段失败 | 第 1 段漏了 `--pages`，补上 |
| 分段报 `timeout waiting for batch` | 并发冲突，改串行；服务端故障时稍后重跑 |
| 清洗后正文消失 | 块删除无界，改用本脚本的有界版本 |
| PDF 转 md 图片丢失 | 各段 `images/` 必须合并到统一目录 |
| `fgkb` 报缺 API Key | 检查 `~/.agents/skills/fastgpt-content-processor/.env` 是否存在且含 `FASTGPT_API_KEY` |
| 同步混入 PDF | 加 `--only '**/*.md'` |
| `--dataset-id` 没生效 | 本脚本经 `FGKB_DATASET_ID` 环境变量传给 fgkb，不要直接给 fgkb 传该参数（它不认） |

## 文件结构
- `scripts/wbkb_ingest.py` — 一站式 CLI（download/convert/clean/sync/all）
- `references/pipeline-details.md` — 各步骤命令细节与错误处理码

## 维护
改脚本后跑 `python3 -m py_compile scripts/wbkb_ingest.py` 确认语法。
凭据只从 `~/.agents/skills/fastgpt-content-processor/.env` 读取，绝不写入本技能任何文件；公开仓库只含 `.env.example` 占位符。
