#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 知识库 → 本地下载 → MinerU 转 MD → 清洗 → FastGPT 同步 一站式管线。
零第三方依赖（仅标准库 + curl + mineru-open-api）。Python 3。

子命令：
  download   从 /space/d/{nodeId} 分享链接下载全部文件到目标目录
  convert    PDF → MD（超 200 页自动按 100 页分段、串行、合并 images）
  clean      清洗 MD 中的署名/编辑/审核/广告/出版编务/CIP（保守有界）
  sync       调用 fastgpt-kb-sync 的 fgkb.py 增量同步
  all        依次执行 download → convert → clean → sync

用法示例见 SKILL.md。所有凭据走环境变量，不写入脚本/产物。
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

LIB_SKILL_CANDIDATES = [
    "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library",
    os.path.expanduser("~/.workbuddy/skills/library"),
]
FGKB_DEFAULT = os.path.expanduser("~/.workbuddy/skills/fastgpt-kb-sync/scripts/fgkb.py")
# FastGPT 凭据默认来源（本机既有配置，用户指定；不随技能包分发）
FGKB_ENV_DEFAULT = os.path.expanduser("~/.agents/skills/fastgpt-content-processor/.env")


# ---------------------------------------------------------------- 基础工具
def run(cmd, timeout=None, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)


def find_library_skill():
    if os.environ.get("LIBRARY_SKILL_DIR"):
        return os.environ["LIBRARY_SKILL_DIR"]
    for p in LIB_SKILL_CANDIDATES:
        if os.path.isfile(os.path.join(p, "space_api.py")):
            return p
    raise RuntimeError("未找到 library 技能目录，请设置 LIBRARY_SKILL_DIR")


def api(skill, name, *args):
    """调用 space_api.py，返回 data 字典；失败抛错。"""
    p = run(["python3", os.path.join(skill, "space_api.py"), name, *args])
    try:
        d = json.loads(p.stdout)
    except Exception:
        raise RuntimeError(f"{name} 返回非 JSON: {p.stdout[:200]}")
    if "error" in d:
        raise RuntimeError(f"{name} 失败: {d['error']}")
    return d.get("data", d)


