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

## 给 AI 的部署提示词

把下面整段发给 AI（WorkBuddy / Codex / Claude 等），它会完成安装并自行核对凭据：

```text
请帮我部署 wbkb-ingest-to-fastgpt 技能。

1. 克隆仓库到技能目录：
   git clone https://github.com/opencare-skillhub/wbkb-ingest-to-fastgpt.git ~/.workbuddy/skills/wbkb-ingest-to-fastgpt
   （若目录已存在，先备份原有 .env / 自定义配置，不要覆盖。）
   确认 SKILL.md / scripts/wbkb_ingest.py / references/ 均在位，跑一次 py_compile 确认脚本语法。

2. 检查并安装依赖：
   - mineru-open-api CLI（没有则 npm i -g mineru-open-api），确认 mineru-open-api version 可用。
   - fastgpt-kb-sync 技能：~/.workbuddy/skills/fastgpt-kb-sync/scripts/fgkb.py 存在。
   - WorkBuddy library 技能内置，无需安装。

3. 检查并匹配凭据 —— 这一步务必逐项核对，不要跳过：
   - 默认凭据文件必须是 ~/.agents/skills/fastgpt-content-processor/.env。
     先确认它存在；若不存在，询问我路径，或用 --env-file / 环境变量 FASTGPT_ENV_FILE 指定。
   - 读取并核对三个键：FASTGPT_BASE_URL、FASTGPT_API_KEY、FASTGPT_DATASET_ID。
     缺任何一个就停下告诉我，不要猜、不要写死到仓库里。
   - 注意本机可能存在多份 FastGPT 凭据（如 ~/.zshrc 里的 FASTGPT_APIKEY、~/.env 里的 FGKB_*），
     它们可能不同。本技能以 fastgpt-content-processor/.env 为准；如发现其它来源与它不一致，提示我确认。
   - 用该 Key 调 fgkb.py ls-datasets 验证可用，并确认目标知识库 ID 出现在可见列表中。
     绝不打印或回显完整 API Key。
   - 目标库 ID：若我未指定，用凭据文件里的 FASTGPT_DATASET_ID；若我指定了，用 --dataset-id 覆盖。

4. 验证部署（只跑 dry-run，不写库）：
   python3 ~/.workbuddy/skills/wbkb-ingest-to-fastgpt/scripts/wbkb_ingest.py sync \
     --source-dir <待上传目录> --only '**/*.md' --dataset-id <目标库ID> --dry-run
   确认它能扫描到文件、且无「缺少配置 / 未授权」报错后，部署才算完成。

完成后简要汇报：仓库与版本、依赖检查结果、凭据来源与匹配情况（Key 只显示前后几位）、
dry-run 结果。有任何不一致先停下问我，不要自行更换凭据或更换目标库。
```

## 安全边界

- 凭据只从本机 `.env` 读取，仓库只含 `.env.example` 占位符。
- 不做医院/医生排名，不替代临床判断；清洗只去除编务与推广信息，不改医学正文。
- 处理前建议备份目标目录；清洗有删除量阈值告警，误删可回滚。

## License

MIT — 见 `LICENSE`。
