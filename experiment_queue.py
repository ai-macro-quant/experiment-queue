#!/usr/bin/env python3
"""Local-first experiment queue backed by GitHub Contents API and Releases."""
from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "runs", "sweeps", "cross_validation", "experiments", "reports", "cache", "caches"}
EXCLUDED_SUFFIXES = {".parquet", ".feather", ".arrow", ".pkl", ".pickle", ".npy", ".npz", ".pt", ".pth", ".ckpt", ".h5", ".hdf5", ".db", ".sqlite", ".zip", ".tar", ".gz"}
SECRET = re.compile(r"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|secret[_-]?key|password)\s*[:=]\s*['\"]?[A-Za-z0-9/+_=-]{12,})")
# Match quoted literal credentials in JSON/Python as well as common token formats.
LITERAL_SECRET = re.compile(r"""(?ix)(?:["'](?:password|passwd|pwd|api[_-]?key|access[_-]?token|secret[_-]?key)["']\s*:\s*["'][^"'\r\n]+["']|(?:password|passwd|pwd)\s*=\s*["'][^"'\r\n]+["'])""")
CHUNK = 1800 * 1024 * 1024

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def file_sha_and_secret(path: Path) -> str:
    h = hashlib.sha256(); tail = ""; textual = True
    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block: break
            h.update(block)
            if textual:
                if b"\0" in block: textual = False
                else:
                    sample = tail + block.decode("utf-8", "replace")
                    if SECRET.search(sample) or LITERAL_SECRET.search(sample): fail(f"文件疑似包含凭据，未打包/上传: {path.name}")
                    tail = sample[-512:]
    return h.hexdigest()

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""): h.update(block)
    return h.hexdigest()

def fail(msg: str) -> None:
    raise RuntimeError(msg)

def valid_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value):
        fail("task-id 仅允许字母、数字、点、下划线和连字符")
    return value

def safe_rel(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        fail(f"不安全的归档路径: {name!r}")
    if any(ord(ch) < 32 or ch in '<>:"|?*' for ch in name): fail(f"Windows 非法归档路径: {name!r}")
    p = PurePosixPath(name)
    if any(part in ("", ".", "..") or ":" in part or part.endswith((".", " ")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part) for part in p.parts):
        fail(f"不安全的归档路径: {name!r}")
    for part in p.parts:
        if part.endswith((".", " ")) or re.match(r"(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", part):
            fail(f"Windows 非法归档路径: {name!r}")
    return p.as_posix()

class GhError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message); self.status = status

def gh(*args: str, input_data: bytes | None = None) -> bytes:
    p = subprocess.run(["gh", *args], input=input_data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        # gh diagnostics may contain response fragments; avoid echoing them.
        match = re.search(rb"(?:HTTP|status|code)[^0-9]{0,8}(\d{3})", p.stderr, re.I)
        status = int(match.group(1)) if match else None
        raise GhError(f"gh 命令失败 (exit {p.returncode}): {' '.join(args[:3])}", status)
    return p.stdout

class GitHub:
    def __init__(self, repo: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo): fail("repo 格式应为 owner/name")
        self.repo = repo
    def api(self, endpoint: str, method: str = "GET", body: Any = None) -> Any:
        route = f"repos/{self.repo}" + (f"/{endpoint}" if endpoint else "")
        args = ["api", route]
        if method != "GET": args += ["--method", method]
        payload = None
        if body is not None:
            args += ["--input", "-"]
            payload = canonical(body)
        raw = gh(*args, input_data=payload)
        try: return json.loads(raw)
        except (ValueError, TypeError): return raw
    def get_file(self, path: str) -> tuple[dict, str] | None:
        try: obj = self.api("contents/" + path)
        except GhError as e:
            if e.status == 404: return None
            raise
        if not isinstance(obj, dict) or obj.get("encoding") != "base64": return None
        return obj, base64.b64decode(obj["content"]).decode("utf-8")
    def put_file(self, path: str, content: str, message: str, sha0: str | None = None) -> dict:
        body = {"message": message, "content": base64.b64encode(content.encode()).decode()}
        if sha0: body["sha"] = sha0
        return self.api("contents/" + path, "PUT", body)
    def ref_exists(self, ref: str) -> bool:
        try: self.api("git/ref/" + ref)
        except GhError as e:
            if e.status == 404: return False
            raise
        return True
    def list_dir(self, path: str) -> list[dict]:
        obj = self.api("contents/" + path)
        return obj if isinstance(obj, list) else []

def project_files(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part.startswith(".env") for part in rel.parts): continue
        if any(part in EXCLUDED_DIRS for part in rel.parts): continue
        if p.is_symlink() or not p.is_file(): continue
        if p.suffix.lower() in EXCLUDED_SUFFIXES: continue
        try: file_sha_and_secret(p)
        except OSError: continue
        out.append((rel.as_posix(), p))
    if not out: fail("项目中没有可打包的文本/源码文件")
    return sorted(out)

def write_package(root: Path, out: Path) -> dict:
    files = project_files(root)
    manifest = {"format": 1, "files": [{"path": rel, "sha256": sha(p.read_bytes()), "size": p.stat().st_size} for rel, p in files]}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel, p in files: z.write(p, rel)
        z.writestr("QUEUE_PACKAGE_MANIFEST.json", canonical(manifest))
    return manifest

def safe_extract(zip_path: Path, destination: Path, expected_sha: str | None = None) -> dict:
    if expected_sha and hash_file(zip_path) != expected_sha: fail("任务包 SHA256 不匹配")
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()): fail("目标目录必须不存在或为空")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        infos = z.infolist()
        names = [safe_rel(i.filename) for i in infos]
        if len(names) != len(set(names)): fail("归档中有重复路径")
        for info, name in zip(infos, names):
            mode = info.external_attr >> 16
            if (mode & 0o170000) == 0o120000: fail("归档中不允许符号链接")
            target = (destination / name).resolve()
            if destination != target and destination not in target.parents: fail("归档路径越界")
        z.extractall(destination)
    mp = destination / "QUEUE_PACKAGE_MANIFEST.json"
    if not mp.exists(): fail("任务包缺少文件清单")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    for item in manifest.get("files", []):
        rel = safe_rel(item["path"]); p = destination / rel
        if not p.is_file() or hash_file(p) != item["sha256"]: fail(f"任务文件校验失败: {rel}")
    return manifest

