# Feature registry — team-roster v1.2

## Ide lo, dalam satu kalimat

**Jangan bikin fitur yang sudah ada. Kalau sudah ada yang bikin, pakai yang itu — jangan bikin dobel, jangan hapus yang lama.**

Ini terobosan bukan karena teknologinya baru — component library dan package registry sudah ada sejak lama — tapi karena aturannya ditulis untuk AI yang tidak saling kenal: sebelum bikin apapun, AI *wajib* cari dulu di satu daftar yang sama. Tanpa daftar itu, tiap AI akan bikin versinya sendiri-sendiri, dan tim berakhir dengan 3 login, 2 monitor harga, 0 yang terawat.

## Apa yang didaftarkan

`team/features.json` — satu daftar semua fitur yang sudah dibuat tim, siapa pun pembuatnya (manusia atau AI).

Satu fitur = satu entri. Bukan satu file — satu *kemampuan* yang bisa dipanggil orang lain.

| Field | Artinya |
|---|---|
| `id` | Nama pendek, kebab-case, unik. Contoh: `login-api`, `price-monitor`. |
| `name` | Nama yang dibaca manusia. |
| `does` | Satu kalimat: fitur ini ngapain. Kalau tidak bisa dijelaskan dalam satu kalimat, fiturnya terlalu besar — pecah. |
| `entry` | Cara memakainya: `file:fungsi` atau `file` saja. Contoh: `src/auth/login.py:login`. Ini yang bikin fitur bisa "dipakai langsung", bukan cuma dibaca. |
| `files` | File-file yang membentuk fitur ini. |
| `keywords` | Kata kunci pencarian, 2–5 kata. Pikirkan kata yang akan diketik AI lain saat cari fitur ini. |
| `owner_id` | Siapa yang bertanggung jawab merawatnya. Harus ada di `team/roster.json` dan `active`. |
| `status` | `stable`, `experimental`, atau `deprecated`. |
| `version` | `1.0.0`, naik tiap ada perubahan cara pakai. |
| `used_by` | Siapa saja yang memakai fitur ini (task id atau teammate id). Ini yang melindungi fitur dari penghapusan. |
| `last_verified` | Terakhir dicek masih jalan. Sama seperti roster: maks 30 hari untuk `stable`. |

## Alur kerja — sebelum bikin fitur apapun

1. **Cari dulu.** `python3 scripts/features.py search <kata kunci>`. Cari dengan 2–3 kata kunci berbeda (misal: `login`, lalu `auth`, lalu `sesi`) — satu pencarian yang kosong belum membuktikan fitur itu tidak ada.
2. **Kalau ketemu yang `stable`: PAKAI, jangan bikin.** Import / panggil `entry`-nya. Lalu catat pemakaian lo: `features.py use --id login-api --by t-002`. Itu penting — `used_by` adalah bukti ada yang butuh fitur ini.
3. **Kalau ketemu yang `experimental`: pakai dengan hati-hati, atau perbaiki di tempat.** Klaim dulu file-nya (`claim.py`), perbaiki fitur yang sama, naikkan versinya. Jangan bikin salinan baru di sebelahnya dengan nama mirip — itu awal dari dobel.
4. **Kalau ketemu yang `deprecated`: jangan pakai untuk kerjaan baru.** Pakai `replacement`-nya kalau ada. Tapi jangan hapus yang deprecated — mungkin masih ada task lama yang memakainya (cek `used_by`).
5. **Kalau tidak ketemu: baru bikin.** Klaim file-nya dulu (aturan v1.1), bikin, tes, lalu **daftarkan** sebelum push: `features.py register --id ... --name ... --does ... --entry ... --files ... --owner ... --keywords ...`. Fitur yang tidak didaftarkan dianggap tidak ada — AI berikutnya akan bikin dobel tanpa salah, karena memang tidak bisa menemukannya.

## Lanjutkan di tempat, jangan fork — ini inti skillnya

Tujuan skill ini memang mempermudah pengerjaan, bukan sekadar mencegah dobel. Alur yang benar:

**AI pertama bikin fitur yang bagus → AI kedua nyambung / ngubah langsung fitur itu → fitur yang sama jadi makin bagus, bukan ada dua versi.**

Caranya, bukan dengan copy-paste ke file baru:

1. **Cari** fiturnya (`features.py search`).
2. **Klaim** file-file fitur itu (`claim.py claim`) — supaya tidak ada AI ketiga yang edit bersamaan.
3. **Ubah file yang sama.** `id` tetap, `entry` tetap kalau pemakai lama harus tetap jalan.
4. **Naikkan versi** (`features.py improve --bump minor`): `patch` untuk perbaikan bug, `minor` untuk kemampuan baru yang kompatibel (default), `major` hanya kalau `entry` berubah / pemakai lama rusak. Script menolak ganti `entry` tanpa `--bump major`.
5. **Catat siapa yang lanjutin.** Owner tetap pemilik pertama; yang memperbaiki masuk ke `contributors` dan `changes` (versi, siapa, catatan, tanggal). Jadi AI ketiga bisa lihat: fitur ini sudah dilewati dua tangan, dan apa yang berubah di tiap versi.
6. **Push registry-nya.** Kalau tidak di-push, AI berikutnya akan memperbaiki versi lama — dobel lagi, cuma beda bentuk.

Kapan boleh bikin fitur baru, bukan lanjutin yang lama?
- Tujuannya beda secara mendasar, bukan kemampuan yang sama tapi lebih besar.
- Yang lama `deprecated` dan ada penggantinya — lanjutin penggantinya.
- Yang lama tidak bisa diperbaiki tanpa merusak semua pemakainya, dan sudah dibuktikan, bukan sekadar "kayaknya lebih gampang bikin baru". Kalau alasannya itu, tulis alasannya di entri baru, supaya orang berikutnya tahu.

Fork diam-diam adalah dobel work dengan nama lain. Kalau ada dua entri dengan `does` yang hampir sama, salah satunya harus dideprecate dan diarahkan ke pemenang.

## Aturan anti-hilang

Ini bagian yang lo tekankan — "gak ngebuat ilang":

- **Jangan pernah hapus fitur.** Kalau sudah tidak dipakai, ubah status jadi `deprecated`, isi `replacement` (fitur penggantinya, kalau ada), dan biarkan entrinya tetap di daftar. Penghapusan hanya boleh dilakukan manusia (owner) setelah `used_by` kosong dan sudah lewat 30 hari masa deprecated.
- **Jangan pernah ganti `entry` tanpa naikkan `version`.** Kalau cara pakainya berubah, pemakai lama harus tahu — versi adalah sinyalnya.
- **Jangan pernah ganti `id`.** ID adalah alamat tetap. Kalau namanya jelek, ganti `name`-nya, bukan `id`-nya.
- **Satu kemampuan = satu entri.** Kalau ada dua entri dengan `does` yang hampir sama, itu sudah dobel. Gabungkan: pilih yang lebih bagus, deprecated-kan yang kalah, dan arahkan pemakainya ke pemenang.

## Kalau bukan coding?

Sama, yang didaftarkan adalah "hasil kerja yang bisa dipakai ulang":

- **Tim konten / affiliate:** fitur = template postingan, angle, atau script yang sudah terbukti: `id: angle-baut-slek`, `does: "Caption Reels baut slek untuk kunci sok"`, `entry: "affiliate/riset-angle/bahasa-postingan-rame.md"`. AI berikutnya tidak perlu nulis ulang angle yang sama.
- **Tim PKL:** fitur = prosedur yang sudah jadi: `id: ukur-sinyal`, `does: "Cara ukur sinyal di mess dan di titik tertinggi"`, `entry: "dokumen/ukur-sinyal.md"`.
- **Freelance:** fitur = script laporan yang sudah jadi: `id: laporan-penjualan-otomatis`, `does: "Generate laporan penjualan Excel dari CSV"`, `entry: "buat_laporan.py"`. Klien berikutnya tinggal pakai ulang, bukan dibuat dari nol.

## Yang TIDAK dijamin

- **Pencarian di sini masih pencocokan kata, bukan pemahaman.** Kalau keywords-nya jelek, fitur yang ada tidak ketemu. Makanya `keywords` wajib diisi dengan kata yang akan diketik orang lain, bukan kata yang lo suka. Perbaiki keywords tiap kali ada yang nyaris bikin dobel.
- **Tidak menggantikan review manusia.** `features.py check` hanya menangkap dobel yang persis sama (id atau entry yang sama). Dua fitur yang "mirip tapi beda sedikit" hanya ketahuan kalau ada yang membaca `does`-nya. Kalau ragu, tanya owner sebelum bikin.
- **Registry harus di-push seperti roster dan work ledger.** Fitur yang hanya ada di laptop lo tidak ada buat tim. Daftarkan, commit, push — sebelum push kerjaan lo yang memakainya.