def node_id_from_url(url):
    m = re.search(r"/space/d/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError("链接中找不到 /space/d/{nodeId}")
    return m.group(1)


def collect_nodes(skill, root_id):
    """递归收集文件夹下所有叶子节点。注意：space list-nodes 常因权限失败，
    改走 node-info 的 nodes[] 列表（分享场景按 node-id 可读）。"""
    out = []

    def walk(nid):
        d = api(skill, "space.workspace.node-info", "--node-id", nid)["node"]
        kids = d.get("nodes") or []
        if kids:
            for k in kids:
                walk(k)
        else:
            out.append({"id": nid, "title": d.get("title", ""), "kind": d.get("kind", "")})

    walk(root_id)
    return out


# ---------------------------------------------------------------- download
def download_one(skill, node, outdir):
    """返回 (状态, 说明)。drive 走下载链接；doc/web 走导出。"""
    nid, title, kind = node["id"], node["title"], node["kind"]
    if kind == "drive":
        p = run(["python3", os.path.join(skill, "drive", "get_download_link.py"), "--node-id", nid])
        line = next((l for l in p.stdout.splitlines() if l.startswith("KS_DRIVE_DOWNLOAD ")), "")
        if not line:
            return "FAIL", f"{title}: 取链接失败 {p.stdout[:120]}"
        info = json.loads(line.split(" ", 1)[1])
        dest = os.path.join(outdir, info["file_name"])
        r = run(["curl", "-fsSL", "--retry", "2", "-o", dest, info["download_url"]])
        if r.returncode != 0:
            return "FAIL", f"{title}: 下载失败 {r.stderr[:120]}"
        return "OK", f"{os.path.basename(dest)} ({os.path.getsize(dest)//1024//1024} MB)"

    if kind in ("doc", "web", "database"):
        k = {"doc": "doc", "web": "web", "database": "database"}[kind]
        for attempt in range(6):
            p = run(["python3", os.path.join(skill, "export", "export_node.py"),
                     "--node-id", nid, "--kind", k, "--out", outdir])
            line = next((l for l in p.stdout.splitlines() if l.startswith("KS_NODE_EXPORT ")), "")
            if line:
                d = json.loads(line.split(" ", 1)[1])
                return "OK", f"{d.get('file_name')} -> {d.get('local_path')}"
            # 50001 提交过于频繁：间隔重试
            time.sleep(8)
        return "FAIL", f"{title}: 导出失败/超限 {p.stdout[:150]}"
    return "SKIP", f"{title}: kind={kind}"


def cmd_download(args):
    skill = find_library_skill()
    outdir = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(outdir, exist_ok=True)
    root = node_id_from_url(args.url)
    nodes = collect_nodes(skill, root)
    print(f"发现 {len(nodes)} 个文件，目标目录 {outdir}", flush=True)
    ok = fail = 0
    for n in nodes:
        st, msg = download_one(skill, n, outdir)
        print(f"{st} {msg}", flush=True)
        ok += st == "OK"
        fail += st == "FAIL"
    print(f"结果：成功 {ok}，失败 {fail}", flush=True)
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------- convert
def pdf_pages(path):
    data = open(path, "rb").read()
    return len(re.findall(rb"/Type\s*/Page(?:\s|/|>)", data))


def convert_pdf(pdf, mdroot, pages_per=100, retries=4, timeout=2100, model="pipeline"):
    """PDF→MD。>200 页必须 --pages 分段；每段都要带 --pages（含 part1）；
    串行执行避免 API 批处理冲突；合并 md 与 images。"""
    name = os.path.splitext(os.path.basename(pdf))[0]
    outdir = os.path.join(mdroot, name)
    final = os.path.join(outdir, name + ".md")
    if os.path.exists(final):
        return "SKIP", name
    os.makedirs(os.path.join(outdir, "images"), exist_ok=True)
    total = pdf_pages(pdf)
    ranges = [(s, min(s + pages_per - 1, total)) for s in range(1, total + 1, pages_per)] if total > pages_per else [None]
    parts = []
    for idx, rg in enumerate(ranges, 1):
        part = os.path.join(outdir, f"_part{idx}")
        os.makedirs(part, exist_ok=True)
        mds = glob.glob(os.path.join(part, "*.md"))
        if not mds:
            cmd = ["mineru-open-api", "extract", pdf, "-o", part, "-f", "md", "--model", model]
            if rg:
                cmd += ["--pages", f"{rg[0]}-{rg[1]}"]
            for attempt in range(retries):
                try:
                    p = run(cmd, timeout=timeout)
                except subprocess.TimeoutExpired:
                    time.sleep(5)
                    continue
                mds = glob.glob(os.path.join(part, "*.md"))
                if mds:
                    break
                time.sleep(10)
        if not mds:
            shutil.rmtree(part, ignore_errors=True)
            # 彻底失败时移除整个空壳目录，避免留下只有 images 的误导性目录
            if idx == 1 and not any(os.path.exists(os.path.join(outdir, f"_part{i}")) or
                                    glob.glob(os.path.join(outdir, f"_part{i}", "*.md"))
                                    for i in range(2, len(ranges) + 1)):
                shutil.rmtree(outdir, ignore_errors=True)
            return "FAIL", f"{name}: 第 {idx} 段转换失败（MinerU 服务端可能故障，稍后重试）"
        parts.append(mds[0])
    chunks = []
    for m in parts:
        chunks.append(open(m, encoding="utf-8").read().strip())
        srcimg = os.path.join(os.path.dirname(m), "images")
        if os.path.isdir(srcimg):
            for img in os.listdir(srcimg):
                shutil.copy2(os.path.join(srcimg, img), os.path.join(outdir, "images", img))
        os.remove(m)
        shutil.rmtree(os.path.dirname(m), ignore_errors=True)
    open(final, "w", encoding="utf-8").write("\n\n".join(chunks) + "\n")
    return "OK", f"{name} ({os.path.getsize(final)//1024} KB, {len(parts)} 段)"


def cmd_convert(args):
    model = args.model
    if args.token:
        os.environ["MINERU_TOKEN"] = args.token
    src = os.path.abspath(os.path.expanduser(args.dir))
    mdroot = os.path.join(src, "MD")
    os.makedirs(mdroot, exist_ok=True)
    pdfs = sorted(glob.glob(os.path.join(src, "*.pdf")))
    print(f"发现 {len(pdfs)} 个 PDF，模型 {model}", flush=True)
    ok = fail = 0
    for pdf in pdfs:
        st, msg = convert_pdf(pdf, mdroot, args.pages, args.retries, args.timeout, model)
        print(f"{st} {msg}", flush=True)
        ok += st == "OK"
        fail += st == "FAIL"
    print(f"结果：成功 {ok}，失败 {fail}；MD 位于 {mdroot}", flush=True)
    return 0 if fail == 0 else 1


# ---------------------------------------------------------------- clean
LINE_PATTERNS = [
    r"^\s*[-*>]?\s*(作者|作者简介|撰文|文稿|编辑|责任编辑|责编|审核|审稿|校对|插画|绘图|美术|排版|封面设计|策划编辑|责任校对|责任出版|主译|副主编|名誉主编|编委|供稿|统筹|策划|监制|本条鸣谢|鸣谢|主办方|协办方|来源|公众号)[:：／/]",
    r"^\s*[-*>]?\s*(主编|主译|副主编|编委|策划编辑|封面设计|责任校对|责任出版)[^\d].{0,40}$",
    r"^\s*.{0,20}定\s*价\s*[:：]?\s*[\d.]+\s*元?\s*.{0,10}$",
    r"^\s*(出版者|发行者|印刷者|版\s*次|开\s*本|字\s*数|印\s*张|书\s*号|邮购|地\s*址|官方网址|电\s*话|传\s*真|图文|图片来源|著作权合同登记号)[:：]?",
    r"^\s*(出版人|出\s*版|印\s*刷|发\s*行|邮政编码|网\s*址|上架建议)[:：]",
    r"^\s*中国版本图书馆CIP数据核字",
    r"^\s*(整理说明|转换说明)[:：]",
    r"^\s*>\s*(整理说明|转换说明|声明|免责)",
    r"^\s*(I\.①|如发现印装问题|医患交流.*系列丛书)",
]
LINE_RE = [re.compile(p) for p in LINE_PATTERNS]
# 有界块：标题匹配后最多删 30 行，遇同级/更高级标题停止（绝不吞整书）
BLOCK_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(转录核对备注|整理说明|转换说明|免责声明|图书在版编目|CIP数据|版权页|出版说明|内容简介|丛书编委会名单|编委会名单)")
HTML_TABLE = re.compile(r"<table>.*?</table>", re.S)
PUB_KW = re.compile(r"策划编辑|责任编辑|出版者|印刷者|发行者|著作权|图书在版编目|书\s*号|定\s*价|出版社")


def clean_text(text):
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if BLOCK_HEAD.match(ln):
            i += 1
            cnt = 0
            while i < len(lines) and cnt < 30:
                if re.match(r"^#{1,6}\s", lines[i]) or (lines[i].strip() == "" and cnt > 2):
                    break
                i += 1
                cnt += 1
            continue
        if any(r.match(ln) for r in LINE_RE):
            i += 1
            continue
        out.append(ln)
        i += 1
    res = "\n".join(out)
    res = HTML_TABLE.sub(lambda m: "" if PUB_KW.search(m.group(0)) else m.group(0), res)
    return re.sub(r"\n{3,}", "\n\n", res).strip() + "\n"


def cmd_clean(args):
    src = os.path.abspath(os.path.expanduser(args.dir))
    # 安全检查：若清洗后内容骤减，拒绝写入（防止误吞正文）
    mds = [os.path.join(r, f) for r, _, fs in os.walk(src) for f in fs if f.endswith(".md")]
    changed = 0
    for p in mds:
        try:
            old = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            print(f"WARN 跳过 {os.path.relpath(p, src)}：非 UTF-8 编码", flush=True)
            continue
        new = clean_text(old)
        if new == old:
            continue
        # 大文件（书籍）严控 20%；短文档（在线文章）元信息占比天然高，放宽到 40%
        threshold = 0.8 if len(old) > 20000 else 0.6
        if len(new) < len(old) * threshold:
            print(f"WARN 跳过 {os.path.relpath(p, src)}：删除量 {len(old)-len(new)} 超过阈值，疑似误删", flush=True)
            continue
        open(p, "w", encoding="utf-8").write(new)
        changed += 1
    print(f"清洗 {changed}/{len(mds)} 个 MD", flush=True)
    return 0


# ---------------------------------------------------------------- sync
def load_fastgpt_env(env_file=None):
    """从 fastgpt-content-processor/.env 读取凭据，映射为 fgkb 的 FGKB_* 环境变量。
    返回 (env_dict, source_path)。文件不存在时返回空 dict，交由 fgkb 自身配置链处理。"""
    path = os.path.expanduser(env_file or os.environ.get("FASTGPT_ENV_FILE") or FGKB_ENV_DEFAULT)
    kv = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            kv[k.strip()] = v
    mapping = {
        "FGKB_BASE_URL": kv.get("FASTGPT_BASE_URL"),
        "FGKB_API_KEY": kv.get("FASTGPT_API_KEY"),
        "FGKB_DATASET_ID": kv.get("FASTGPT_DATASET_ID"),
    }
    return {k: v for k, v in mapping.items() if v}, path


def cmd_sync(args):
    fgkb = args.fgkb or os.environ.get("FGKB_SCRIPT", FGKB_DEFAULT)
    if not os.path.isfile(fgkb):
        raise RuntimeError(f"未找到 fgkb.py：{fgkb}")
    # 凭据：本机 .env 为底，CLI --dataset-id 覆盖目标库（fgkb 用环境变量读 dataset）
    child_env, src = load_fastgpt_env(args.env_file)
    if args.dataset_id:
        child_env["FGKB_DATASET_ID"] = args.dataset_id
    if args.source_dir:
        child_env["FGKB_SOURCE_DIR"] = os.path.abspath(os.path.expanduser(args.source_dir))
    cmd = ["python3", fgkb, "sync"]
    if args.mode:
        cmd += ["--mode", args.mode]
    if args.only:
        cmd += ["--only", args.only]
    if args.dry_run:
        cmd += ["--dry-run"]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    print(f"凭据来源：{src}（仅键名可见）", flush=True)
    if args.dataset_id:
        print(f"目标库覆盖：{args.dataset_id}", flush=True)
    print("执行：" + " ".join(cmd), flush=True)
    p = run(cmd, timeout=args.timeout, env=child_env)
    print(p.stdout, flush=True)
    if p.stderr:
        print(p.stderr, file=sys.stderr, flush=True)
    return p.returncode


# ---------------------------------------------------------------- all
def cmd_all(args):
    a = argparse.Namespace(**vars(args))
    for fn in (cmd_download, cmd_convert, cmd_clean, cmd_sync):
        rc = fn(a)
        if rc:
            print(f"{fn.__name__} 返回 {rc}，中止后续步骤", flush=True)
            return rc
    return 0


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 知识库 → 下载 → MinerU 转 MD → 清洗 → FastGPT 同步")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download")
    d.add_argument("--url", required=True, help="/space/d/{nodeId} 分享链接")
    d.add_argument("--out", required=True, help="目标目录（如 ~/Downloads/小巢宝子）")
    d.set_defaults(fn=cmd_download)

    c = sub.add_parser("convert")
    c.add_argument("--dir", required=True)
    c.add_argument("--model", default="pipeline", choices=["pipeline", "vlm"])
    c.add_argument("--pages", type=int, default=100, help="每段页数（>200 页必分段；建议 100）")
    c.add_argument("--retries", type=int, default=4)
    c.add_argument("--timeout", type=int, default=2100)
    c.add_argument("--token", default=None, help="MINERU_TOKEN；不填则读环境变量")
    c.set_defaults(fn=cmd_convert)

    cl = sub.add_parser("clean")
    cl.add_argument("--dir", required=True)
    cl.set_defaults(fn=cmd_clean)

    s = sub.add_parser("sync")
    s.add_argument("--fgkb", default=None)
    s.add_argument("--mode", default="changed")
    s.add_argument("--dataset-id", default=None, help="覆盖目标知识库 ID（不传则用凭据文件中的）")
    s.add_argument("--source-dir", default=None, help="待上传文件根目录（默认取 fgkb 配置）")
    s.add_argument("--only", default=None, help="glob 过滤，如 '**/*.md' 只上传 md（避免混入 PDF）")
    s.add_argument("--env-file", default=None, help="FastGPT 凭据 .env 路径")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--timeout", type=int, default=3600)
    s.set_defaults(fn=cmd_sync)

    allp = sub.add_parser("all")
    for k, v in [("url", None), ("out", None), ("dir", None), ("model", "pipeline"),
                 ("pages", 100), ("retries", 4), ("timeout", 3600), ("token", None),
                 ("fgkb", None), ("mode", "changed"), ("dataset-id", None),
                 ("source-dir", None), ("only", None), ("env-file", None)]:
        allp.add_argument("--" + k, default=v)
    allp.add_argument("--dry-run", action="store_true")
    allp.set_defaults(fn=cmd_all)

    args = ap.parse_args()
    try:
        sys.exit(args.fn(args) or 0)
    except Exception as e:
        print(f"ERROR {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
