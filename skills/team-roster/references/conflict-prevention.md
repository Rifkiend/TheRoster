# Anti-tabrakan — team-roster v2

## Masalahnya

Roster v2 hanya menjawab **SIAPA** tim lo. Roster tidak menjawab **SIAPA LAGI NGAPAIN**.

Dua AI yang tidak terhubung satu sama lain tidak bisa "tahu" secara ajaib kalau AI lain sedang edit file yang sama. Tanpa sesuatu yang dibagi, mereka pasti tabrakan: edit file yang sama, overwrite kerjaan, merge conflict.

## Jawabannya: klaim sebagai file, git sebagai alat sinkron

Tidak ada server, tidak ada biaya, tidak ada akun baru. Alat sinkronnya adalah file yang sama-sama dibaca dan ditulis lewat git — dan di v2, **satu klaim = satu file**:

- `team/roster/<id>.json` — siapa timnya (satu file per orang).
- `team/claims/<task-id>.json` — siapa lagi ngapain SEKARANG (satu file per klaim).
- `team/features/<id>.json` — fitur apa yang sudah ada (satu file per fitur).
- `team/config.json` — aturan tim sebagai data (remote kanonis, TTL, kuota, batas kesegaran branch).
- `team/index.json` — index yang digenerate dari file-file di atas, jangan pernah diedit manual.
- `team/events.jsonl` — log event append-only (audit trail).

Dua agent yang mengklaim task berbeda sekarang menyentuh **file yang berbeda** dan tidak pernah conflict. Conflict hanya bisa terjadi kalau dua agent mengedit record yang sama — dan itu memang kasus yang seharusnya jadi conflict. Inilah perubahan paling berdampak di v2 (audit §2.1): file monolitik v1 (`team/work.json`) membuat contention tumbuh kuadrat dengan jumlah penulis; sharding membuatnya O(1) per record.

### Yang masih dibagi (dan bagaimana conflict-nya ditangani)

Sharding menghilangkan conflict di file klaim, tapi dua file masih dibagi semua agent:

- **`team/events.jsonl` (append-only).** Setiap mutasi menambah satu baris. File yang di-copy dari contoh membawa `team/.gitattributes` dengan `events.jsonl merge=union` — driver merge bawaan git yang mengambil baris dari kedua sisi. Untuk file append-only satu-record-per-baris, itu aman, dan hanya di sini union boleh dipakai. Jangan pernah pakai `merge=union` untuk file record. Kalau rebase tetap conflict di log ini, resolve manual: ambil semua baris dari kedua sisi (dedup kalau ada duplikat), lanjutkan rebase.
- **`team/index.json` (generated).** Ini cache lokal, bukan sumber kebenaran — karena itu file ini **tidak di-commit** (lihat `team/.gitignore` di contoh tree). Dua agent tidak pernah conflict di file ini karena file ini tidak ada di remote. Setelah merge/rebase apa pun, refresh cache lokal lo: `python3 scripts/rebuild_index.py team`. Kalau index lo ketinggalan, yang rusak cuma tampilan ringkasnya — record aslinya tetap benar.

## Cara kerja — 5 langkah, tanpa kecuali

1. **Fetch dulu, jangan `git pull` mentah.** `git fetch origin` + `git rebase origin/main` (atau merge eksplisit). Jangan pernah bare `git pull` — pull bisa membuat merge commit atau me-rewrite commit lo dan membatalkan cek yang baru saja lo jalankan.
2. **Baca klaim yang ada.** `python3 skills/team-roster/scripts/claim.py list team/claims`. Cari file, task, atau area yang mau lo kerjakan di klaim yang statusnya `active` dan belum kedaluwarsa. Kalau ada pemilik lain, JANGAN mulai. Pilih kerjaan lain, atau tunggu.
3. **Klaim dulu — membuat file klaim ADALAH klaimnya.** Jalankan `scripts/claim.py claim` dengan task, file, dan owner lo, lalu **commit + push file klaim itu sendiri, sendirian, sebelum mulai kerja**:
   ```bash
   python3 skills/team-roster/scripts/claim.py claim --task t-002 --title "Kerjakan checkout" --owner <your-roster-id> --files src/checkout.py tests/test_checkout.py --branch feat/checkout
   git add team/claims/t-002.json && git commit -m "claim: t-002 kerjakan checkout [claim #1]" && git push --atomic
   ```
   Push klaim duluan adalah kuncinya: AI lain baru tahu lo sedang kerja setelah klaim lo sampai di remote. Klaim ini push-nya sendiri — jangan dicampur dengan commit kerjaan.
