#!/usr/bin/env python3
"""Simulasi bervariasi TheRoster — 6 skenario, pakai script ASLI skill (bukan mock).

Setiap skenario jalan di salinan segar starter-repo, merekam perintah persis,
exit code, dan output asli tiap langkah. Hasil: transcripts.json (+ README.md).
Jalankan: python3 run_simulations.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path.home() / "workspace" / "team-roster-skill" / "skills" / "team-roster"
STARTER = SKILL / "examples" / "starter-repo"
SCRIPTS = SKILL / "scripts"
OUT = Path.home() / "workspace" / "team-roster-skill" / "simulations"
WORK = Path("/tmp/sim-work")
MAX_OUT = 1200


def sh(argv, cwd, env=None):
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    return p.returncode, (p.stdout + p.stderr).strip()


def fresh(name):
    d = WORK / name
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(STARTER, d)
    return d


def step(scn, label, argv, cwd, expect="pass", note=""):
    rc, out = sh(argv, cwd)
    ok = (rc == 0) if expect == "pass" else (rc != 0)
    scn["steps"].append({
        "label": label,
        "cmd": " ".join(argv),
        "exit": rc,
        "expected": expect,
        "gate_ok": ok,
        "output": out[:MAX_OUT],
        "note": note,
    })
    print(f"  [{'OK ' if ok else '?!?'}] {label} (exit {rc}, expected {expect})")
    return rc, out


def file_action(scn, label, path, content, cwd):
    p = Path(cwd) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        p.unlink(missing_ok=True)
        scn["steps"].append({"label": label, "cmd": f"rm {path}", "exit": 0,
                             "expected": "pass", "gate_ok": True,
                             "output": "", "note": "file dihapus (aksi kerja, bukan script)"})
    else:
        p.write_text(content)
        scn["steps"].append({"label": label, "cmd": f"write {path}", "exit": 0,
                             "expected": "pass", "gate_ok": True,
                             "output": content[:400],
                             "note": "AI menulis/mengubah file kerja (aksi kerja, bukan script)"})
    print(f"  [A] {label}")


def git(argv, cwd):
    return sh(["git", *argv], cwd)


def state(d):
    """Ringkasan status akhir: roster, klaim, fitur, PRD."""
    snap = {}
    for sub in ("roster", "claims", "features", "prd"):
        items = {}
        for f in sorted((Path(d) / "team" / sub).glob("*.json")):
            try:
                r = json.loads(f.read_text())
                items[r.get("id") or r.get("task_id") or f.stem] = {
                    "status": r.get("status"),
                    "version": r.get("version"),
                    "owner": r.get("owner_id"),
                    "released_by": r.get("released_by") or None,
                }
            except Exception as e:
                items[f.stem] = {"error": str(e)}
        snap[sub] = items
    return snap


def validate_end(scn, cwd):
    step(scn, "Validasi akhir semua file tim",
         ["python3", str(SCRIPTS / "validate.py"), "team"], cwd)
    scn["final_state"] = state(cwd)


# ---------------------------------------------------------------- S1: tabrakan
def s1():
    d = fresh("s1-tabrakan")
    scn = {"id": "s1", "title": "Tabrakan: dua AI garap file yang sama",
           "team": "Tim bangun website",
           "story": "Owner lagi garap login (klaim t-001, tapi sudah kedaluwarsa). "
                    "Assistant mau perbaiki login yang sama — lalu owner balik dan "
                    "mau garap file yang sama di saat bersamaan.",
           "tanpa_roster": "Dua AI edit src/auth/login.py bareng. Yang push duluan "
                           "ketimpa yang push belakangan — kerjaan satu orang hilang "
                           "tanpa jejak, ketahuannya pas merge conflict.",
           "steps": []}
    claim = ["python3", str(SCRIPTS / "claim.py"), "claim",
             "--claims-dir", "team/claims", "--roster", "team/roster",
             "--config", "team/config.json"]
    step(scn, "Assistant klaim src/auth/login.py (t-001 sudah kedaluwarsa, boleh diambil alih)",
         claim + ["--task", "t-101", "--title", "Perbaiki login",
                  "--owner", "assistant", "--files", "src/auth/login.py"], d, "pass",
         "t-001 kedaluwarsa 12:00Z — klaim lama jadi stealable, klaim baru sah.")
    step(scn, "Owner coba klaim file yang SAMA di saat bersamaan",
         claim + ["--task", "t-102", "--title", "Ganti validasi login",
                  "--owner", "owner", "--files", "src/auth/login.py"], d, "fail",
         "Satu file = satu pemilik. Klaim kedua DITOLAK sebelum kerja dimulai.")
    step(scn, "Cek klaim: siapa yang pegang apa",
         ["python3", str(SCRIPTS / "claim.py"), "check", "team/claims",
          "--roster", "team/roster", "--config", "team/config.json"], d, "pass")
    step(scn, "Assistant selesai, lepas klaim t-101",
         ["python3", str(SCRIPTS / "claim.py"), "release", "--task", "t-101",
          "--by", "assistant", "--claims-dir", "team/claims"], d, "pass")
    step(scn, "Owner klaim ulang file yang sama — sekarang boleh",
         claim + ["--task", "t-102", "--title", "Ganti validasi login",
                  "--owner", "owner", "--files", "src/auth/login.py"], d, "pass",
         "Giliran. Bukan rebutan.")
    validate_end(scn, d)
    return scn


# ---------------------------------------------------------------- S2: cari dulu
def s2():
    d = fresh("s2-cari-dulu")
    scn = {"id": "s2", "title": "Cari dulu sebelum bangun",
           "team": "Tim bangun website",
           "story": "Assistant disuruh bikin login. Sebelum nulis satu baris pun, "
                    "dia wajib cari dulu di registry — siapa tahu sudah ada.",
           "tanpa_roster": "Assistant bangun login dari nol, padahal login-api "
                           "sudah stabil dan dipakai. Dua versi login hidup "
                           "berdampingan, bug diperbaiki di satu tempat doang.",
           "steps": []}
    step(scn, "Cari 'login' di registry fitur",
         ["python3", str(SCRIPTS / "features.py"), "search", "login", "team/features"],
         d, "pass", "Registry jawab: sudah ada login-api [stable] v1.0.0 — REUSE.")
    step(scn, "Pakai login-api (catat pemakai baru)",
         ["python3", str(SCRIPTS / "features.py"), "use", "--id", "login-api",
          "--by", "assistant", "--features-dir", "team/features"], d, "pass")
    step(scn, "Coba daftarkan login-api lagi dengan id yang sama",
         ["python3", str(SCRIPTS / "features.py"), "register", "--id", "login-api",
          "--name", "Login API baru", "--does", "Login ulang",
          "--entry", "src/auth/login.py:login", "--owner", "assistant",
          "--keywords", "login", "auth", "--features-dir", "team/features",
          "--repo-root", "."], d, "fail", "Id duplikat DITOLAK — tidak ada dua fitur kembar.")
    step(scn, "Klaim kerjaan profil yang memakai login-api (bukan membangun ulang)",
         ["python3", str(SCRIPTS / "claim.py"), "claim", "--task", "t-103",
          "--title", "Pakai login-api di halaman profil", "--owner", "assistant",
          "--files", "src/profile.py", "--reserves-feature", "login-api",
          "--claims-dir", "team/claims", "--roster", "team/roster",
          "--config", "team/config.json"], d, "pass")
    validate_end(scn, d)
    return scn


# ---------------------------------------------------------------- S3: PRD
def s3():
    d = fresh("s3-prd")
    scn = {"id": "s3", "title": "PRD dibagi ke AI beda + done-gate",
           "team": "Tim produk (4 AI: frontend, backend, QA, docs)",
           "story": "PRD 'Checkout' dipecah: payment-api dikerjakan assistant. "
                    "Owner mau menandai PRD selesai — tapi gerbang done tidak "
                    "membiarkan klaim selesai mendahului bukti.",
           "tanpa_roster": "PRD ditulis 'done' di dokumen, padahal payment-api "
                           "belum terdaftar — apalagi stabil. Status bohong, "
                           "ketahuannya pas pelanggan bayar dan gagal.",
           "steps": []}
    step(scn, "Buat item PRD prd-002 'Checkout' (fitur: payment-api)",
         ["python3", str(SCRIPTS / "prd.py"), "create", "--id", "prd-002",
          "--title", "Checkout", "--owner", "owner",
          "--source", "docs/prd.md#checkout", "--features", "payment-api",
          "--prd-dir", "team/prd"], d, "pass")
    step(scn, "Assistant klaim payment-api, terikat ke prd-002",
         ["python3", str(SCRIPTS / "claim.py"), "claim", "--task", "t-104",
          "--title", "Buat payment-api", "--owner", "assistant",
          "--files", "src/payment.py", "--prd", "prd-002",
          "--claims-dir", "team/claims", "--roster", "team/roster",
          "--config", "team/config.json"], d, "pass")
    step(scn, "Coba loncat planned → done langsung (tanpa lewat in_progress)",
         ["python3", str(SCRIPTS / "prd.py"), "status", "--id", "prd-002",
          "--to", "done", "--by", "owner", "--reason", "selesai",
          "--prd-dir", "team/prd"], d, "fail",
         "State machine: planned harus lewat in_progress dulu; done/cancelled itu terminal.")
    step(scn, "Gerakkan prd-002 ke in_progress (kerja dimulai)",
         ["python3", str(SCRIPTS / "prd.py"), "status", "--id", "prd-002",
          "--to", "in_progress", "--by", "owner", "--reason", "kerja dimulai (t-104)",
          "--prd-dir", "team/prd"], d, "pass")
    step(scn, "Owner coba tandai prd-002 DONE (payment-api belum terdaftar)",
         ["python3", str(SCRIPTS / "prd.py"), "status", "--id", "prd-002",
          "--to", "done", "--by", "owner", "--reason", "selesai",
          "--prd-dir", "team/prd"], d, "fail",
         "Done-gate: fitur yang dirujuk harus terdaftar dulu.")
    file_action(scn, "Assistant menulis src/payment.py (kerja nyata)",
                "src/payment.py",
                "def charge(amount, method):\n    \"\"\"Terima pembayaran, kembalikan status order.\"\"\"\n    return {\"status\": \"paid\", \"amount\": amount, \"method\": method}\n", d)
    step(scn, "Daftarkan payment-api sebagai experimental",
         ["python3", str(SCRIPTS / "features.py"), "register", "--id", "payment-api",
          "--name", "Payment API", "--does", "Terima pembayaran, kembalikan status order",
          "--entry", "src/payment.py:charge", "--files", "src/payment.py",
          "--owner", "assistant", "--keywords", "payment", "bayar", "checkout",
          "--status", "experimental", "--breaking", "false",
          "--features-dir", "team/features", "--repo-root", "."], d, "pass")
    step(scn, "Owner tandai prd-002 DONE (fitur sudah terdaftar, tapi masih experimental)",
         ["python3", str(SCRIPTS / "prd.py"), "status", "--id", "prd-002",
          "--to", "done", "--by", "owner", "--reason", "selesai",
          "--prd-dir", "team/prd"], d, "pass",
         "status hanya memeriksa keterdaftaran — yang memeriksa 'stable' adalah validate. DONE lolos di sini, tapi belum bersih.")
    step(scn, "Validasi: item done tapi fitur experimental + klaim t-104 masih aktif",
         ["python3", str(SCRIPTS / "validate.py"), "team"], d, "fail",
         "Gerbang kedua: validate MENOLAK item done yang fiturnya belum stable, dan klaim yang masih menempel di item done.")
    step(scn, "Assistant lepas klaim t-104 (tidak ada lagi yang dikerjakan di item done)",
         ["python3", str(SCRIPTS / "claim.py"), "release", "--task", "t-104",
          "--by", "assistant", "--claims-dir", "team/claims"], d, "pass")
    step(scn, "Promosikan payment-api ke stable (direview owner)",
         ["python3", str(SCRIPTS / "features.py"), "promote", "--id", "payment-api",
          "--by", "assistant", "--reviewer", "owner",
          "--reason", "dicek owner, jalan", "--features-dir", "team/features"],
         d, "pass")
    step(scn, "Validasi ulang: bersih — PRD done, fitur stable, klaim dilepas",
         ["python3", str(SCRIPTS / "validate.py"), "team"], d, "pass",
         "Dua lapis gerbang: status cegah 'done' tanpa fitur terdaftar, validate cegah 'done' tanpa fitur stable.")
    validate_end(scn, d)
    return scn


# ---------------------------------------------------------------- S4: cancel + git
def s4():
    base = WORK / "s4-batal"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    git(["init", "--bare", "-b", "main", "remote.git"], base)
    git(["clone", "-q", "remote.git", "work-a"], base)
    git(["clone", "-q", "remote.git", "work-b"], base)
    for w in ("work-a", "work-b"):
        git(["config", "user.email", "sim@theroster.test"], base / w)
        git(["config", "user.name", "Sim"], base / w)
    a, b = base / "work-a", base / "work-b"
    shutil.copytree(STARTER, a, dirs_exist_ok=True)
    git(["add", "-A"], a)
    git(["commit", "-q", "-m", "starter"], a)
    git(["push", "-q", "origin", "main"], a)
    git(["pull", "-q", "origin", "main"], b)
    scn = {"id": "s4", "title": "Fitur dibatalkan di tengah jalan",
           "team": "Tim toko online (2 AI + owner)",
           "story": "Assistant lagi garap monitor harga. Owner memutuskan fitur "
                    "itu dibatalkan — diganti layanan luar. Pembatalan harus "
                    "sampai ke semua anggota tim, bukan cuma yang membatalkan.",
           "tanpa_roster": "Owner batalkan fitur di chat. Assistant tidak baca, "
                           "lanjut garap, push — kerjaan seminggu jadi sampah, "
                           "dan tidak ada yang tahu sampai review.",
           "steps": []}
    claim = ["python3", str(SCRIPTS / "claim.py"), "claim",
             "--claims-dir", "team/claims", "--roster", "team/roster",
             "--config", "team/config.json"]
    step(scn, "[work-a] Assistant klaim monitor harga (t-105)",
         claim + ["--task", "t-105", "--title", "Tambah alert diskon ke monitor",
                  "--owner", "assistant", "--files", "scripts/monitor_harga.py"],
         a, "pass")
    rc, out = git(["add", "-A"], a); git(["commit", "-q", "-m", "claim t-105"], a)
    git(["push", "-q", "origin", "main"], a)
    git(["pull", "-q", "origin", "main"], b)
    step(scn, "[work-b] Owner BATALKAN price-monitor (alasan tercatat)",
         ["python3", str(SCRIPTS / "features.py"), "cancel", "--id", "price-monitor",
          "--by", "owner", "--reason", "Diganti layanan luar, monitor harga pindah",
          "--features-dir", "team/features"], b, "pass",
         "Cancel melepas klaim aktif yang menyentuh file fitur itu — di push yang sama.")
    rc, out = git(["add", "-A"], b); git(["commit", "-q", "-m", "cancel price-monitor"], b)
    git(["push", "-q", "origin", "main"], b)
    step(scn, "[work-a] Assistant cek remote sebelum push (sync_check)",
         ["python3", str(SCRIPTS / "sync_check.py"), "team/features", "team/claims",
          "--remote", "origin", "--branch", "main",
          "--files", "scripts/monitor_harga.py", "--owner", "assistant"],
         a, "fail",
         "Remote sudah membatalkan fitur yang klaim lokal sentuh — push DITAHAN, kerja dihentikan.")
    git(["pull", "-q", "origin", "main"], a)
    step(scn, "[work-a] Cek klaim setelah tarik remote: t-105 sudah dilepas",
         ["python3", str(SCRIPTS / "claim.py"), "check", "team/claims",
          "--roster", "team/roster", "--config", "team/config.json"], a, "pass")
    scn["final_state"] = state(a)
    return scn


# ---------------------------------------------------------------- S5: kontrak
def s5():
    d = fresh("s5-kontrak")
    scn = {"id": "s5", "title": "Kontrak diganti seenaknya",
           "team": "Tim bangun website",
           "story": "login-api adalah kontrak: banyak fitur bergantung padanya. "
                    "Assistant mau mengganti entry-nya — sistem menuntut alasan "
                    "dan bump major, lalu memberi tahu semua yang bergantung.",
           "tanpa_roster": "Entry login diganti diam-diam. Semua pemakai rusak "
                           "di runtime — ketahuannya pas pelanggan tidak bisa login.",
           "steps": []}
    login_py = (d / "src/auth/login.py").read_text()
    file_action(scn, "Assistant menulis fungsi login_v2 di src/auth/login.py",
                "src/auth/login.py",
                login_py + "\n\ndef login_v2(email, password):\n    \"\"\"Versi baru: kembalikan objek sesi, bukan token string.\"\"\"\n    return {\"session\": {\"email\": email}}\n", d)
    step(scn, "Ganti entry kontrak TANPA alasan dan TANPA bump major",
         ["python3", str(SCRIPTS / "features.py"), "improve", "--id", "login-api",
          "--by", "assistant", "--entry", "src/auth/login.py:login_v2",
          "--breaking", "false", "--features-dir", "team/features",
          "--repo-root", "."], d, "fail",
         "Kontrak tidak boleh berubah diam-diam: butuh --bump major + --contract-change.")
    step(scn, "Ganti entry DENGAN --bump major + --contract-change",
         ["python3", str(SCRIPTS / "features.py"), "improve", "--id", "login-api",
          "--by", "assistant", "--bump", "major",
          "--entry", "src/auth/login.py:login_v2",
          "--contract-change", "login_v2 kembalikan objek sesi, bukan token string — pemakai lama harus adaptasi",
          "--breaking", "true",
          "--breaking-notes", "login_v2 kembalikan objek sesi, bukan token string — pemakai lama harus adaptasi",
          "--note", "Entry login diganti ke login_v2 (kontrak berubah)",
          "--features-dir", "team/features", "--repo-root", "."], d, "pass",
         "Perubahan tercatat di changes + breaking notes. login-api sekarang v2.0.0.")
    file_action(scn, "Assistant menulis src/checkout.py (kerja nyata)",
                "src/checkout.py",
                "def checkout(cart):\n    \"\"\"Checkout memakai login-api untuk sesi.\"\"\"\n    return {\"total\": sum(cart)}\n", d)
    step(scn, "Daftarkan checkout-api yang bergantung ke login-api@2.0.0",
         ["python3", str(SCRIPTS / "features.py"), "register", "--id", "checkout-api",
          "--name", "Checkout API", "--does", "Checkout memakai login-api untuk sesi",
          "--entry", "src/checkout.py:checkout", "--files", "src/checkout.py",
          "--owner", "assistant", "--keywords", "checkout", "bayar",
          "--depends-on", "login-api@2.0.0", "--status", "experimental",
          "--breaking", "false", "--features-dir", "team/features",
          "--repo-root", "."], d, "pass")
    step(scn, "login-api di-bump major lagi (v3.0.0) — dependensi tertinggal",
         ["python3", str(SCRIPTS / "features.py"), "improve", "--id", "login-api",
          "--by", "owner", "--bump", "major",
          "--contract-change", "tambah field device di sesi",
          "--breaking", "true",
          "--breaking-notes", "field device baru di sesi — pemakai lama harus kirim device",
          "--note", "Tambah field device di sesi",
          "--features-dir", "team/features", "--repo-root", "."], d, "pass")
    step(scn, "Validasi: checkout-api ketinggalan major — PERINGATAN reverify",
         ["python3", str(SCRIPTS / "validate.py"), "team"], d, "pass",
         "Validate tidak gagal, tapi teriak: dependensi sudah major di depan, reverify dulu.")
    step(scn, "Reverify checkout-api ke versi dependensi terbaru",
         ["python3", str(SCRIPTS / "features.py"), "reverify", "--id", "checkout-api",
          "--by", "assistant", "--features-dir", "team/features",
          "--repo-root", "."], d, "pass")
    step(scn, "Validasi ulang: bersih",
         ["python3", str(SCRIPTS / "validate.py"), "team"], d, "pass")
    scn["final_state"] = state(d)
    return scn


# ---------------------------------------------------------------- S6: orang asing
def s6():
    d = fresh("s6-orang-asing")
    scn = {"id": "s6", "title": "Orang asing ngaku teman tim",
           "team": "Tim mana pun — inti skill ini",
           "story": "AI baru bernama ghost-ai mau ikut kerja. Roster adalah "
                    "satu-satunya identitas di sistem — kalau namanya tidak "
                    "ada di daftar, dia bukan teman tim, apa pun yang dia klaim.",
           "tanpa_roster": "AI asing ikut campur tanpa jejak: tidak ada yang "
                           "tahu siapa dia, siapa yang bertanggung jawab, atau "
                           "bagaimana menghubunginya kalau kerjaannya rusak.",
           "steps": []}
    claim = ["python3", str(SCRIPTS / "claim.py"), "claim",
             "--claims-dir", "team/claims", "--roster", "team/roster",
             "--config", "team/config.json"]
    step(scn, "ghost-ai (tidak ada di roster) coba klaim kerjaan",
         claim + ["--task", "t-999", "--title", "Kerja gelap",
                  "--owner", "ghost-ai", "--files", "src/ghost.py"], d, "fail",
         "Bukan anggota roster = bukan teman tim. Klaim DITOLAK.")
    step(scn, "Cek roster yang ada: valid",
         ["python3", str(SCRIPTS / "check_roster.py"), "team/roster"], d, "pass")
    file_action(scn, "Owner daftarkan ghost-ai — tapi verification-nya 'self-asserted'",
                "team/roster/ghost-ai.json",
                json.dumps({"id": "ghost-ai", "name": "Ghost AI", "type": "ai-agent",
                            "role": "agen baru — belum jelas",
                            "can_do": ["testing"], "contact": "chat",
                            "status": "active", "last_verified": "2026-11-14",
                            "added_by": "ghost-ai", "added_in_pr": "#0",
                            "verification": "self-asserted"}, indent=2), d)
    step(scn, "Cek roster: entri self-asserted DITOLAK",
         ["python3", str(SCRIPTS / "check_roster.py"), "team/roster"], d, "fail",
         "Roster hanya sah kalau manusia yang menyetujui — klaim sendiri tidak dihitung.")
    file_action(scn, "Owner perbaiki: verification jadi human-approved, added_by owner",
                "team/roster/ghost-ai.json",
                json.dumps({"id": "ghost-ai", "name": "Ghost AI", "type": "ai-agent",
                            "role": "agen baru — testing",
                            "can_do": ["testing"], "contact": "chat",
                            "status": "active", "last_verified": "2026-11-14",
                            "added_by": "owner", "added_in_pr": "#9",
                            "verification": "human-approved"}, indent=2), d)
    step(scn, "Cek roster ulang: lolos",
         ["python3", str(SCRIPTS / "check_roster.py"), "team/roster"], d, "pass")
    step(scn, "ghost-ai klaim kerjaan — sekarang dia teman tim",
         claim + ["--task", "t-999", "--title", "Testing fitur checkout",
                  "--owner", "ghost-ai", "--files", "src/ghost.py"], d, "pass",
         "Jalurnya jelas: manusia menyetujui dulu, baru AI boleh kerja.")
    validate_end(scn, d)
    return scn


def main():
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    scenarios = [s1(), s2(), s3(), s4(), s5(), s6()]
    total_steps = sum(len(s["steps"]) for s in scenarios)
    gates_ok = sum(1 for s in scenarios for st in s["steps"] if st.get("gate_ok"))
    doc = {
        "generated_by": "run_simulations.py — script ASLI TheRoster v2.2.0",
        "repo": "Rifkiend/TheRoster",
        "scenarios": scenarios,
        "summary": {"scenarios": len(scenarios), "steps": total_steps,
                    "gates_ok": gates_ok, "gates_failed": total_steps - gates_ok},
    }
    (OUT / "transcripts.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"\nDONE: {len(scenarios)} skenario, {total_steps} langkah, "
          f"gate sesuai harapan: {gates_ok}/{total_steps}")
    print("->", OUT / "transcripts.json")


if __name__ == "__main__":
    main()
