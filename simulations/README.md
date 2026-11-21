# Simulasi TheRoster — 13 skenario bervariasi

Simulasi ini menjalankan **script asli** skill TheRoster v2.8.0 (bukan mock)
di salinan segar starter-repo, dan merekam perintah persis, exit code, dan
output asli tiap langkah.

Jalankan ulang: `python3 run_simulations.py`
Hasil: `transcripts.json` (494 langkah, semua gate sesuai harapan pada run terakhir).

| ID | Skenario | Yang dibuktikan |
|----|----------|-----------------|
| s1 | Tabrakan: dua AI garap file yang sama | Klaim kedua ditolak; lepas klaim → giliran jalan |
| s2 | Cari dulu sebelum bangun | Search → reuse; id duplikat ditolak; klaim pakai --reserves-feature |
| s3 | PRD dibagi ke AI beda + done-gate | planned→done langsung ditolak; done-gate di status (fitur belum terdaftar); validate cegah item done yang fiturnya belum stable + klaim menempel |
| s4 | Fitur dibatalkan di tengah jalan | Cancel melepas klaim aktif; sync_check menahan push dari clone yang belum tahu |
| s5 | Kontrak diganti seenaknya | Ganti entry tanpa --contract-change ditolak; --breaking true butuh --breaking-notes; bump butuh --note; validate beri peringatan reverify; reverify bersihkan |
| s6 | Orang asing ngaku teman tim | Klaim oleh non-roster ditolak; roster self-asserted ditolak; human-approved → boleh kerja |
| s7 | Worktree-per-claim: dua AI garap file yang sama, tanpa overwrite | `--worktree` dengan mode off ditolak; dua klaim worktree di worktree berbeda boleh menutupi file yang sama; klaim worktree terlihat dari fresh clone (papan tidak hilang); mutasi dari dalam worktree ditolak; pembacaan dari dalam worktree dialihkan ke main; kedua edit merge satu per satu bersih ke main; base basi 25 commit → sync_check gagal minta rebase; setelah rebase lolos |
| s8 | Worktree-gate fixes (v2.3.1) | Canonical worktree paths; deleted-worktree gate (branch-aware); dirty-worktree gate; branch-mismatch di claim check |
| s9 | Signed identity + creation order + lifetime + mailbox trust (v2.4.0; mailbox §C per v2.5.0) | Commit team/ tanpa tanda tangan ditolak; tanda tangan kunci salah ditolak; created_seq memutuskan "duluan"; klaim mati di max_lifetime_hours walau heartbeat segar; mailbox: pesan manusia tanpa sig valid FAIL (pesan asli "STOP semua kerja hari ini..." tertangkap), pesan bertanda tangan lolos, sig kunci salah gagal |
| s10 | Hardening: close every remaining hole (v2.5.0) | Deletion record team/ FAIL; trailer [claim #N] ditegakkan (tanpa trailer / nomor salah FAIL, merge commit dilewati); event malformed/dangling/actor asing FAIL; ghost claim tidak blocking + warning; siklus A↔B dan 3-cycle FAIL di check/validate/reverify + ditolak di register/improve; history rewrite terdeteksi sync_check + validate via last-known-main-SHA; heartbeat/started_at dari masa depan FAIL, expires_at 2 jam ke depan sah; klaim atas team/ ditolak; nama tampilan ganda FAIL |
| s11 | Fitur Orca (v2.6.0): fan-out, gates, receipts, reviews | Fan-out: create max-1 FAIL, 3 member di worktree masing-masing, sibling tanpa worktree FAIL, member ke-4 FAIL, decide prematur / winner bukan member / winner failed / decider owner winner FAIL, decide benar melepas loser `fanout:<winner>`, cancel melepas `fanout-cancel:<by>`; Gates: klaim ditolak selagi gate open, resolve oleh owner subject FAIL, mid-work gate FAIL di check+validate, waive oleh agent FAIL, resolve --review approve LOLOS / request-changes FAIL; Receipts: done tanpa --outcome FAIL, done oleh agent lain FAIL, done human tanpa --key FAIL, receipt failed tetap menutup klaim tapi PRD in_progress, receipt palsu (claim_seq) FAIL, receipt bertanda tangan yang diubah FAIL "invalid sig"; Reviews: PRD done ditolak selagi review terakhir request-changes, approve terbaru membersihkan, review oleh owner subject FAIL, anotasi baris 0 FAIL, delivery mailbox hanya id + kata verdict, follow-up hanya untuk request-changes; validate + doctor check 11 LOLOS end-to-end |
| s12 | Tutup lubang (v2.7.0): behind-skew, created_seq ganda, entry line citation | Behind-skew: klaim normal LOLOS (`created_base_commit == HEAD`); `started_at` 3 jam sebelum base FAIL di claim check + validate; release + re-claim id yang sama memperbaiki; klaim tanpa base tidak false-FAIL; dalam grace 15 menit LOLOS. Duplikat seq: dua klaim aktif seq 77 FAIL di check + validate; release satu → LOLOS. Line citation: register/improve tanpa `#line` DITOLAK, `#line` salah DITOLAK, `#line` benar LOLOS; entry lama tanpa baris hanya warning (grandfather), reverify menambah/meng-update baris saat simbol pindah; check GAGAL saat baris dikutip sudah tidak benar |
| s13 | Graphyfy (v2.8.0): build graph, query slice, basi FAIL, ambang LOLOS, graph ke-commit | Tanpa graph di atas ambang: validate LOLOS + warning "above the graph threshold". Build: 15 file, commit_sha == HEAD, threshold_note None, index.json (gitignored) tidak dipetakan. Regresi bug `.github/` (startswith `.git` v0.1): `.github/workflows/ci.yml` + `.github/ISSUE_TEMPLATE/bug.md` dipetakan (17 file) — jalur git DAN fallback non-git. Query: simbol login → `src/auth/login.py` baris 14, owner `owner` via t-001, `--dir src` → 1 file, label "terakhir diketahui". Graph basi: commit kerja → validate FAIL "STALE (basi)" + `graph.py check` FAIL "GRAPH BASI"; rebuild → LOLOS. Ambang: repo 4 file/400 baris tanpa graph LOLOS + "below threshold"; angka dikunci `below_threshold` ((4,400) bawah, (16,355) atas, batas (5,499)/(4,500) atas). Commit graph saja TIDAK membuat basi (aturan diff); graph terlacak + ada di origin/main |

Baterai kasus langka: `rerun_rare_cases.py` menjalankan ulang 15 kasus dari
`research_notes/rare-cases-theroster-20261115/report.md` terhadap script saat
ini. Hasil v2.8.0 (`rare-cases-rerun.json`): 12 tertutup, 0 sebagian, 2 bukan lubang
(#7 skala, #12c index), 1 boundary (#11 dua repo), 0 masih lubang, 0 error —
sama persis dengan v2.7.0 (tidak ada regresi).
Proof before/after: `before_after_v270.py` — fixture yang sama lolos di script
v2.6.0 murni dan gagal di script v2.7.0 dengan error baru (9/9 sesuai harapan).

Catatan jujur dari run: tiga perilaku script yang awalnya salah diduga
dan diperbaiki di skenario (bukan di script): `prd.py status --to done`
hanya memeriksa keterdaftaran fitur (yang memeriksa stable adalah
`validate.py`); `register`/`improve` mewajibkan `--breaking true|false`
(dan `--breaking-notes` kalau true, `--note` kalau bump) dan `--files` untuk
kind=code; PRD tidak bisa loncat planned → done.

Catatan jujur dari v2.5.0: dua desain yang dikoreksi saat pengujian, bukan
sesudah rilis — (1) `expires_at` TIDAK ikut gate futureness clock-skew
(klaim baru memang kedaluwarsa 2 jam lagi; gate hanya untuk `started_at` /
`heartbeat_at`); (2) validasi event bersifat temporal — baris LAMA (sudah di
histori bersama) hanya dicek bentuknya, baris BARU dicek penuh; kalau tidak,
setiap ghost akan gagal validasi lewat event log-nya sendiri. Dan satu
perilaku `register`/`improve` yang ditemukan saat pengujian: keduanya
memeriksa graf dependensi KESELURUHAN, jadi fitur baru tidak bisa mendarat
di atas graf yang sudah melingkar — putuskan siklusnya dulu.
