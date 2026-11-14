# Anti-tabrakan — team-roster v1.1

## Masalahnya

Roster v1.0 hanya menjawab **SIAPA** tim lo. Roster tidak menjawab **SIAPA LAGI NGAPAIN**.

Dua AI yang tidak terhubung satu sama lain tidak bisa "tahu" secara ajaib kalau AI lain sedang edit file yang sama. Tanpa sesuatu yang dibagi, mereka pasti tabrakan: edit file yang sama, overwrite kerjaan, merge conflict.

## Jawabannya: satu file kerjaan yang dibagi, git sebagai alat sinkron

Tidak ada server, tidak ada biaya, tidak ada akun baru. Alat sinkronnya adalah file yang sama-sama dibaca dan ditulis lewat git:

- `team/roster.json` — siapa timnya (v1.0).
- `team/work.json` — siapa lagi ngapain SEKARANG (v1.1, baru).

Git adalah satu-satunya penghubung antar AI yang tidak saling kenal. Aturannya: **pull dulu, klaim dulu, baru kerja, push klaimnya langsung.**

## Cara kerja — 5 langkah, tanpa kecuali

1. **Pull dulu.** `git pull` sebelum baca apapun. Kalau tidak pull, klaim yang lo baca sudah basi.
2. **Baca `team/work.json`.** Cari file atau task yang mau lo kerjakan di klaim yang statusnya `doing` dan belum kedaluwarsa. Kalau ada pemilik lain, JANGAN mulai. Pilih kerjaan lain, atau tunggu.
3. **Klaim dulu.** Jalankan `scripts/claim.py claim` dengan task, file, dan owner lo. Klaim berlaku 2 jam. Lalu **commit + push klaim itu sendiri, sebelum mulai kerja**:
   ```bash
   git add team/work.json && git commit -m "claim: t-002 kerjakan checkout" && git push
   ```
   Push klaim duluan adalah kuncinya: AI lain baru tahu lo sedang kerja setelah klaim lo sampai di remote.
4. **Kerja di branch sendiri.** Satu task = satu branch, misal `feat/login-api`. Jangan edit langsung di `main`. Jangan sentuh file di luar klaim lo. Kalau ternyata butuh file lain, klaim dulu file itu, atau lepas klaim lama.
5. **Lepas klaim saat selesai.** `scripts/claim.py release --task t-002` (atau ubah status jadi `done`), lalu commit + push. Merge lewat pull request, bukan push langsung ke `main`.

## Kalau dua AI klaim barengan?

Itu bisa terjadi — dua AI pull di saat yang sama, sama-sama lihat file kosong, sama-sama klaim. Git yang jadi wasitnya:

- Yang push duluan menang. Yang push belakangan **ditolak** (`non-fast-forward`).
- Yang ditolak WAJIB `git pull` lagi, baca ulang `work.json`, dan kalau klaimnya ternyata bentrok, batalkan kerjaannya. Jangan `--force push`. Jangan timpa klaim orang.
- Ini bukan bug, ini desainnya: tabrakan ketahuan di push klaim, bukan di akhir saat semua kerjaan sudah jadi.

## Aturan klaim

- Satu file = satu pemilik dalam satu waktu. Satu task boleh pegang beberapa file, selama semua file itu diklaim.
- Klaim kedaluwarsa otomatis 2 jam setelah `started_at`. Kalau kerjaan lo lebih lama, perpanjang dengan klaim ulang (`claim` lagi dengan task_id yang sama) — jangan diam-diam lanjut setelah kedaluwarsa.
- Klaim kedaluwarsa BUKAN izin merebut seenaknya: kalau pemilik lama masih aktif di chat/branchnya, tanya dulu. Kedaluwarsa hanya izin untuk mengambil alih kerjaan yang jelas-jelas ditinggal.
- Klaim hanya boleh dibuat oleh orang yang ada di `team/roster.json` dengan status `active`. `claim.py` mengecek ini kalau roster tersedia.
- Jangan klaim seluruh folder atau seluruh repo. Klaim yang terlalu luas = memblokir semua orang.
- `work.json` bukan tempat diskusi. Hanya fakta: siapa, task apa, file apa, dari kapan sampai kapan, branch apa, status apa.

## Kalau bukan coding?

Prinsipnya sama, medianya beda:

- **Tim konten / affiliate:** yang diklaim bukan file, tapi produk/postingan: `files: ["produk: isku-kunci-sok", "slot: threads 2026-11-15 pagi"]`. Dua orang tidak boleh menulis postingan yang sama untuk produk yang sama di slot yang sama.
- **Tim PKL / lapangan:** yang diklaim adalah tugas dan alat: `files: ["tugas: ukur sinyal mess", "alat: modem orbit"]`. Satu alat = satu peminjam.
- **Freelance:** yang diklaim adalah laporan klien: `files: ["laporan: klien X — november"]`. Klien yang sama tidak boleh dikerjakan dua orang tanpa sepengetahuan.

Lihat `references/examples.md` — tiap contoh roster sudah punya pasangan `work.json` versi pekerjaan itu.

## Yang TIDAK dijamin skill ini

- **Bukan real-time.** AI lain baru tahu setelah lo push klaim dan mereka pull. Kalau ada yang tidak pull, mereka tetap bisa tabrakan. Skill ini mengurangi tabrakan jadi langka, bukan nol — seperti layaknya traffic light, bukan tembok.
- **Bukan kunci file di OS.** `work.json` adalah perjanjian sosial yang ditegakkan oleh script + hook, bukan lock di filesystem. Kalau ada yang sengaja melanggar, file tetap bisa diedit. Yang skill ini berikan adalah: pelanggaran ketahuan cepat (di push dan di `check`), bukan dicegah secara fisik.
- **Bukan ganti code review / CI.** Tetap merge lewat PR, tetap jalankan test. Klaim mencegah dua orang edit bersamaan; review mencegah hasil yang salah.

## Cek cepat sebelum push

`hooks/pre-push` v1.1 menjalankan dua cek, bukan satu:

```bash
python3 scripts/check_roster.py team/roster.json
python3 scripts/claim.py check team/work.json
```

`claim.py check` gagal kalau: ada klaim ganda di file yang sama (dua pemilik aktif), ada klaim dari orang yang tidak ada di roster / tidak aktif, atau ada klaim yang formatnya rusak. Lepas atau perbaiki klaimnya, baru push.