def submit(a: argparse.Namespace) -> None:
    task_id = valid_id(a.task_id); command = json.loads(a.command_json)
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command): fail("command-json 必须是非空 argv 字符串数组")
    if not all(any("{" + k + "}" in x for x in command) for k in ("python", "env_file", "output_dir")):
        fail("command-json 必须包含 {python}、{env_file} 和 {output_dir} 占位符")
    required = json.loads(a.required_json or "[]")
    if not isinstance(required, list) or not all(isinstance(x, str) and x for x in required): fail("required-json 必须是字符串数组")
    root = Path(a.project); tmp = Path(tempfile.mkdtemp(prefix="queue-task-")); archive = tmp / "source.zip"
    try:
        ghc = GitHub(a.repo)
        if ghc.get_file(f"tasks/{task_id}.json"): fail("task-id 已存在；任务不可覆盖")
        manifest = write_package(root, archive); package_bytes = archive.read_bytes(); package_sha = sha(package_bytes)
        gh("release", "create", f"task-{task_id}", str(archive), "--repo", a.repo, "--title", f"Task {task_id}", "--notes", "Immutable experiment source package")
        task = {"id": task_id, "title": a.title, "project": str(root.resolve()), "command": command, "required_patterns": required, "package_sha256": package_sha, "package_bytes": len(package_bytes), "source_manifest": manifest, "created_at": now(), "release_tag": f"task-{task_id}"}
        state = {"task_id": task_id, "status": "queued", "updated_at": now()}
        ghc.put_file(f"tasks/{task_id}.json", json.dumps(task, ensure_ascii=False, indent=2), f"Queue task {task_id}")
        ghc.put_file(f"states/{task_id}.json", json.dumps(state, ensure_ascii=False, indent=2), f"Queue state {task_id}")
        refresh_indexes(ghc, ("tasks",))
        print(f"已提交 {task_id}，包 SHA256 {package_sha}")
    finally: shutil.rmtree(tmp, ignore_errors=True)