4. **Kerja di branch sendiri.** Satu task = satu branch, misal `feat/login-api`. Jangan edit langsung di `main`. Jangan sentuh file di luar klaim lo. Kalau ternyata butuh file lain, klaim dulu file itu.
5. **Lepas klaim saat selesai.** `scripts/claim.py done --task t-002` (atau `release`), lalu commit + push. Merge lewat pull request, bukan push langsung ke `main`.

## Aturan klaim (v2)

- **Satu task = satu pemilik.** Untuk kode, spesialisasinya: satu file = satu pemilik dalam satu waktu. Satu task boleh pegang beberapa file (maks 10), selama semua file itu diklaim.
- **Klaim hanya berisi path file persis — bukan direktori, bukan glob.** Klaim direktori/glob ditolak. Klaim yang path-nya adalah prefix dari, atau punya prefix berupa, path klaim aktif lain juga ditolak. Perbandingan path memakai bentuk lowercase (normalisasi sebelum bandingkan, ejaan asli tetap disimpan). File yang baru dibuat di dalam direktori yang diklaim tetap **tidak diklaim** sampai diklaim eksplisit.
- **Klaim kedaluwarsa 2 jam** setelah `started_at` (`claim_ttl_minutes: 120` di `team/config.json`), tapi kedaluwarsa itu **advisory dengan grace window 15 menit**: klaim baru boleh diambil alih kalau `now > expires_at + 15 menit` menurut jam si pengambil **DAN** `heartbeat_at`-nya juga sudah basi. Skew jam kecil tidak boleh membalik klaim yang masih hidup.
- **Heartbeat:** klaim yang berjalan lebih dari 30 menit wajib di-heartbeat tiap 30 menit (`heartbeat_minutes: 30`) — push pembaruan `heartbeat_at` (dan `handoff_note`). Kalau 30 menit tanpa heartbeat, klaim boleh ditandai `stalled` oleh agent lain (dengan `stalled_by` + `stall_reason` tercatat — bukan dihapus diam-diam) supaya manusia bisa audit. Klaim yang selesai ditandai `done`; yang ditinggal ditandai `abandoned`; yang dilepas `released` (dengan `released_by` kalau yang melepas bukan pemiliknya).
- **Kuota:** maks 5 klaim aktif per agent, maks 10 file per klaim. Siapa pun boleh menandai klaim sebagai abusive; 3 flag dari agent berbeda, atau 1 flag dari anggota roster manusia, akan melepas klaim itu.
- **Klaim hanya boleh dibuat oleh orang yang ada di `team/roster/` dengan status `active`.** `claim.py` mengecek ini.
- **Jangan klaim seluruh folder atau seluruh repo.** Klaim yang terlalu luas = memblokir semua orang.
- **Non-code:** `scope` klaim bisa `files`, `artifact`, atau `area`, dengan `work_type` `code|design|writing|data|ops`. Contoh: `scope: {"type": "artifact", "name": "produk: isku-kunci-sok"}`, `scope: {"type": "area", "name": "slot: threads 2026-11-15 pagi"}`. Invariannya tetap sama: satu task = satu pemilik.
- **Urutan (Lamport + claim_seq):** tiap file record punya counter `lamport`; tiap penulis set `lamport = existing + 1` (record baru: 1). Kalau dua klaim bentrok, yang menang ditentukan oleh `(lamport, agent id)` — bukan oleh timestamp dinding, karena jam antar mesin bisa miring. `claim_seq` monotonik per file yang diklaim, disalin ke trailer commit `[claim #N]` di setiap commit yang menyentuh file klaim itu, supaya holder yang bangkit setelah expiry (klaimnya sudah diambil orang lain) ketahuan sebagai conflict, bukan overwrite diam-diam.
- **Semua timestamp UTC ISO-8601 berakhiran `Z`.** Timestamp tanpa zona ditolak hook.
- **Branch freshness:** klaim hanya valid selama branch-nya ≤20 commit di belakang `origin/main` atau ≤7 hari (`branch_freshness_commits` / `branch_freshness_days` di config). `base_commit` dicatat di klaim supaya basi-nya bisa diukur. Lewat ambang = warning; lewat 2× ambang = push diblokir sampai rebase + cari ulang registry di `main` yang segar.
- **Handoff:** klaim dipindah HANYA dengan membuat `team/handoffs/<id>.json` (`from`, `to`, `claim_id`, `reason`, `state_summary`) — jangan pernah edit field `owner_id` klaim langsung.
- **Kepemilikan record:** agent hanya boleh membuat/mengubah/menghapus record yang `owner`-nya dirinya sendiri — kecuali melepas klaim yang sudah expired/stalled (dicatat sebagai `released_by`) dan perubahan roster (butuh approval manusia). Mengedit klaim orang lain untuk "mencuri" adalah pelanggaran.
- **No self-registration:** entri roster yang ditambahkan di push yang sama dengan kerjaan yang diotorisasi entri itu ditolak.
- `team/claims/` bukan tempat diskusi. Hanya fakta: siapa, task apa, file apa, dari kapan sampai kapan, branch apa, status apa.

