# Pipeline 细节与错误处理

## 实证来源
2026-11-24 处理一个 WorkBuddy 知识库分享文件夹（24 个节点：11 个 PDF 源文件 18–121MB + 13 个在线文档）。全流程跑通后沉淀本技能。

## 各步真实返回

### node-info（分享场景唯一可靠的枚举方式）
```json
{"data":{"node":{"id":"...","kind":"folder","nodes":["...24 ids..."],
"spaceId":"...","url":"https://www.workbuddy.cn/space/d/..."}}}
```
`list-nodes --space-id` 报 `code=12607 permission denied: role=invalid`——分享链接没有 space 读权限，但按 node-id 可读。

### drive 下载
`drive/get_download_link.py --node-id <id>` → `KS_DRIVE_DOWNLOAD {file_name, ext, download_url}`。链接短期有效，取到即 curl 下载。

### doc 导出
`export/export_node.py --node-id <id> --kind doc --out <dir>` → `KS_NODE_EXPORT {file_name, file_type, local_path}`。
- 错误 `50001 提交过于频繁`：间隔 8s 重试（本次 13 个里有 3 个需重试）。
- 同一用户导出任务串行；并发会撞 `19901 已有导出任务进行中`。

### mineru extract
`mineru-open-api extract <pdf> -o <dir> -f md --model pipeline [--pages S-E]`
- 单文件 ≤200MB/600页，但 pipeline 实限 200 页；超页报 `number of pages exceeds limit (200 pages)`。
- 中文扫描书 145 页用 pipeline 约 1–2 分钟；错误重试要注意 API 偶发 `timeout waiting for batch <uuid>`（并发或后端抖动）。
- `--pages 1-200` 必须显式给；否则 CLI 忽略分段直接整本解析。
- 多段必须串行：并发 2 段时疑似共享 batch 队列冲突，成功率显著下降。

### 清洗红线
无界块删除会吞掉整本书（实测两个 250KB 的 md 被删到 286 bytes）。规则：
1. 块删除必须有界（≤30 行 + 遇标题停止）。
2. 删除量 >20% 跳过并告警。
3. 有 `### 转录核对备注（非原文）` 这类标题块，需整块删。
4. 无冒号署名（如插画/编辑后直接跟人名）用宽松前缀匹配。
5. HTML `<table>` 里混出版编务（策划编辑/出版者/定价）整表删除。

## 状态记录
- 原始备份：处理前先 `cp -a <dir> /tmp/<name>_backup_raw`，清洗误删可回滚。
- 图片：分段转换后 images 分散在各 part 目录，合并时拷到统一 `images/`，md 里引用 `images/xxx.jpg` 才不失效。
