#!/usr/bin/env python3
"""log_viewer.py — 日志查询 CLI

用法:
    python3 core/log_viewer.py tail -n 50          # 查看最近 50 行
    python3 core/log_viewer.py tail -f             # 实时监控
    python3 core/log_viewer.py tail --trace-id abc # 按 trace_id 过滤
    python3 core/log_viewer.py grep "error"        # 关键词搜索
"""

import os, sys, glob, argparse, time

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


def _list_log_files():
    return sorted(glob.glob(os.path.join(LOG_DIR, "*.log")))


def _read_lines(path, n=50):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return lines[-n:] if len(lines) > n else lines
    except Exception:
        return []


def _tail_all(n=50, trace_id=None):
    files = _list_log_files()
    entries = []
    for path in files:
        for line in _read_lines(path, n):
            line = line.rstrip("\n")
            if not line:
                continue
            if trace_id and f"[{trace_id}]" not in line:
                continue
            # 从行首提取时间戳用于排序
            ts = line[:15] if len(line) > 15 else line
            entries.append((ts, line))
    entries.sort(key=lambda x: x[0])
    return [e[1] for e in entries]


def _follow(trace_id=None):
    files = _list_log_files()
    positions = {p: os.path.getsize(p) for p in files}
    print(f"[log] following {len(files)} files in {LOG_DIR}")
    if trace_id:
        print(f"[log] filter trace_id={trace_id}")
    print("-" * 60)
    try:
        while True:
            time.sleep(0.5)
            for path in files:
                if not os.path.exists(path):
                    continue
                size = os.path.getsize(path)
                pos = positions.get(path, 0)
                if size < pos:
                    pos = 0
                if size == pos:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(pos)
                    for line in f:
                        line = line.rstrip("\n")
                        if trace_id and f"[{trace_id}]" not in line:
                            continue
                        print(line)
                positions[path] = size
    except KeyboardInterrupt:
        print("\n[log] stopped")


def _grep(pattern, n=100):
    files = _list_log_files()
    matches = []
    for path in files:
        name = os.path.basename(path)
        for line in _read_lines(path, n * 3):
            line = line.rstrip("\n")
            if pattern.lower() in line.lower():
                matches.append((line[:15], f"[{name}] {line}"))
    matches.sort(key=lambda x: x[0])
    return [m[1] for m in matches]


def main():
    parser = argparse.ArgumentParser(description="日志查询工具")
    sub = parser.add_subparsers(dest="cmd")

    p_tail = sub.add_parser("tail", help="查看日志尾部")
    p_tail.add_argument("-n", type=int, default=50, help="行数 (默认 50)")
    p_tail.add_argument("-f", action="store_true", help="持续监控")
    p_tail.add_argument("--trace-id", help="按 trace_id 过滤")

    p_grep = sub.add_parser("grep", help="关键词搜索")
    p_grep.add_argument("pattern", help="搜索关键词")
    p_grep.add_argument("-n", type=int, default=100, help="最大结果数")

    args = parser.parse_args()

    if args.cmd == "tail":
        if args.f:
            _follow(trace_id=args.trace_id)
        else:
            for line in _tail_all(n=args.n, trace_id=args.trace_id):
                print(line)
    elif args.cmd == "grep":
        for line in _grep(args.pattern, n=args.n):
            print(line)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