## Kalau dua AI klaim barengan?

Itu bisa terjadi — dua AI fetch di saat yang sama, sama-sama lihat file kosong, sama-sama klaim. Git yang jadi wasitnya:

- Yang push duluan menang. Yang push belakangan **ditolak** (`non-fast-forward`).
- Yang ditolak WAJIB fetch lagi, baca ulang klaim, dan kalau klaimnya ternyata bentrok, batalkan kerjaannya. Jangan `--force push`. Jangan timpa klaim orang.
- Karena klaim adalah file sendiri, yang kalah rugi kecil: klaimnya push duluan **sebelum** kerjaan apapun ditulis, jadi yang hilang cuma klaimnya, bukan sejam kerjaan.

## Urutan pre-push atomik-ish

Jangan ada jeda antara cek dan push. Urutan yang tepat, dalam satu gerakan:

```
git fetch origin → rebase/merge eksplisit ke origin/main → re-cek klaim/fitur terhadap origin/main yang baru di-fetch → push segera (utamakan --atomic) → kalau ditolak, mulai lagi dari fetch
```

- `git push --atomic` (GitHub mendukung; bare git server mungkin tidak) — semua ref ter-update atau tidak sama sekali.
- Jangan pernah sisipkan `git pull` di antara cek dan push.
- Klaim di-push sendiri dulu, sebelum commit kerjaan ada. Jendela TOCTOU-nya jadi hanya sebesar klaim itu.
- `sync_check.py` berjalan sebagai bagian dari hook — ia membandingkan dengan `origin/main` yang baru di-fetch.

## Event log, mailbox, handoff

- **`team/events.jsonl`** — append-only, satu baris per event (`claim_created`, `claim_released`, `feature_registered`, `status_changed`, …) dengan `lamport`, `actor`, `base_commit`. Ini audit trail: kalau ada yang meracuni `team/`, dari log ini bisa direkonstruksi siapa, apa, kapan, di PR mana.
- **`team/mailbox/<agent-id>.jsonl`** — pesan append-only antar teammate ("tolong review PR saya", "saya lepas klaim lo yang stalled"). **Cek mailbox lo di awal sesi.** Ini asynchronous dan poll-based — bukan notifikasi real-time, dan skill ini bilang begitu secara jujur.
- **`team/handoffs/<id>.json`** — transfer klaim yang eksplisit (lihat "Handoff" di atas).

## Kalau bukan coding?

Prinsipnya sama, medianya beda — klaim `scope`-nya saja yang berubah (lihat "Aturan klaim"):

