#!/usr/bin/env python3
"""Graphyfy — peta kode + roster, bagian dari skill team-roster v2.8.0.

Graphyfy = peta kode + roster: tiap file dicatat lokasi, ukuran (baris +
perkiraan token), links (dependensi/import), dan owner (dari roster
TheRoster kalau ada). Agent tanya graph dulu, bukan baca semua file.

Aturan (dari catatan konsep + baterai case + pengukuran ambang):
- Banyak yang baca, satu yang nulis, jangan pernah diedit tangan.
  Yang nulis hanya generator ini (saat build, atau pas merge).
- Graph BISA basi: graph mencatat commit_sha + generated_at.
  Kesegaran BUKAN "commit_sha == HEAD" mentah — itu mustahil untuk
  graph yang ikut ke-commit (commit graph itu sendiri menggerakkan
  HEAD melewati commit_sha yang dicatat graph). Kesegaran = "tidak ada
  file yang dipetakan graph berubah sejak commit_sha": diuji dengan
  `git diff --name-only <commit_sha> HEAD`, mengecualikan
  team/graph.json + team/shards/ (yang memang tidak pernah dipetakan).
  Commit yang hanya mengubah graph tetap segar; commit kerja apa pun
  membuat graph basi. `check` FAIL keras kalau basi (case 7).
- Graph bisa semahal wilayahnya: `query` hanya mengembalikan slice
  yang dibutuhkan (case 1). Shard per direktori hanya ditulis kalau
  --shards-dir diminta eksplisit — artefak yang di-commit cukup satu
  file: team/graph.json.
- Graph tidak menyimpan isi file, cuma metadata (case 15). File
  sensitif (.env, kunci, credentials) tidak masuk graph sama sekali.
  .gitignore dihormati (via `git ls-files --exclude-standard`).
- Bahasa selain Python dicatat sebagai unparsed — jujur, bukan
  pura-pura ke-parse (case 19).
- Ambang TERUKUR (evidence/threshold-measurement.md, 8 repo, +
  evidence/threshold-github-measurement.md, 5 repo):
  repo di bawah ambang iff file < 5 DAN baris < 500 (AND, bukan OR)
  — di bawah itu baca langsung lebih murah dari graph. Titik balik
  token murni itu per-file: ~140-230 token (~20-30 baris); file yang
  lebih kecil dari itu record-nya lebih mahal dari file-nya sendiri.
- Label "terakhir diketahui": tiap graph + tiap hasil query membawa
  generated_at + commit_sha, supaya cermin tidak pernah pura-pura tahu
  keadaan sekarang (analogi dunia kaca, case 11).

Subcommands:
    build   --repo DIR --out FILE [--shards-dir DIR] [--roster-dir DIR]
    check   --repo DIR --graph FILE
    query   --graph FILE [--repo DIR] [--dir PREFIX | --path PATH | --symbol NAME]

Fungsi bersama untuk validate.py / doctor.py:
    check_graph(team_dir, repo_root=None) -> (errors, warnings, status)
    graph_freshness(graph, repo) -> (state, detail, changed)

Estimasi token: chars/4 (kasar, dinyatakan di mana-mana dipakai).
"""

import argparse
import ast
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

VERSION = "2.8.0"
TOKENS_PER_CHAR = 4  # estimasi kasar: ~4 karakter per token (kode/Inggris)

# Direktori yang tidak pernah dipetakan.
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             ".tox", ".pytest_cache", ".mypy_cache"}
# Ekstensi biner — dicatat sebagai skipped, bukan node.
BINARY_EXTS = {".zip", ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif",
               ".ico", ".mp4", ".mov", ".mp3", ".wav", ".pdf", ".pyc",
               ".pyo", ".so", ".dll", ".exe", ".woff", ".woff2", ".ttf",
               ".eot", ".db", ".sqlite", ".parquet"}
# Nama file sensitif (case 15) — tidak masuk graph sama sekali.
SECRET_NAMES = {".env"}
SECRET_SUFFIXES = (".pem", ".key")
SECRET_SUBSTRINGS = ("credentials", "secret", "id_rsa")

