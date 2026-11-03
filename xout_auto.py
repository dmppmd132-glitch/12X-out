"""X-out 連續缺陷自動判定（矩陣版，輸入為資料夾）

讀取對象：無副檔名的 Strip 檔（例如 22691651-000_0504，可用記事本開啟），
同時也相容 *.txt。檔案開頭的 Strip_ID / Origin_Location 等標題列會自動略過。

廠內慣例：0 = 缺陷，1 = 良品（可用 --defect 改）

矩陣檔格式：
    Strip_ID : 22691651-000 0504   <- 標題列（含 0/1 以外的字元，自動略過）
    Origin_Location : TUL

    1111111111 1111111111      <- block 之間用空格隔開
    0111110111 1111110111
    ...

流程：
1. 自動判斷 block 數：每列用空格切開，幾段就是幾個 block（各段寬度即 block 寬）
   - 沒有空格分隔的檔案視為 1 個 block（可用 --blocks N 強制平分成 N 個）
2. 有 2 個以上 block：在每個 block 交界插入一欄槽欄。
   交界左欄或右欄任一為缺陷，該列的槽欄就算缺陷（客戶規則，2 block 時與其 exe 一致）
3. 缺陷格以上下左右 4 連通成團，團內格數 = 面積
4. 面積 >= 門檻（預設 12）=> X_out（Fail）

用法：
    python xout_auto.py 資料夾 [--threshold 12] [--defect 0] [--blocks N]
不帶參數（例如雙擊 exe）時，分析程式所在資料夾。
結果寫入 資料夾\\output_xout\\ ：
    report.txt、report.csv，以及 xout_files\\<資料夾名>\\
    （判定為 X_out 的檔案，預設「剪下」搬入；加 --copy 改為複製）。
    例：處理 dist，其中 dist\\22691651-000\\ 有 X_out 檔
        => dist\\output_xout\\xout_files\\22691651-000\\該檔案
"""
import argparse
import csv
import os
import shutil
import sys
from collections import deque

THRESHOLD = 12
DEFECT = 0
OUT_DIR = "output_xout"
XOUT_DIR = "xout_files"  # X_out 檔案搬移的目的地（在 output_xout 底下）


