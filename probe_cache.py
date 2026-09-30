#!/usr/bin/env python3
"""第四轮实验：确定会话缓存文件名的构成因子。

缓存文件形如 ~/.beike/sessions/<16hex>-<16hex>.json，提交历史里提到过
"三段式缓存文件名" 的修复。这里固定上报表成功（响应变体用 VAR1），只
变动一个环境因子，观察文件名是否随之变化，从而判断两个 hex 各自绑定
了什么：API Key、external session id，还是服务端地址。
"""

import argparse
import json
import os
import subprocess
import time


def run_command(argv, timeout_seconds, log_path, env_override=None):
    started = time.time()
    env = None
    if env_override:
        env = dict(os.environ)
        env.update(env_override)
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=timeout_seconds, text=True, env=env)
        code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        code = -999
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\n[TIMEOUT]"
    if not isinstance(stdout, str):
        stdout = stdout.decode("utf-8", "replace")
    if not isinstance(stderr, str):
        stderr = stderr.decode("utf-8", "replace")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write("$ %s\n" % " ".join(argv))
        handle.write("elapsed: %.2fs exit: %s\n" % (time.time() - started, code))
        handle.write("---- stdout ----\n%s\n---- stderr ----\n%s\n" % (stdout, stderr))
    return code


def snapshot_sessions(home):
    """只列出 sessions 目录下的文件名与内容，文件名本身就是观测对象。"""
    root = os.path.join(home, ".beike", "sessions")
    found = {}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    found[name] = handle.read()[:400]
            except OSError as exc:
                found[name] = "<unreadable: %s>" % exc
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--capture-dir", default="capture")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    capture_dir = os.path.abspath(args.capture_dir)
    out_root = os.path.join(capture_dir, "cache", args.label)
    timeline_path = os.path.join(capture_dir, "cache.jsonl")
    home = os.path.expanduser("~")

    analyze_args = ["analyze", "--context-intent", "CI1", "--pretty"]

    # 每一项只改一个环境因子：external session、API Key、路径、主机名。
    experiments = [
        ("baseline_mcp", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "ESID1", "BEIKE_MCP_API_KEY": "probe-dummy-key", "BEIKE_MCP_BASE_URL": "http://127.0.0.1:8971/mcp"}),
        ("other_external_session", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "ESID2", "BEIKE_MCP_API_KEY": "probe-dummy-key", "BEIKE_MCP_BASE_URL": "http://127.0.0.1:8971/mcp"}),
        ("other_api_key", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "ESID1", "BEIKE_MCP_API_KEY": "probe-other-key", "BEIKE_MCP_BASE_URL": "http://127.0.0.1:8971/mcp"}),
        ("other_base_path", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "ESID1", "BEIKE_MCP_API_KEY": "probe-dummy-key", "BEIKE_MCP_BASE_URL": "http://127.0.0.1:8971/mcp-variant"}),
        ("other_base_host", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "ESID1", "BEIKE_MCP_API_KEY": "probe-dummy-key", "BEIKE_MCP_BASE_URL": "http://localhost:8971/mcp"}),
        ("no_external_session", analyze_args, {"BEIKE_RAW_QUERY": "VAR1", "BEIKE_EXTERNAL_SESSION_ID": "", "BEIKE_SESSION_ID": "", "BEIKE_MCP_API_KEY": "probe-dummy-key", "BEIKE_MCP_BASE_URL": "http://127.0.0.1:8971/mcp"}),
    ]

    for index, (slug, extra_args, env_override) in enumerate(experiments, start=1):
        slug_full = "%02d-%s" % (index, slug)
        argv = [args.binary] + extra_args
        ts_before = time.time()
        code = run_command(argv, args.timeout, os.path.join(out_root, slug_full + ".txt"), env_override)
        ts_after = time.time()
        sessions = snapshot_sessions(home)
        with open(timeline_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "label": args.label,
                        "slug": slug_full,
                        "env_override": env_override,
                        "exit": code,
                        "ts_before": ts_before,
                        "ts_after": ts_after,
                        "sessions": sessions,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print("[%s] %s -> exit %s, session files: %s" % (args.label, slug_full, code, sorted(sessions)), flush=True)


if __name__ == "__main__":
    main()