TEXT_EXTS = {
    ".py": "python", ".md": "markdown", ".json": "json",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "shell",
    ".toml": "toml", ".txt": "text", ".svg": "svg",
    ".html": "html", ".css": "css", ".js": "javascript",
    ".ts": "typescript", ".rs": "rust", ".go": "go",
    ".sql": "sql", ".cfg": "config", ".ini": "config",
    ".gitignore": "gitignore", ".gitattributes": "gitattributes",
}
NO_EXT_NAMES = {"LICENSE", "Makefile", "Dockerfile", "CODEOWNERS",
                "pre-push"}

# Ambang TERUKUR (bukan tebakan): di bawah ambang iff file < 5 DAN
# baris < 500. Konstanta yang didokumentasikan — bukan knob config
# (tidak ada di team/config.json; kalau pengukuran baru mengubahnya,
# yang berubah adalah angka di sini + design note, bukan knob).
THRESHOLD_FILES = 5
THRESHOLD_LINES = 500
MAX_TEXT_BYTES = 2_000_000


def below_threshold(n_files, total_lines):
    """Aturan ambang TERUKUR — satu sumber kebenaran, dipakai build()
    (threshold_note) dan check_graph() (gate ambang): di bawah ambang
    iff file < 5 DAN baris < 500 (AND, bukan OR). Dua kaki harus
    terpenuhi: repo 16 file / 355 baris DI ATAS ambang (kakinya file
    yang lewat), repo 4 file / 400 baris di bawah ambang (dua-duanya
    di bawah). Dikunci skenario s13 [C4]."""
    return n_files < THRESHOLD_FILES and total_lines < THRESHOLD_LINES

# File turunan graph — tidak pernah dipetakan (graph tidak memetakan
# dirinya sendiri, dan shard adalah turunan dari graph).
GRAPH_PATH = "team/graph.json"
SHARDS_PREFIX = "team/shards/"

REGENERATE_CMD = ("python3 skills/team-roster/scripts/graph.py "
                  "build --repo . --out team/graph.json")


def estimate_tokens(text):
    """Perkiraan token kasar: chars/4. Dinyatakan di mana-mana dipakai."""
    return len(text) // TOKENS_PER_CHAR


def is_secret(path_rel):
    name = os.path.basename(path_rel)
    if name in SECRET_NAMES:
        return True
    low = name.lower()
    if low.endswith(SECRET_SUFFIXES):
        return True
    return any(s in low for s in SECRET_SUBSTRINGS)


def language_of(path_rel):
    _, ext = os.path.splitext(path_rel)
    if ext in TEXT_EXTS:
        return TEXT_EXTS[ext]
    if os.path.basename(path_rel) in NO_EXT_NAMES:
        return "text"
    if not ext:
        return "unknown"
    return "unknown"