# ---------- 讀檔與 block 判斷 ----------
def read_matrix(path, blocks_override=None):
    """回傳 (grid, widths)。grid 為原始 0/1 二維 list，widths 為各 block 的欄寬。"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip().replace(",", " ").replace("\t", " ")
            if not s or any(ch not in "01 " for ch in s):
                continue  # 空行、標題列
            rows.append(s.split())
    if not rows:
        raise ValueError("找不到矩陣資料")

    if any(len(t) > 1 for r in rows for t in r):  # 有「一段多個格子」=> 以空格分 block
        patterns = {tuple(len(t) for t in r) for r in rows}
        if len(patterns) != 1:
            raise ValueError("各列的 block 分隔位置不一致")
        widths = list(patterns.pop())
    else:  # 每個格子都單獨一個字元，或整列沒有空格
        total = len(rows[0]) if len(rows[0]) > 1 else len(rows[0][0])
        widths = [total]

    grid = [[int(c) for c in "".join(r)] for r in rows]
    if len({len(r) for r in grid}) != 1:
        raise ValueError("各列欄數不一致")
    if sum(widths) != len(grid[0]):  # 單字元格子的情況
        widths = [len(grid[0])]

    if blocks_override and blocks_override > 1 and len(widths) == 1:
        if widths[0] % blocks_override:
            raise ValueError(f"{widths[0]} 欄無法平分成 {blocks_override} 個 block")
        widths = [widths[0] // blocks_override] * blocks_override
    return grid, widths


# ---------- 分析 ----------
def build_defect_grid(grid, widths, defect, use_slot):
    """回傳 (缺陷圖, 欄對照)。欄對照：新欄 -> 原欄(1 起算)，槽欄為 None。"""
    dg = [[1 if v == defect else 0 for v in row] for row in grid]
    colmap, pos = [], 0
    for bi, w in enumerate(widths):
        colmap += list(range(pos + 1, pos + w + 1))
        pos += w
        if use_slot and bi < len(widths) - 1:
            colmap.append(None)

    out = []
    for row in dg:
        new, pos = [], 0
        for bi, w in enumerate(widths):
            new += row[pos:pos + w]
            if use_slot and bi < len(widths) - 1:
                new.append(1 if (row[pos + w - 1] or row[pos + w]) else 0)
            pos += w
        out.append(new)
    return out, colmap


def clusters(dg):
    """4 連通缺陷團，回傳 [(面積, [(列, 新欄), ...]), ...]（0 起算）。"""
    g = [r[:] for r in dg]
    n, m = len(g), len(g[0])
    res = []
    for i in range(n):
        for j in range(m):
            if not g[i][j]:
                continue
            g[i][j] = 0
            q, cells = deque([(i, j)]), []
            while q:
                x, y = q.popleft()
                cells.append((x, y))
                for a, b in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= a < n and 0 <= b < m and g[a][b]:
                        g[a][b] = 0
                        q.append((a, b))
            res.append((len(cells), cells))
    return res


def analyze(grid, widths, threshold=THRESHOLD, defect=DEFECT):
    use_slot = len(widths) >= 2
    cl_no = clusters(build_defect_grid(grid, widths, defect, False)[0])
    dg, colmap = build_defect_grid(grid, widths, defect, use_slot)
    cl = clusters(dg)

    hits = []
    for area, cells in sorted(cl, key=lambda c: -c[0]):
        if area < threshold:
            continue
        rows = [x + 1 for x, _ in cells]
        cols = [colmap[y] for _, y in cells if colmap[y] is not None]
        hits.append({
            "area": area,
            "rows": (min(rows), max(rows)),
            "cols": (min(cols), max(cols)) if cols else None,
            "slot_cells": sum(1 for _, y in cells if colmap[y] is None),
        })
    return {
        "blocks": len(widths),
        "widths": widths,
        "size": (len(grid), len(grid[0])),
        "max_no_slot": max((a for a, _ in cl_no), default=0),
        "max_with_slot": max((a for a, _ in cl), default=0),
        "hits": hits,
        "fail": bool(hits),
    }


# ---------- 資料夾批次 ----------
def find_files(base):
    found = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != OUT_DIR]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in ("", ".txt"):  # 無副檔名的 Strip 檔（或 .txt）
                found.append(os.path.join(root, f))
    return sorted(found)


def unique_path(folder, name):
    """目的地若已有同名檔案，自動加 _1、_2…避免覆蓋。"""
    dst = os.path.join(folder, name)
    stem, ext = os.path.splitext(name)
    i = 1
    while os.path.exists(dst):
        dst = os.path.join(folder, f"{stem}_{i}{ext}")
        i += 1
    return dst


def source_folder_name(path, base):
    """X_out 檔所屬的資料夾名：取 base 底下第一層資料夾名；檔案直接在 base 時用 base 本身的名稱。"""
    rel = os.path.relpath(path, base)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return os.path.basename(os.path.normpath(base)) or "root"


def move_xout(path, base, out_dir, copy=False):
    """把 X_out 檔搬（或複製）到 output_xout\\xout_files\\<資料夾名>\\，回傳目的路徑。"""
    dest_dir = os.path.join(out_dir, XOUT_DIR, source_folder_name(path, base))
    os.makedirs(dest_dir, exist_ok=True)
    dst = unique_path(dest_dir, os.path.basename(path))
    (shutil.copy2 if copy else shutil.move)(path, dst)
    return dst


def describe(h):
    cols = f"欄 {h['cols'][0]}-{h['cols'][1]}" if h["cols"] else "僅槽欄"
    slot = f"，含槽 {h['slot_cells']} 格" if h["slot_cells"] else ""
    return f"面積 {h['area']}（列 {h['rows'][0]}-{h['rows'][1]}，{cols}{slot}）"


def run(base, threshold, defect, blocks, copy=False):
    base = os.path.abspath(base.strip().strip('"'))
    # 輸出結構：base\\output_xout\\report.txt、report.csv、xout_files\\<資料夾名>\\（X_out 檔案）
    out_dir = os.path.join(base, OUT_DIR)
    os.makedirs(out_dir, exist_ok=True)
    files = find_files(base)
    if not files:
        print(f"{base} 底下找不到無副檔名（或 .txt）的矩陣檔")
        return

    results, lines, fails = [], [], 0
    fail_paths = []
    for path in files:
        name = os.path.relpath(path, base)
        try:
            grid, widths = read_matrix(path, blocks)
            r = analyze(grid, widths, threshold, defect)
        except Exception as e:  # noqa: BLE001
            print(f"[錯誤] {name}: {e}")
            results.append({"name": name, "error": str(e)})
            continue
        r["name"] = name
        r["path"] = path
        results.append(r)

        tag = "FAIL" if r["fail"] else "PASS"
        print(f"{tag}  {name}  {r['blocks']} block  最大面積 {r['max_no_slot']}(無槽)/{r['max_with_slot']}(含槽)")
        for h in r["hits"]:
            print(f"        └ {describe(h)}")
        if r["blocks"] >= 3:
            print("        ※ 3 個以上 block 客戶未定義槽規則，此處每個交界都插槽，請與客戶確認")
        if r["fail"]:
            fails += 1
            fail_paths.append(path)
            lines.append(f"{fails}. {name} X_out->{[h['area'] for h in r['hits']]}")

    # 分析全部完成後，才搬移 X_out 檔案（避免搬動途中影響掃描）
    moved = 0
    for path in fail_paths:
        try:
            move_xout(path, base, out_dir, copy)
            moved += 1
        except Exception as e:  # noqa: BLE001
            print(f"[搬移失敗] {os.path.relpath(path, base)}: {e}")

    ok = [r for r in results if "error" not in r]
    lines.append(f"Total {len(ok)} file --> {fails} X_out" if fails else f"{base} does no have X_out")
    with open(os.path.join(out_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    with open(os.path.join(out_dir, "report.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["檔案", "block數", "block寬度", "列", "欄", "最大面積(無槽)", "最大面積(含槽)", "結果", "X_out面積", "明細"])
        for r in results:
            if "error" in r:
                w.writerow([r["name"], "", "", "", "", "", "", "錯誤", "", r["error"]])
                continue
            w.writerow([
                r["name"], r["blocks"], "+".join(map(str, r["widths"])), *r["size"],
                r["max_no_slot"], r["max_with_slot"], "FAIL" if r["fail"] else "PASS",
                ",".join(str(h["area"]) for h in r["hits"]),
                " | ".join(describe(h) for h in r["hits"]),
            ])
    print(f"\n共 {len(ok)} 檔，{fails} 檔 FAIL。報告：{out_dir}")
    if fail_paths:
        act = "複製" if copy else "搬移"
        print(f"已{act} {moved}/{len(fail_paths)} 個 X_out 檔案到：{os.path.join(out_dir, XOUT_DIR)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="X-out 連續缺陷自動判定（矩陣版）")
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--threshold", type=int, default=THRESHOLD)
    ap.add_argument("--defect", type=int, default=DEFECT, help="缺陷值，預設 0")
    ap.add_argument("--blocks", type=int, default=None, help="沒有空格分隔時，強制平分成 N 個 block")
    ap.add_argument("--copy", action="store_true", help="X_out 檔案改為複製（預設為剪下搬移）")
    a = ap.parse_args()

    no_arg = a.folder is None
    base = a.folder or (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                        else os.path.dirname(os.path.abspath(__file__)))
    run(base, a.threshold, a.defect, a.blocks, a.copy)
    if no_arg and os.name == "nt":
        os.system("pause")
