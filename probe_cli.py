#!/usr/bin/env python3
"""动态发现 beike CLI 的全部叶子命令，并逐个用哨兵值触发一次请求。

这里刻意不硬编码命令树。静态反汇编出的命令树与参数名存在归属误差，而
clap 的 --help 输出是程序自身给出的权威结构，因此脚本先用 --help 递归
发现子命令，再解析每个叶子命令的 Options/Arguments 段。

每个参数都注入一个与其他参数不重复的哨兵值，例如 area 用 91、rooms 用
3、parlors 用 2、cookrooms 用 5、toilets 用 7。假服务端抓到的请求 JSON
里出现哪个哨兵，就说明该命令行参数映射到了哪个请求键，这样无需寄存器
数据流分析即可判定是否存在重命名。数值型参数必须给数字，否则 clap 会在
解析阶段就报错，所以数值参数的哨兵值单独维护。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# 明确不执行的命令：login 会等待 beike:// 回调而永久挂起，install/uninstall
# 会改动 runner 自身，mcp 会启动常驻服务，update/auth 属于本机维护动作。
SKIPPED_TOP_LEVEL = {
    "login",
    "install",
    "uninstall",
    "mcp",
    "update",
    "auth",
    "completion",
    "help",
}

# 布尔开关中只保留这两个：它们只影响输出格式，不会改变发出的请求字段。
# 其余布尔开关（force/purge/yes/save 等）属于被跳过命令的参数，不注入。
SAFE_BOOLEAN_FLAGS = {"json", "pretty"}

TEXT_SENTINELS = {
    "city": "北京",
    "city_name": "北京",
    "query": "PROBE_QUERY",
    "house_type": "old",
    "rank_type": "resblock",
    "entity_type": "newhouse",
    "biz": "newhouse",
    "house_code": "HC1",
    "resblock_id": "RB1",
    "agent_ucid": "AU1",
    "unix_id": "UX1",
    "house_id_list": "HL1",
    "id": "ID1",
    "ids": "IDS1",
    "entity_ids": "EID1",
    "appoint_time": "2026-01-02 10:00:00",
    "origin": "ORIGIN1",
    "destination": "DEST1",
    "dest": "DEST1",
    "addr": "ADDR1",
    "address": "ADDR1",
    "reason": "RSN1",
    "session_id": "SID1",
    "external_session_id": "ESID1",
    "context_intent": "CI1",
    "raw_query": "PROBE_RAW_QUERY",
    "source": "SRC1",
    "start": "2026-01-01",
    "end": "2026-01-31",
    "date": "2026-01-15",
    "type": "TYP1",
    "current": "CUR1",
    "struct": "struct",
}

# 数值参数必须提供合法数字，否则 clap 会在解析阶段直接失败，我们就抓不到请求。
NUMERIC_SENTINELS = {
    "area": "91",
    "rooms": "3",
    "parlors": "2",
    "cookrooms": "5",
    "toilets": "7",
    "count": "11",
    "page": "1",
    "page_size": "13",
    "size": "13",
    "limit": "13",
    "offset": "0",
    "price": "1701",
    "budget": "1703",
    "distance": "1709",
    "radius": "1711",
    "index": "0",
    "port": "8972",
}


def run_command(argv, timeout_seconds, log_path=None):
    """执行命令并返回 (exit_code, stdout, stderr)，超时按失败处理。"""
    started = time.time()
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout_seconds,
            text=True,
        )
        code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        code = -999
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\n[TIMEOUT after %ss]" % timeout_seconds
    if not isinstance(stdout, str):
        stdout = stdout.decode("utf-8", "replace")
    if not isinstance(stderr, str):
        stderr = stderr.decode("utf-8", "replace")
    elapsed = time.time() - started
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("$ %s\n" % " ".join(argv))
            handle.write("elapsed: %.2fs exit: %s\n" % (elapsed, code))
            handle.write("---- stdout ----\n%s\n" % stdout)
            handle.write("---- stderr ----\n%s\n" % stderr)
    return code, stdout, stderr


def split_sections(help_text):
    """把 clap 的 --help 输出按 Usage/Commands/Arguments/Options 切段。"""
    sections = {}
    current = None
    for line in help_text.splitlines():
        match = re.match(r"^(Commands|Arguments|Options):\s*$", line.strip())
        if match:
            current = match.group(1)
            sections[current] = []
            continue
        if current:
            sections[current].append(line)
    return sections


def parse_subcommands(section_lines):
    """从 Commands 段取出子命令名，跳过 -h/--help 这类伪子命令。"""
    names = []
    for line in section_lines:
        match = re.match(r"^ {2,}([A-Za-z0-9][A-Za-z0-9_-]*)\s{2,}\S", line)
        if match:
            names.append(match.group(1))
    return names


OPTION_PATTERN = re.compile(
    r"^\s+(?:-(\w), )?-{1,2}([A-Za-z0-9][A-Za-z0-9_-]*)"
    r"(?:[ =](<[^>]*>|\[[^\]]*\]))?"
)


def parse_options(section_lines):
    """解析 Options/Arguments 段，返回 [(长名, 是否需要取值)]。"""
    options = []
    for line in section_lines:
        match = OPTION_PATTERN.match(line)
        if not match:
            continue
        long_name = match.group(2)
        takes_value = match.group(3) is not None
        options.append((long_name, takes_value))
    return options


def sentinel_for(name):
    """按参数名给出哨兵值；未登记的名称回落到 P_<NAME> 形式，保证唯一可辨。"""
    normalized = name.replace("-", "_").lower()
    if normalized in TEXT_SENTINELS:
        return TEXT_SENTINELS[normalized]
    if normalized in NUMERIC_SENTINELS:
        return NUMERIC_SENTINELS[normalized]
    return "P_" + normalized.upper()


def build_command(binary, path, options):
    """拼出一次探测调用，只注入带值的参数与白名单布尔开关。"""
    argv = [binary] + path
    injected = []
    for name, takes_value in options:
        normalized = name.replace("-", "_").lower()
        if normalized in {"help", "version"}:
            continue
        if takes_value:
            value = sentinel_for(name)
            argv.extend(["--" + name, value])
            injected.append({"option": "--" + name, "sentinel": value})
        elif normalized in SAFE_BOOLEAN_FLAGS:
            argv.append("--" + name)
            injected.append({"option": "--" + name, "sentinel": True})
    return argv, injected


def discover(binary, help_root, capture_dir):
    """递归发现命令树，同时把每份 help 文本落盘作为命令契约的证据。"""
    os.makedirs(help_root, exist_ok=True)
    code, stdout, stderr = run_command([binary, "--help"], 60)
    with open(os.path.join(help_root, "root.txt"), "w", encoding="utf-8") as handle:
        handle.write("exit: %s\n---- stdout ----\n%s\n---- stderr ----\n%s\n" % (code, stdout, stderr))

    top_level = parse_subcommands(split_sections(stdout).get("Commands", []))
    leaves = []
    for name in top_level:
        if name in SKIPPED_TOP_LEVEL:
            continue
        code, sub_stdout, sub_stderr = run_command([binary, name, "--help"], 60)
        with open(os.path.join(help_root, name + ".txt"), "w", encoding="utf-8") as handle:
            handle.write(
                "exit: %s\n---- stdout ----\n%s\n---- stderr ----\n%s\n" % (code, sub_stdout, sub_stderr)
            )
        sub_sections = split_sections(sub_stdout)
        nested = parse_subcommands(sub_sections.get("Commands", []))
        nested = [n for n in nested if n not in SKIPPED_TOP_LEVEL]
        if nested:
            for child in nested:
                child_path = [name, child]
                code, child_stdout, child_stderr = run_command([binary] + child_path + ["--help"], 60)
                slug = name + "_" + child
                with open(os.path.join(help_root, slug + ".txt"), "w", encoding="utf-8") as handle:
                    handle.write(
                        "exit: %s\n---- stdout ----\n%s\n---- stderr ----\n%s\n"
                        % (code, child_stdout, child_stderr)
                    )
                child_sections = split_sections(child_stdout)
                options = parse_options(child_sections.get("Options", []))
                options += parse_options(child_sections.get("Arguments", []))
                leaves.append((child_path, options))
        else:
            options = parse_options(sub_sections.get("Options", []))
            options += parse_options(sub_sections.get("Arguments", []))
            leaves.append(([name], options))
    return leaves


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--capture-dir", default="capture")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    capture_dir = os.path.abspath(args.capture_dir)
    help_root = os.path.join(capture_dir, "help", args.label)
    out_root = os.path.join(capture_dir, "stdout", args.label)
    os.makedirs(out_root, exist_ok=True)

    leaves = discover(args.binary, help_root, capture_dir)
    print("discovered %d leaf commands for %s" % (len(leaves), args.label), flush=True)

    index = 0
    for path, options in leaves:
        index += 1
        argv, injected = build_command(args.binary, path, options)
        slug = "%02d-%s" % (index, "_".join(path))
        log_path = os.path.join(out_root, slug + ".txt")
        code, stdout, stderr = run_command(argv, args.timeout, log_path)
        with open(os.path.join(capture_dir, "commands.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "label": args.label,
                        "slug": slug,
                        "argv": argv,
                        "injected": injected,
                        "exit": code,
                        "ts": time.time(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print("[%s] %s -> exit %s" % (args.label, " ".join(argv), code), flush=True)


if __name__ == "__main__":
    main()