def git(repo, *args):
    """Jalankan git read-only di repo. Kembalikan stdout strip, atau None."""
    try:
        out = subprocess.run(
            ["git", "-C", repo] + list(args),
            capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def git_z(repo, *args):
    """Seperti git(), tapi output dipisah NUL (untuk daftar path)."""
    try:
        out = subprocess.run(
            ["git", "-C", repo] + list(args),
            capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


def repo_head(repo):
    sha = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return sha, branch


def repo_dirty_files(repo):
    """Daftar path (relatif) yang kotor di working tree. [] kalau bersih."""
    out = git(repo, "status", "--porcelain")
    if out is None:
        return None
    files = []
    for line in out.splitlines():
        if len(line) > 3:
            files.append(line[3:].strip().strip('"'))
    return sorted(set(files))


def repo_tracked(repo):
    out = git(repo, "ls-files")
    if out is None:
        return None
    return set(out.splitlines())


def parse_python(path_abs, path_rel):
    """Parse satu file Python. Kembalikan (symbols, imports).

    symbols: [{name, kind, line}] — fungsi/kelas, nama qualified
    (Class.method) untuk method.
    imports: [{module, level, name}] — module dotted, level 0 = absolut.
    Gagal parse -> ([], []) dan parsed=False di pemanggil.
    """
    try:
        with open(path_abs, encoding="utf-8") as f:
            source = f.read()
    except (OSError, UnicodeDecodeError):
        return None, None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None, None

    symbols = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                symbols.append({"name": name, "kind": "function",
                                "line": child.lineno})
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                name = prefix + child.name
                symbols.append({"name": name, "kind": "class",
                                "line": child.lineno})
                walk(child, name + ".")
            else:
                walk(child, prefix)

    walk(tree)

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append({"module": a.name, "level": 0,
                                "name": a.name.split(".")[0]})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                imports.append({"module": mod, "level": node.level,
                                "name": a.name, "from_import": True})
    return symbols, imports


def resolve_import(imp, file_rel, repo_files_by_basename, repo_files):
    """Resolusi satu import ke path repo-relatif, atau None.

    repo_files: set path relatif semua file repo.
    repo_files_by_basename: basename -> [path, ...].

    `from pkg import b` dicoba dua arah: modulnya sendiri (pkg.py /
    pkg/__init__.py) DAN submodule-nya (pkg/b.py) — karena `b` bisa jadi
    submodule, bukan simbol di dalam pkg.
    """
    module = imp["module"]
    level = imp["level"]
    if level > 0:
        # Import relatif: dari direktori file, naik (level-1).
        base_dir = os.path.dirname(file_rel)
        for _ in range(level - 1):
            base_dir = os.path.dirname(base_dir)
        parts = [p for p in module.split(".") if p] if module else []
        cand_dirs = [os.path.join(base_dir, *parts) if parts else base_dir]
        if imp.get("from_import"):
            cand_dirs.append(os.path.join(cand_dirs[0], imp["name"]))
    else:
        parts = [p for p in module.split(".") if p]
        if not parts:
            return None
        cand_dirs = [os.path.join(*parts)]
        if imp.get("from_import"):
            cand_dirs.append(os.path.join(*(parts + [imp["name"]])))
    for cand_dir in cand_dirs:
        for cand in (cand_dir + ".py",
                     os.path.join(cand_dir, "__init__.py")):
            if cand in repo_files:
                return cand
    # Import satu nama (import team_common): cari basename unik.
    if level == 0 and len(parts) == 1 and not imp.get("from_import"):
        cands = repo_files_by_basename.get(parts[0] + ".py", [])
        if len(cands) == 1:
            return cands[0]
    return None


def load_owners(roster_dir):
    """Baca team/claims (aktif) + team/features -> {file: (owner, source)}.

    Klaim aktif menang atas fitur. Kalau >1 owner berbeda menutupi file
    yang sama, owner pertama (urut abjad) yang dipakai + flag ambigu.
    "Aktif" di sini = status active di record — termasuk klaim yang
    sudah kedaluwarsa/stealable: graph menunjukkan siapa yang terakhir
    mengklaim file itu (tempat bertanya), bukan kunci hidup; yang
    memutuskan "apakah file ini terkunci sekarang" adalah claim.py
    check, bukan graph.
    """
    owners = {}
    ambiguous = set()
    if not roster_dir or not os.path.isdir(roster_dir):
        return owners, ambiguous

    def note(file_path, owner, source):
        if file_path in owners:
            if owners[file_path][0] != owner:
                ambiguous.add(file_path)
            return
        owners[file_path] = (owner, source)

    claims_dir = os.path.join(roster_dir, "claims")
    if os.path.isdir(claims_dir):
        for name in sorted(os.listdir(claims_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(claims_dir, name),
                          encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("status") != "active":
                continue
            owner = rec.get("owner_id") or rec.get("owner")
            if not owner:
                continue
            task = rec.get("task_id") or os.path.splitext(name)[0]
            for fp in rec.get("files") or []:
                note(fp, owner, "claim:%s" % task)

    feats_dir = os.path.join(roster_dir, "features")
    if os.path.isdir(feats_dir):
        for name in sorted(os.listdir(feats_dir)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(feats_dir, name),
                          encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            owner = rec.get("owner_id") or rec.get("owner")
            if not owner:
                continue
            fid = rec.get("id") or os.path.splitext(name)[0]
            for fp in rec.get("files") or []:
                note(fp, owner, "feature:%s" % fid)
    return owners, ambiguous


def _is_mapped(path_rel, exclude_rel, exclude_prefixes):
    """True kalau path ini masuk peta (bukan graph itu sendiri, bukan
    shard, bukan dir yang dilewati)."""
    if path_rel in exclude_rel:
        return False
    if any(path_rel.startswith(p) for p in exclude_prefixes):
        return False
    parts = path_rel.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return False
    return True


def collect_files(repo, exclude_rel=frozenset(), exclude_prefixes=()):
    """Daftar file yang dipetakan graph.

    Utama: `git ls-files --cached --others --exclude-standard` —
    tracked + untracked-yang-tidak-di-ignore, jadi .gitignore
    dihormati. Kalau git tidak tersedia / bukan repo, fallback ke
    os.walk (tanpa .gitignore — dicatat di gitignore_applied).

    Kembalikan (rel_paths, skipped_binary, excluded_secret,
    gitignore_applied). Symlink dilewati; file sensitif dan biner
    tidak masuk daftar.
    """
    exclude_rel = set(exclude_rel) | {GRAPH_PATH}
    exclude_prefixes = tuple(exclude_prefixes) + (SHARDS_PREFIX,)
    rel_paths = []
    skipped_binary = []
    excluded_secret = []

    listed = git_z(repo, "ls-files", "-z", "--cached", "--others",
                   "--exclude-standard")
    tracked = None
    if listed is not None:
        tracked_list = git_z(repo, "ls-files", "-z", "--cached")
        tracked = set(tracked_list) if tracked_list is not None else set()
        for rel in sorted(listed):
            if not _is_mapped(rel, exclude_rel, exclude_prefixes):
                continue
            full = os.path.join(repo, rel)
            if os.path.islink(full):
                continue
            if is_secret(rel):
                excluded_secret.append(rel)
                continue
            _, ext = os.path.splitext(rel)
            if ext.lower() in BINARY_EXTS:
                skipped_binary.append(rel)
                continue
            rel_paths.append(rel)
        return rel_paths, skipped_binary, excluded_secret, True, tracked

    # Fallback: bukan repo git (atau git tidak ada) — jalan biasa.
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, repo).replace(os.sep, "/")
            if not _is_mapped(rel, exclude_rel, exclude_prefixes):
                continue
            if os.path.islink(full):
                continue
            if is_secret(rel):
                excluded_secret.append(rel)
                continue
            _, ext = os.path.splitext(name)
            if ext.lower() in BINARY_EXTS:
                skipped_binary.append(rel)
                continue
            rel_paths.append(rel)
    return rel_paths, skipped_binary, excluded_secret, False, None


def count_for_threshold(repo):
    """Hitung (n_files, total_lines) untuk gate ambang.

    n_files = file yang akan dipetakan build (teks, bukan biner,
    bukan sensitif, bukan graph/shard). total_lines hanya dihitung
    kalau n_files < THRESHOLD_FILES — karena ambang memakai AND,
    repo dengan >= 5 file sudah pasti di atas ambang apa pun
    jumlah barisnya, jadi tidak perlu membaca isi file-nya.
    total_lines None berarti "tidak dihitung (tidak perlu)".
    """
    rel_paths, _b, _s, _g, _t = collect_files(
        repo, exclude_rel={GRAPH_PATH}, exclude_prefixes=(SHARDS_PREFIX,))
    n_files = len(rel_paths)
    if n_files >= THRESHOLD_FILES:
        return n_files, None
    total_lines = 0
    for rel in rel_paths:
        full = os.path.join(repo, rel)
        try:
            if os.path.getsize(full) > MAX_TEXT_BYTES:
                continue
            with open(full, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        total_lines += text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return n_files, total_lines


def build(repo, roster_dir=None, out_path=None, shards_dir=None):
    repo = os.path.abspath(repo)
    sha, branch = repo_head(repo)
    dirty = repo_dirty_files(repo)
    tracked = repo_tracked(repo)
    exclude_rel = set()
    exclude_prefixes = set()
    if out_path:
        out_abs = os.path.abspath(out_path)
        try:
            exclude_rel.add(os.path.relpath(out_abs, repo).replace(os.sep, "/"))
        except ValueError:
            pass
    if shards_dir:
        shards_abs = os.path.abspath(shards_dir)
        try:
            exclude_prefixes.add(
                os.path.relpath(shards_abs, repo).replace(os.sep, "/") + "/")
        except ValueError:
            pass
    rel_paths, skipped_binary, excluded_secret, gitignore_applied, _t = \
        collect_files(repo, exclude_rel=exclude_rel,
                      exclude_prefixes=tuple(exclude_prefixes))
    repo_files = set(rel_paths)
    by_basename = {}
    for p in rel_paths:
        by_basename.setdefault(os.path.basename(p), []).append(p)

    if roster_dir is None:
        cand = os.path.join(repo, "team")
        roster_dir = cand if os.path.isdir(cand) else None
    owners, ambiguous = load_owners(roster_dir)

    stdlib = getattr(sys, "stdlib_module_names", set())

    files = []
    symbols_index = {}
    total_lines = 0
    total_tokens = 0
    parsed_count = 0
    for rel in rel_paths:
        full = os.path.join(repo, rel)
        lang = language_of(rel)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        rec = {
            "path": rel,
            "bytes": size,
            "language": lang,
            "parsed": False,
            "tracked": (rel in tracked) if tracked is not None else None,
            "owner": "unassigned",
            "owner_source": None,
        }
        if rel in owners:
            rec["owner"], rec["owner_source"] = owners[rel]
            if rel in ambiguous:
                rec["owner_ambiguous"] = True
        # Baca isi (teks saja).
        text = None
        if size <= MAX_TEXT_BYTES:
            try:
                with open(full, encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                text = None
        if text is None:
            rec["lines"] = None
            rec["tokens_est"] = None
            rec["symbols"] = []
            rec["links"] = []
            rec["links_unresolved"] = []
            rec["used_by"] = []
            rec["note"] = "binary or undecodable or too large"
            files.append(rec)
            continue
        lines = text.count("\n") + (1 if text and not text.endswith("\n")
                                     else 0)
        rec["lines"] = lines
        rec["tokens_est"] = estimate_tokens(text)
        total_lines += lines
        total_tokens += rec["tokens_est"]
        symbols = []
        links = []
        unresolved = []
        if lang == "python":
            parsed_symbols, imports = parse_python(full, rel)
            if parsed_symbols is not None:
                rec["parsed"] = True
                parsed_count += 1
                symbols = parsed_symbols
                for s in symbols:
                    symbols_index.setdefault(s["name"], []).append({
                        "file": rel, "line": s["line"],
                        "kind": s["kind"]})
                for imp in imports or []:
                    target = resolve_import(imp, rel, by_basename,
                                            repo_files)
                    if target and target != rel:
                        if target not in links:
                            links.append(target)
                    else:
                        mod = imp["module"] or imp["name"]
                        reason = ("stdlib" if mod.split(".")[0] in stdlib
                                  else "external")
                        entry = {"module": mod, "reason": reason}
                        if entry not in unresolved:
                            unresolved.append(entry)
            else:
                rec["note"] = "python parse failed (syntax error)"
        rec["symbols"] = symbols
        rec["links"] = sorted(links)
        rec["links_unresolved"] = unresolved
        rec["used_by"] = []
        files.append(rec)

    # used_by = kebalikan links (konsep: "dipakai oleh").
    by_path = {f["path"]: f for f in files}
    for f in files:
        for target in f["links"]:
            if target in by_path and f["path"] not in by_path[target]["used_by"]:
                by_path[target]["used_by"].append(f["path"])
    for f in files:
        f["used_by"] = sorted(f["used_by"])

    shards = {}
    for f in files:
        top = f["path"].split("/")[0] if "/" in f["path"] else "root"
        s = shards.setdefault(top, {"files": 0, "lines": 0,
                                    "tokens_est": 0})
        s["files"] += 1
        s["lines"] += f["lines"] or 0
        s["tokens_est"] += f["tokens_est"] or 0

    langs = {}
    for f in files:
        langs[f["language"]] = langs.get(f["language"], 0) + 1

    # Ambang TERUKUR — AND, bukan OR (lihat below_threshold()).
    threshold_note = None
    if below_threshold(len(files), total_lines):
        threshold_note = (
            "repo kecil (%d file, %d baris): di bawah ambang TERUKUR "
            "(%d file DAN %d baris) — baca langsung lebih murah dari "
            "graph (case 2; titik balik per-file ~140-230 token)" % (
                len(files), total_lines,
                THRESHOLD_FILES, THRESHOLD_LINES))

    graph = {
        "graphyfy_version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "commit_sha": sha,
        "commit_short": sha[:7] if sha else None,
        "branch": branch,
        "working_tree_dirty": bool(dirty),
        "dirty_files": dirty if dirty is not None else None,
        "gitignore_applied": gitignore_applied,
        "roster_source": roster_dir,
        "threshold_note": threshold_note,
        "stats": {
            "files": len(files),
            "tracked": sum(1 for f in files if f["tracked"]),
            "untracked": sum(1 for f in files if f["tracked"] is False),
            "excluded_secret": len(excluded_secret),
            "skipped_binary": len(skipped_binary),
            "total_lines": total_lines,
            "total_tokens_est": total_tokens,
            "parsed": parsed_count,
            "unparsed": len(files) - parsed_count,
            "languages": langs,
        },
        "shards": shards,
        "symbols": symbols_index,
        "files": files,
    }
    return graph, skipped_binary, excluded_secret


def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def graph_freshness(graph, repo):
    """Satu sumber kebenaran kesegaran graph — dipakai `check` CLI,
    `check_graph` (validate.py / doctor.py), dan query.

    Kembalikan (state, detail, changed) dengan state:
      "fresh" — tidak ada file yang dipetakan berubah sejak
          commit_sha (commit yang hanya mengubah team/graph.json /
          team/shards/ tetap segar: graph tidak memetakan dirinya
          sendiri, jadi peta itu masih benar).
      "stale" — ada file yang dipetakan berubah sejak commit_sha;
          `changed` = daftar path (max 5 + jumlah).
      "unverifiable" — kesegaran tidak bisa dipastikan (bukan repo
          git / tidak ada commit_sha / base commit tidak ada di clone
          ini, mis. shallow clone).
      "missing" — graph tidak bisa dibaca (bukan dict / file hilang).
    """
    if not isinstance(graph, dict):
        return "missing", "graph tidak bisa dibaca (bukan JSON object)", []
    gsha = graph.get("commit_sha")
    if not gsha:
        return ("unverifiable",
                "graph tidak punya commit_sha (tidak bisa dipastikan "
                "kesegarannya)", [])
    head, _branch = repo_head(repo)
    if head is None:
        return ("unverifiable",
                "%s bukan repo git — tidak ada HEAD untuk dibandingkan" % repo,
                [])
    if gsha == head:
        return "fresh", "commit_sha %s == HEAD" % head[:7], []
    # commit_sha != HEAD belum tentu basi: kalau satu-satunya yang
    # berubah sejak commit_sha adalah graph itu sendiri (commit graph),
    # peta masih benar — graph tidak memetakan dirinya sendiri.
    diff = git(repo, "diff", "--name-only", gsha, head, "--", ".",
               ":!team/graph.json", ":!team/shards")
    if diff is None:
        return ("unverifiable",
                "base commit %s tidak ada di clone ini (shallow clone?) — "
                "tidak bisa dipastikan kesegarannya" % gsha[:7], [])
    changed = [l for l in diff.splitlines() if l.strip()]
    if not changed:
        return ("fresh",
                "hanya team/graph.json / team/shards/ yang berubah sejak "
                "commit %s (commit graph itu sendiri)" % gsha[:7], [])
    return ("stale",
            "sejak commit %s, %d file yang dipetakan berubah"
            % (gsha[:7], len(changed)), changed)


def check_graph(team_dir, repo_root=None):
    """Gate graph untuk validate.py / doctor.py.

    Kembalikan (errors, warnings, status) — status satu baris untuk
    ringkasan "VALIDATE: OK ...; graph: <status>".
      graph ada + segar -> OK, status "fresh (commit abc1234)".
      graph ada + basi -> ERROR (peta yang bohong lebih buruk dari
          tidak ada peta).
      graph ada + tidak bisa dipastikan -> WARNING (bukan repo git /
          tidak ada commit_sha / base tidak ada di clone).
      graph tidak ada + repo di atas ambang -> WARNING (bukan FAIL:
          tim lama yang belum membangun graph tidak di-brick).
      graph tidak ada + repo di bawah ambang -> OK diam-diam (baca
          langsung memang lebih murah di sana).
    """
    # NOTE: GRAPH_PATH is repo-relative ("team/graph.json") — for the
    # diff exclusions. Here team_dir already IS team/, so join with
    # the bare filename.
    graph_path = os.path.join(team_dir, "graph.json")
    if repo_root is None:
        repo_root = os.path.dirname(os.path.abspath(team_dir))
    if not os.path.isdir(repo_root):
        return ([],
                ["team/graph.json: repo root tidak ditemukan (%s) — gate "
                 "graph dilewati" % repo_root],
                "unknown (repo root not found)")

    if os.path.isfile(graph_path):
        try:
            with open(graph_path, encoding="utf-8") as f:
                graph = json.load(f)
        except (OSError, ValueError) as e:
            return (["team/graph.json is unreadable: %s — regenerate: %s"
                     % (e, REGENERATE_CMD)],
                    [],
                    "unreadable")
        state, detail, changed = graph_freshness(graph, repo_root)
        if state == "stale":
            shown = ", ".join(changed[:5])
            if len(changed) > 5:
                shown += ", ... (%d total)" % len(changed)
            return (["team/graph.json is STALE (basi): built at commit %s "
                     "(%s); %s: %s — regenerate: %s"
                     % (graph.get("commit_short") or "?",
                        graph.get("generated_at", "?"), detail, shown,
                        REGENERATE_CMD)],
                    [],
                    "STALE")
        if state == "unverifiable":
            return ([],
                    ["team/graph.json: %s — graph adalah snapshot "
                     "('terakhir diketahui'), bukan keadaan sekarang"
                     % detail],
                    "unverifiable")
        warnings = []
        dirty = repo_dirty_files(repo_root)
        if dirty:
            warnings.append(
                "working tree is dirty (%d file(s)): the graph matches "
                "HEAD, but the disk has moved since; answers are "
                "'terakhir diketahui', not 'now'." % len(dirty))
        return ([], warnings,
                "fresh (commit %s)" % (graph.get("commit_short") or "?"))

    # Tidak ada graph: gate ambang (TERUKUR, AND — below_threshold()).
    # total_lines None berarti count_for_threshold tidak menghitung
    # baris karena n_files >= 5 — sudah pasti di atas ambang.
    n_files, total_lines = count_for_threshold(repo_root)
    if total_lines is None or not below_threshold(n_files, total_lines):
        lines_txt = (("(%d file(s))" % n_files) if total_lines is None
                     else ("(%d file(s), %d lines)"
                           % (n_files, total_lines)))
        return ([],
                ["team/graph.json is missing but this repo is above the "
                 "graph threshold %s — build it: %s"
                 % (lines_txt, REGENERATE_CMD)],
                "missing (above threshold — build it)")
    return ([],
            [],
            "missing (below threshold: %d file(s), %d lines — direct "
            "reading is cheaper)" % (n_files, total_lines or 0))


def cmd_build(args):
    graph, skipped_binary, excluded_secret = build(
        args.repo, args.roster_dir, out_path=args.out,
        shards_dir=args.shards_dir)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    write_atomic(out, graph)
    if args.shards_dir:
        shards_dir = os.path.abspath(args.shards_dir)
        os.makedirs(shards_dir, exist_ok=True)
        by_shard = {}
        for f in graph["files"]:
            top = f["path"].split("/")[0] if "/" in f["path"] else "root"
            by_shard.setdefault(top, []).append(f)
        for top, recs in sorted(by_shard.items()):
            shard = {
                "graphyfy_version": VERSION,
                "generated_at": graph["generated_at"],
                "repo": graph["repo"],
                "commit_sha": graph["commit_sha"],
                "commit_short": graph["commit_short"],
                "shard": top,
                "files": recs,
            }
            write_atomic(os.path.join(shards_dir, top + ".json"), shard)
        print("  shards: %d -> %s" % (len(by_shard), shards_dir))
    print("GRAPH: built %s" % out)
    print("  repo: %s @ %s (branch %s)"
          % (graph["repo"], graph["commit_short"], graph["branch"]))
    print("  files: %d (%d tracked, %d untracked), %d lines, ~%d tokens"
          % (graph["stats"]["files"], graph["stats"]["tracked"],
             graph["stats"]["untracked"], graph["stats"]["total_lines"],
             graph["stats"]["total_tokens_est"]))
    print("  parsed: %d, unparsed: %d, skipped binary: %d, "
          "excluded secret: %d"
          % (graph["stats"]["parsed"], graph["stats"]["unparsed"],
             len(skipped_binary), len(excluded_secret)))
    print("  .gitignore: %s"
          % ("applied (git ls-files)" if graph["gitignore_applied"]
             else "NOT applied (not a git repo — walk fallback)"))
    print("  working tree dirty: %s" % graph["working_tree_dirty"])
    if graph["threshold_note"]:
        print("  NOTE: " + graph["threshold_note"])
    print("  label: terakhir diketahui %s pada commit %s"
          % (graph["generated_at"], graph["commit_short"]))
    return 0


def cmd_check(args):
    graph_path = os.path.abspath(args.graph)
    repo = os.path.abspath(args.repo)
    try:
        with open(graph_path, encoding="utf-8") as f:
            graph = json.load(f)
    except (OSError, ValueError) as e:
        print("GRAPH CHECK: FAIL — graph tidak bisa dibaca: %s (%s)"
              % (graph_path, e))
        return 1
    state, detail, changed = graph_freshness(graph, repo)
    if state == "stale":
        print("GRAPH CHECK: FAIL — GRAPH BASI.")
        print("  graph dibuat pada commit %s (%s)"
              % (graph.get("commit_short", "?"),
                 graph.get("generated_at", "?")))
        print("  %s: %s" % (detail, ", ".join(changed[:5])))
        print("  Regenerate dulu: %s build --repo %s --out %s"
              % (sys.argv[0], repo, graph_path))
        return 1
    if state == "unverifiable":
        print("GRAPH CHECK: FAIL — %s" % detail)
        return 1
    head, _b = repo_head(repo)
    print("GRAPH CHECK: OK — graph segar (%s)." % detail)
    print("  terakhir diketahui %s pada commit %s"
          % (graph.get("generated_at", "?"), (head or "?")[:7]))
    dirty = repo_dirty_files(repo)
    if dirty:
        print("  WARNING — working tree kotor (%d file): graph cocok "
              "dengan HEAD, tapi disk sudah bergerak sesudah commit. "
              "Jawaban graph = 'terakhir diketahui', bukan 'sekarang'."
              % len(dirty))
    return 0


def cmd_query(args):
    graph_path = os.path.abspath(args.graph)
    try:
        with open(graph_path, encoding="utf-8") as f:
            graph = json.load(f)
    except (OSError, ValueError) as e:
        print("GRAPH QUERY: FAIL — graph tidak bisa dibaca: %s (%s)"
              % (graph_path, e))
        return 1
    repo = args.repo or graph.get("repo") or ""
    fresh = None
    if repo and os.path.isdir(repo):
        sha, _ = repo_head(repo)
        fresh = (sha is not None and graph.get("commit_sha") == sha)
    header = {
        "generated_at": graph.get("generated_at"),
        "commit_sha": graph.get("commit_sha"),
        "commit_short": graph.get("commit_short"),
        "fresh_vs_head": fresh,
        "label": "terakhir diketahui %s pada commit %s"
                 % (graph.get("generated_at", "?"),
                    graph.get("commit_short", "?")),
    }
    if args.dir is not None:
        prefix = args.dir.strip("/")
        recs = [f for f in graph["files"]
                if f["path"] == prefix or f["path"].startswith(prefix + "/")]
        out = dict(header)
        out["slice"] = "dir:%s" % prefix
        out["files"] = recs
        out["count"] = len(recs)
    elif args.path is not None:
        rec = next((f for f in graph["files"]
                    if f["path"] == args.path), None)
        if rec is None:
            print("GRAPH QUERY: FAIL — %s tidak ada di graph"
                  % args.path)
            return 1
        out = dict(header)
        out["slice"] = "path:%s" % args.path
        out["file"] = rec
    elif args.symbol is not None:
        hits = graph.get("symbols", {}).get(args.symbol)
        if not hits:
            print("GRAPH QUERY: FAIL — simbol %s tidak ada di graph"
                  % args.symbol)
            return 1
        out = dict(header)
        out["slice"] = "symbol:%s" % args.symbol
        out["locations"] = hits
    else:
        print("GRAPH QUERY: FAIL — pilih --dir, --path, atau --symbol")
        return 1
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Graphyfy — peta kode + roster, team-roster v2.8.0 "
                    "(stdlib saja).")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="bangun graph.json dari satu repo")
    b.add_argument("--repo", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--shards-dir", default=None,
                   help="tulis shard per direktori (opsional; default "
                        "tidak ada shard — yang di-commit cukup graph.json)")
    b.add_argument("--roster-dir", default=None,
                   help="direktori team/ (default: <repo>/team kalau ada)")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="FAIL keras kalau graph basi")
    c.add_argument("--repo", required=True)
    c.add_argument("--graph", required=True)
    c.set_defaults(func=cmd_check)

    q = sub.add_parser("query", help="kembalikan slice graph (label "
                       "terakhir diketahui ikut serta)")
    q.add_argument("--graph", required=True)
    q.add_argument("--repo", default=None,
                   help="repo untuk cek fresh_vs_head (default: repo "
                        "yang tercatat di graph, kalau masih ada)")
    q.add_argument("--dir", default=None)
    q.add_argument("--path", default=None)
    q.add_argument("--symbol", default=None)
    q.set_defaults(func=cmd_query)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