def result_sort_time(task_id: str, task: dict, state: dict) -> datetime:
    created_at = task.get("created_at")
    if created_at:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))

    # Manually imported experiments use Beijing time in the task ID.
    match = re.search(r"-(\d{8})(?:-(\d{6}))?$", task_id)
    if match:
        try:
            return datetime.strptime(match[1] + (match[2] or "000000"), "%Y%m%d%H%M%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
        except ValueError:
            pass

    updated_at = state.get("updated_at")
    if updated_at:
        return datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    return datetime.max.replace(tzinfo=timezone.utc)

def index_text(ghc: GitHub, directory: str, title: str, exclude_readme: bool = False) -> str:
    items = ghc.list_dir(directory); rows = [f"# {title}", "", "[返回实验中心](../README.md)", ""]
    if directory == "tasks":
        rows += ["| 任务 | 状态 | 实验名称 | 完整代码包 |", "|---|---|---|---|"]
        states = {}
        for x in ghc.list_dir("states"):
            if x.get("name", "").endswith(".json"):
                got = ghc.get_file("states/" + x["name"])
                if got: states[x["name"][:-5]] = json.loads(got[1]).get("status", "unknown")
        for x in sorted(items, key=lambda y: y.get("name", "")):
            name = x.get("name", "")
            if name.endswith(".json"):
                task = json.loads(ghc.get_file(directory + "/" + name)[1]); tid = name[:-5]
                rows.append(f"| [{tid}]({name}) | {states.get(tid, 'unknown')} | {task.get('title', '')} | [task-{tid}](https://github.com/{ghc.repo}/releases/tag/task-{tid}) |")
        return "\n".join(rows) + "\n"
    if directory == "results":
        rows += ["| 序号 | 任务 | 状态 | 完整训练结果 |", "|---:|---|---|---|"]
        preview_dirs = {item["name"] for item in items if item.get("type") == "dir"}
        results = []
        for item in items:
            name = item.get("name", "")
            if name.endswith(".json"):
                tid = name[:-5]
                result = json.loads(ghc.get_file(f"results/{name}")[1])
                st = ghc.get_file("states/" + name)
                state = json.loads(st[1]) if st else {}
                status = state.get("status", "unknown")
                task_file = ghc.get_file(f"tasks/{name}")
                if not task_file and status == "failed":
                    task_file = ghc.get_file(f"archive/failed/tasks/{name}")
                task = json.loads(task_file[1]) if task_file else {}
                preview = f"{tid}/README.md"
                link = preview if tid in preview_dirs and ghc.get_file(f"results/{preview}") else name
                tag = result.get("tag", f"result-{tid}")
                results.append((result_sort_time(tid, task, state), tid, status, link, tag))
        for number, (_, tid, status, link, tag) in enumerate(sorted(results), start=1):
            rows.append(f"| {number} | [{tid}]({link}) | {status} | [{tag}](https://github.com/{ghc.repo}/releases/tag/{tag}) |")
        return "\n".join(rows) + "\n"
    for x in sorted(items, key=lambda y: y.get("name", "")):
        name = x.get("name", "")
        if name.endswith(".json"): rows.append(f"- [{name}]({name})")
    return "\n".join(rows) + "\n"

def update_state(g: GitHub, task_id: str, state: dict, message: str) -> None:
    old = g.get_file(f"states/{task_id}.json")
    if not old: fail("远端状态文件不存在")
    obj, _ = old
    g.put_file(f"states/{task_id}.json", json.dumps(state, ensure_ascii=False, indent=2), message, obj["sha"])

def refresh_indexes(g: GitHub, folders: tuple[str, ...] = ("tasks", "results")) -> None:
    for folder in folders:
        title = "实验任务列表" if folder == "tasks" else "实验结果列表"
        text = index_text(g, folder, title)
        old = g.get_file(folder + "/README.md")
        g.put_file(folder + "/README.md", text, f"Refresh {folder} index", old[0]["sha"] if old else None)

def task_manifest(g: GitHub, task_id: str) -> dict:
    got = g.get_file(f"tasks/{task_id}.json")
    if not got: fail("找不到任务 manifest")
    return json.loads(got[1])

def claim(a: argparse.Namespace) -> None:
    task_id = valid_id(a.task_id); g = GitHub(a.repo)
    task_manifest(g, task_id)
    initial = g.get_file(f"states/{task_id}.json")
    if not initial or json.loads(initial[1]).get("status") != "queued": fail("任务状态不是 queued，不能领取")
    ref = f"heads/claim/{task_id}"
    if g.ref_exists(ref):
        print("任务已有 claim；不得自动接管。请检查 claim owner 和远端状态后人工处理。", file=sys.stderr); raise SystemExit(2)
    default_branch = g.api("", "GET").get("default_branch", "main")
    head = g.api("git/ref/heads/" + default_branch)
    tree = g.api("git/commits/" + head["object"]["sha"])["tree"]
    parent = head["object"]["sha"]
    token = secrets.token_urlsafe(32)
    rec = {"task_id": task_id, "worker_id": a.worker, "claim_token": token, "claimed_at": now()}
    commit = g.api("git/commits", "POST", {"message": json.dumps(rec), "tree": tree["sha"], "parents": [parent]})
    g.api("git/refs", "POST", {"ref": "refs/" + ref, "sha": commit["sha"]})
    st = {"task_id": task_id, "status": "running", "worker": a.worker, "claimed_at": rec["claimed_at"], "claim_commit": commit["sha"], "updated_at": now()}
    update_state(g, task_id, st, f"Claim {task_id}")
    refresh_indexes(g, ("tasks",))
    print(json.dumps({"task_id": task_id, "worker_id": a.worker, "claim_token": token}))

def claim_record(g: GitHub, task_id: str) -> dict | None:
    if not g.ref_exists(f"heads/claim/{task_id}"): return None
    c = g.api(f"git/commits/{g.api('git/ref/heads/claim/' + task_id)['object']['sha']}")
    try: return json.loads(c["message"].splitlines()[0])
    except Exception: return None

def split_file(path: Path) -> list[Path]:
    if path.stat().st_size <= CHUNK: return [path]
    out = []
    with path.open("rb") as source:
        remaining_total = path.stat().st_size
        while remaining_total:
            part = path.with_name(f"{path.name}.part{len(out)+1:04d}")
            remaining_part = min(CHUNK, remaining_total)
            with part.open("wb") as dest:
                while remaining_part:
                    block = source.read(min(1024 * 1024, remaining_part))
                    if not block: fail("结果包在分片时发生变化")
                    dest.write(block); remaining_part -= len(block); remaining_total -= len(block)
            out.append(part)
    return out

def upload_results(a: argparse.Namespace) -> None:
    task_id = valid_id(a.task_id); g = GitHub(a.repo); task = task_manifest(g, task_id)
    claimed = claim_record(g, task_id)
    if not claimed or claimed.get("worker_id") != a.worker or not secrets.compare_digest(claimed.get("claim_token", ""), a.claim_token): fail("claim owner/token 不匹配")
    root = Path(a.results).resolve()
    if not root.is_dir(): fail("results 必须是输出目录")
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink(): fail(f"结果目录不允许符号链接: {p}")
        if not p.is_file(): continue
        if any(x.startswith(".env") for x in p.relative_to(root).parts): fail("结果目录包含 .env 凭据文件")
        file_sha_and_secret(p)
        files.append((p.relative_to(root).as_posix(), p))
    if not files: fail("结果目录为空")
    missing = [pat for pat in task.get("required_patterns", []) if not any(fnmatch.fnmatchcase(rel, pat) for rel, _ in files)]
    tmp = Path(tempfile.mkdtemp(prefix="queue-results-")); archive = tmp / f"results-{task_id}.zip"
    try:
        result_manifest = {"task_id": task_id, "worker": a.worker, "command": task["command"], "source_package_sha256": task["package_sha256"], "exit_code": a.exit_code, "started_at": a.started_at, "finished_at": getattr(a, "finished_at", "unknown"), "latest_output_mtime": datetime.fromtimestamp(max(int(p.stat().st_mtime) for _, p in files), timezone.utc).isoformat(timespec="seconds"), "files": [{"path": r, "sha256": file_sha_and_secret(p), "size": p.stat().st_size} for r, p in files], "missing_required_patterns": missing}
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for rel, p in files: z.write(p, rel)
            info = zipfile.ZipInfo("RESULT_MANIFEST.json", (1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, canonical(result_manifest))
        archive_sha = hash_file(archive); tag = f"result-{task_id}"
        try:
            gh("release", "create", tag, "--repo", a.repo, "--title", f"Results {task_id}", "--notes", "Immutable experiment outputs")
        except RuntimeError:
            pass
        parts = split_file(archive)
        for part in parts:
            # Existing asset is accepted only after byte-for-byte hash verification.
            existing = False
            try:
                gh("release", "upload", tag, str(part), "--repo", a.repo)
            except RuntimeError:
                existing = True
            if existing:
                verify = tmp / ("verify-" + part.name)
                gh("release", "download", tag, "--repo", a.repo, "--pattern", part.name, "--dir", str(tmp / "verify"))
                remote = tmp / "verify" / part.name
                if not remote.exists() or hash_file(remote) != hash_file(part): fail(f"已有结果 asset 与本次内容不同: {part.name}")
        # Verify every uploaded byte by downloading the remote assets.
        verify_dir = tmp / "verify-all"; verify_dir.mkdir()
        for part in parts: gh("release", "download", tag, "--repo", a.repo, "--pattern", part.name, "--dir", str(verify_dir))
        if len(parts) == 1:
            remote_sha = hash_file(verify_dir / parts[0].name)
            if remote_sha != archive_sha: fail("远端结果包 SHA256 校验失败")
        else:
            rebuilt = tmp / "rebuilt.zip"
            with rebuilt.open("wb") as f:
                for part in parts:
                    with (verify_dir / part.name).open("rb") as source: shutil.copyfileobj(source, f, 1024 * 1024)
            if hash_file(rebuilt) != archive_sha: fail("远端分片结果包 SHA256 校验失败")
        result_obj = {"task_id": task_id, "tag": tag, "archive_sha256": archive_sha, "assets": [p.name for p in parts], "manifest": result_manifest}
        old = g.get_file(f"results/{task_id}.json")
        if old:
            oldj = json.loads(old[1])
            if oldj.get("archive_sha256") != archive_sha: fail("已有结果记录哈希不同；拒绝覆盖")
        else: g.put_file(f"results/{task_id}.json", json.dumps(result_obj, ensure_ascii=False, indent=2), f"Record results {task_id}")
        success = a.exit_code == 0 and not missing
        state = {"task_id": task_id, "status": "completed" if success else "failed", "worker": a.worker, "exit_code": a.exit_code, "missing_required_patterns": missing, "result_sha256": archive_sha, "updated_at": now()}
        update_state(g, task_id, state, f"Finish {task_id}: {state['status']}")
        refresh_indexes(g, ("tasks", "results"))
        print(f"结果已上传并验证: {archive_sha}; 状态={state['status']}")
    finally: shutil.rmtree(tmp, ignore_errors=True)

def download(a: argparse.Namespace) -> None:
    task_id = valid_id(a.task_id); g = GitHub(a.repo); task = task_manifest(g, task_id)
    dest = Path(a.destination)
    if dest.exists() and any(dest.iterdir()): fail("destination 必须不存在或为空")
    tmp = Path(tempfile.mkdtemp(prefix="queue-download-"))
    try:
        gh("release", "download", task["release_tag"], "--repo", a.repo, "--pattern", "source.zip", "--dir", str(tmp))
        safe_extract(tmp / "source.zip", dest, task["package_sha256"])
        print(f"任务包已校验并解压至 {dest.resolve()}")
    finally: shutil.rmtree(tmp, ignore_errors=True)

def refresh(a: argparse.Namespace) -> None:
    g = GitHub(a.repo)
    refresh_indexes(g)
    print("任务与结果索引已刷新")

def list_tasks(a: argparse.Namespace) -> None:
    g = GitHub(a.repo); tasks = g.list_dir("tasks"); states = {}
    for item in g.list_dir("states"):
        if item.get("name", "").endswith(".json"):
            got = g.get_file("states/" + item["name"])
            if got: states[item["name"][:-5]] = json.loads(got[1])
    print("TASK_ID\tSTATUS\tTITLE\tCOMMAND\tSOURCE_SHA256")
    for item in sorted(tasks, key=lambda x: x.get("name", "")):
        name = item.get("name", "")
        if not name.endswith(".json"): continue
        tid = name[:-5]; task = json.loads(g.get_file("tasks/" + name)[1]); print(f"{tid}\t{states.get(tid, {}).get('status', 'unknown')}\t{task.get('title', '')}\t{json.dumps(task.get('command', []), ensure_ascii=False)}\t{task.get('package_sha256', '')}")

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="GitHub-backed local experiment queue"); sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("submit"); s.add_argument("--repo", required=True); s.add_argument("--project", required=True); s.add_argument("--task-id", required=True); s.add_argument("--title", required=True); s.add_argument("--command-json", required=True); s.add_argument("--required-json", default="[]"); s.set_defaults(func=submit)
    c = sub.add_parser("claim"); c.add_argument("--repo", required=True); c.add_argument("--task-id", required=True); c.add_argument("--worker", required=True); c.set_defaults(func=claim)
    d = sub.add_parser("download"); d.add_argument("--repo", required=True); d.add_argument("--task-id", required=True); d.add_argument("--destination", required=True); d.set_defaults(func=download)
    u = sub.add_parser("complete"); u.add_argument("--repo", required=True); u.add_argument("--task-id", required=True); u.add_argument("--worker", required=True); u.add_argument("--claim-token", required=True); u.add_argument("--results", required=True); u.add_argument("--exit-code", type=int, required=True); u.add_argument("--started-at", default="unknown"); u.add_argument("--finished-at", default="unknown"); u.set_defaults(func=upload_results)
    r = sub.add_parser("refresh-index"); r.add_argument("--repo", required=True); r.set_defaults(func=refresh)
    l = sub.add_parser("list"); l.add_argument("--repo", required=True); l.set_defaults(func=list_tasks)
    return p

if __name__ == "__main__":
    try:
        args = parser().parse_args(); args.func(args)
    except (RuntimeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"错误: {exc}", file=sys.stderr); raise SystemExit(1)