- **Tim konten / affiliate:** yang diklaim bukan file, tapi produk/postingan: `scope: {"type": "artifact", "name": "produk: isku-kunci-sok"}` dan `scope: {"type": "area", "name": "slot: threads 2026-11-15 pagi"}`.
- **Tim PKL / lapangan:** yang diklaim adalah tugas dan alat: `scope: {"type": "artifact", "name": "alat: modem orbit"}`. Satu alat = satu peminjam.
- **Freelance:** yang diklaim adalah laporan klien: `scope: {"type": "artifact", "name": "laporan: klien X — november"}`.

Lihat `references/examples.md` — tiap contoh roster sudah punya pasangan klaim versi pekerjaan itu.

## Yang TIDAK dijamin skill ini

- **Bukan real-time.** AI lain baru tahu setelah lo push klaim dan mereka fetch/pull. Kalau ada yang tidak pull, mereka tetap bisa tabrakan. Skill ini mengurangi tabrakan jadi langka, bukan nol — seperti layaknya traffic light, bukan tembok.
- **Bukan kunci file di OS.** Klaim adalah perjanjian sosial yang ditegakkan oleh script + hook + CI, bukan lock di filesystem. Kalau ada yang sengaja melanggar, file tetap bisa diedit. Yang skill ini berikan adalah: pelanggaran ketahuan cepat (di push dan di `check`), bukan dicegah secara fisik.
- **Bukan ganti code review / CI.** Tetap merge lewat PR, tetap jalankan test. Klaim mencegah dua orang edit bersamaan; review mencegah hasil yang salah.
- **Tidak ada atomisitas kecuali ref update di push.** Fetch → cek → push adalah check-then-act (TOCTOU, CWE-367): dunia bisa berubah di antara langkah-langkahnya. Sharding + klaim-sebagai-file + urutan pre-push mempersempit jendela itu sampai hitungan detik, tapi tidak menutupnya. Penutupan sejati butuh server (pre-receive hook / merge queue yang mengecek ulang terhadap `main` saat ini di saat merge). Lihat README, bagian "Known limits".
- **Tidak ada fencing sejati.** `claim_seq` di trailer commit mengubah overwrite diam-diam menjadi conflict yang terdeteksi — tapi trailer bisa dipalsukan manusia. Fencing yang ditegakkan di tempat tulisan mendarat butuh server.
- **Tidak ada presence real-time.** `last_seen_at` + heartbeat memberi tahu "terakhir terlihat kapan", bukan "online sekarang". Kalau tim lo butuh itu, butuh server.
- **Tidak ada identitas kriptografis.** `owner_id` adalah string yang ditegaskan sendiri. Signed commits + `owner_key_fingerprint` di roster memberi atribusi, bukan otentikasi — registry kunci yang sesungguhnya butuh server. Lihat README, bagian "Known limits".

## Cek cepat sebelum push

`hooks/pre-push` v2 menjalankan semua cek:

```bash
python3 skills/team-roster/scripts/doctor.py
python3 skills/team-roster/scripts/check_roster.py team/roster
python3 skills/team-roster/scripts/claim.py check team/claims
python3 skills/team-roster/scripts/features.py check team/features
python3 skills/team-roster/scripts/validate.py team
python3 skills/team-roster/scripts/sync_check.py team/features team/claims
```

`claim.py check` gagal kalau: ada klaim ganda di file yang sama (dua pemilik aktif), ada klaim dari orang yang tidak ada di roster / tidak aktif, ada klaim yang formatnya rusak, kuota terlampaui, atau ada klaim yang path-nya overlap (prefix) dengan klaim aktif lain. Lepas atau perbaiki klaimnya, baru push.

**`sync_check.py` adalah penutup titik buta:** ia fetch remote dan gagal kalau versi fitur di remote lebih baru dari versi lokal lo, atau ada klaim aktif di remote atas file yang lo sentuh. Penjelasan lengkapnya ada di `references/feature-registry.md`, bagian "Titik buta AI A — dan cara menutupnya".
