# wbkb-ingest-to-fastgpt

一条龙 Agent 技能：把 **WorkBuddy 知识库分享链接**里的全部文件下载到本地 → 用 **MinerU** 把 PDF 转成 Markdown → 清洗署名/编辑/审核/广告/出版编务等无关信息 → 增量同步到 **FastGPT 知识库**。

零第三方 Python 依赖（标准库 + `curl` + `mineru-open-api` CLI）。可单独跑某一步，也可一键全流程。

## 安装

```bash
git clone https://github.com/opencare-skillhub/wbkb-ingest-to-fastgpt.git
cp -R wbkb-ingest-to-fastgpt ~/.workbuddy/skills/
```

仓库根目录即技能目录，`SKILL.md` 含触发词，可直接被 Agent 调用。

## 依赖

| 依赖 | 用途 | 准备 |
|---|---|---|
| WorkBuddy `library` 技能 | 读分享节点、下载 drive、导出在线文档 | 内置，脚本自动定位（可用 `LIBRARY_SKILL_DIR` 覆盖） |
| `mineru-open-api` CLI | PDF → MD | `npm i -g mineru-open-api` |
| MinerU token | `extract` 模式必需 | 环境变量 `MINERU_TOKEN` 或 `--token` |
| `fastgpt-kb-sync` 技能 | FastGPT 增量同步（三层去重） | 安装到 `~/.workbuddy/skills/fastgpt-kb-sync/` |

## 凭据

FastGPT 凭据**不随本技能分发**。默认从本机既有文件读取：

```
~/.agents/skills/fastgpt-content-processor/.env
```

该文件需含：

```ini
FASTGPT_BASE_URL=https://<your-instance>/api
FASTGPT_API_KEY=<your-key>
FASTGPT_DATASET_ID=<default-dataset-id>
```

可用 `--env-file` 或环境变量 `FASTGPT_ENV_FILE` 换路径；用 `--dataset-id` 覆盖目标知识库。脚本只读取键名对应的值，不打印 Key。

## 用法

```bash
S=~/.workbuddy/skills/wbkb-ingest-to-fastgpt/scripts/wbkb_ingest.py

# 1) 下载分享文件夹全部文件
python3 "$S" download --url "https://www.workbuddy.cn/space/d/<nodeId>?share_code=<code>" --out ~/Downloads/mykb

# 2) PDF 转 MD（>200 页自动按 100 页分段、串行、合并 images）
export MINERU_TOKEN="<token>"
python3 "$S" convert --dir ~/Downloads/mykb --model pipeline --pages 100

# 3) 清洗无关信息（有界删除 + 误删护栏）
python3 "$S" clean --dir ~/Downloads/mykb

# 4) 同步到 FastGPT（先 dry-run）
python3 "$S" sync --source-dir ~/Downloads/mykb --only '**/*.md' --dataset-id <目标库ID> --dry-run
python3 "$S" sync --source-dir ~/Downloads/mykb --only '**/*.md' --dataset-id <目标库ID>

# 一键全流程
python3 "$S" all --url "<分享链接>" --out ~/Downloads/mykb --token "<token>" \
  --source-dir ~/Downloads/mykb --only '**/*.md' --dataset-id <目标库ID>
```

细节与踩坑见 `SKILL.md` 和 `references/pipeline-details.md`。

## 安全边界

- 凭据只从本机 `.env` 读取，仓库只含 `.env.example` 占位符。
- 不做医院/医生排名，不替代临床判断；清洗只去除编务与推广信息，不改医学正文。
- 处理前建议备份目标目录；清洗有删除量阈值告警，误删可回滚。

## License

MIT — 见 `LICENSE`。
