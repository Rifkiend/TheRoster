# Simulasi TheRoster — 6 skenario bervariasi

Simulasi ini menjalankan **script asli** skill TheRoster v2.2.0 (bukan mock)
di salinan segar starter-repo, dan merekam perintah persis, exit code, dan
output asli tiap langkah.

Jalankan ulang: `python3 run_simulations.py`
Hasil: `transcripts.json` (45 langkah, semua gate sesuai harapan pada run terakhir).

| ID | Skenario | Yang dibuktikan |
|----|----------|-----------------|
| s1 | Tabrakan: dua AI garap file yang sama | Klaim kedua ditolak; lepas klaim → giliran jalan |
| s2 | Cari dulu sebelum bangun | Search → reuse; id duplikat ditolak; klaim pakai --reserves-feature |
| s3 | PRD dibagi ke AI beda + done-gate | planned→done langsung ditolak; done-gate di status (fitur belum terdaftar); validate cegah item done yang fiturnya belum stable + klaim menempel |
| s4 | Fitur dibatalkan di tengah jalan | Cancel melepas klaim aktif; sync_check menahan push dari clone yang belum tahu |
| s5 | Kontrak diganti seenaknya | Ganti entry tanpa --contract-change ditolak; --breaking true butuh --breaking-notes; bump butuh --note; validate beri peringatan reverify; reverify bersihkan |
| s6 | Orang asing ngaku teman tim | Klaim oleh non-roster ditolak; roster self-asserted ditolak; human-approved → boleh kerja |

Catatan jujur dari run: tiga perilaku script yang awalnya salah diduga
dan diperbaiki di skenario (bukan di script): `prd.py status --to done`
hanya memeriksa keterdaftaran fitur (yang memeriksa stable adalah
`validate.py`); `register`/`improve` mewajibkan `--breaking true|false`
(dan `--breaking-notes` kalau true, `--note` kalau bump); PRD tidak bisa
loncat planned → done.
