# Feature registry — team-roster v2

## Ide lo, dalam satu kalimat

**Jangan bikin fitur yang sudah ada. Kalau sudah ada yang bikin, pakai yang itu — jangan bikin dobel, jangan hapus yang lama.**

Ini terobosan bukan karena teknologinya baru — component library dan package registry sudah ada sejak lama — tapi karena aturannya ditulis untuk AI yang tidak saling kenal: sebelum bikin apapun, AI *wajib* cari dulu di satu daftar yang sama. Tanpa daftar itu, tiap AI akan bikin versinya sendiri-sendiri, dan tim berakhir dengan 3 login, 2 monitor harga, 0 yang terawat.

Di v2 daftar itu **sharded: satu file per fitur**, `team/features/<id>.json`, plus `team/index.json` yang digenerate (jangan diedit manual). Dua agent yang mendaftarkan fitur berbeda tidak pernah conflict — mereka menyentuh file berbeda.

## Apa yang didaftarkan

Satu fitur = satu file = satu *kemampuan* yang bisa dipanggil orang lain. Bukan satu file kode — satu kemampuan.

| Field | Required | Artinya |
|---|---|---|
| `id` | yes | Nama pendek, kebab-case, unik. Contoh: `login-api`, `price-monitor`. Grammar id ketat: `^[a-z0-9][a-z0-9-]*$`, maks 64 karakter, NFKC + casefold sebelum dibandingkan, non-ASCII ditolak. |
| `name` | yes | Nama yang dibaca manusia. |
| `kind` | yes | `code`, `doc`, `design`, atau `process`. Non-code mendaftarkan `entry` berupa pointer (URL dokumen / path repo ke artefak), bukan `file:function`. |
| `does` | yes | Satu kalimat: fitur ini ngapain. Kalau tidak bisa dijelaskan dalam satu kalimat, fiturnya terlalu besar — pecah. |
| `entry` | yes | Cara memakainya. Untuk `kind: code`: `file:fungsi`, contoh `src/auth/login.py:login`. Untuk non-code: pointer seperti URL dokumen atau path repo. |
| `entry_blob_sha` | code only | Git blob SHA dari file entry saat didaftarkan/diverifikasi. Diisi oleh `features.py reverify` terhadap repo asli. Di starter repo yang dikirim ini sudah terisi (file entry-nya ikut disertakan dan sudah di-`reverify`) — kalau blob berubah, entry dianggap *suspect* dan wajib di-reverify sebelum dipakai ulang. |
| `files` | yes | File-file yang membentuk fitur ini. |
| `keywords` | yes | Kata kunci pencarian, 2–5 kata, lowercase, NFKC, spasi tunggal. Pikirkan kata yang akan diketik AI lain saat cari fitur ini. |
| `owner_id` | yes | Siapa yang bertanggung jawab merawatnya. Harus ada di `team/roster/` dan `active`. |
| `contributors` | yes | Semua roster id yang pernah memperbaiki fitur ini. |
| `status` | yes | `stable`, `experimental`, atau `deprecated`. Pendaftaran baru default `experimental`. |
| `version` | yes | SemVer `MAJOR.MINOR.PATCH`. Aturan mekanis: tanpa leading zero, reset ke nol saat bump, naik monotonik, `0.y.z` selalu `experimental`, pre-release tidak pernah bisa `stable`. |
| `breaking` | yes | `true`/`false` — wajib diisi saat register dan improve. |
| `breaking_notes` | yes | Wajib diisi saat register dan improve (boleh string kosong kalau `breaking: false`). |
| `canonical` | yes | `true`/`false`. Tepat satu fitur kanonis per set keyword — hook menolak dua fitur `stable` yang berbagi ≥1 keyword kecuali salah satunya kanonis. |
| `used_by` | yes | Siapa saja yang memakai fitur ini (task id atau teammate id). Ini yang melindungi fitur dari penghapusan. Daftar yang ditegaskan manual — bisa basi; hook hanya warning kalau tidak cocok dengan hasil grep simbol di repo. |
| `changes` | yes | Log append-only, self-contained: tiap entri `version`, `date`, `by`, `note`. File ini — bukan riwayat git — adalah sumber kebenaran, supaya squash-merge tidak menghilangkan sejarah versi. |
| `last_verified` | yes | Terakhir dicek masih jalan (`YYYY-MM-DD`). Sama seperti roster: maks 30 hari untuk `stable`. |
| `status_reason` | yes | Alasan setiap perubahan status. |
| `status_changed_by` | yes | Siapa yang mengubah status. |
| `status_changed_at` | yes | Kapan status diubah (UTC ISO-8601 `Z`). |
| `branch` | yes | Branch tempat fitur ini dikerjakan (string kosong kalau di `main`). |
| `lamport` | yes | Counter Lamport file ini. Record baru: 1; tiap penulisan: existing + 1. |

## Alur kerja — sebelum bikin fitur apapun

1. **Cari dulu.** `python3 scripts/features.py search login team/features` (cari di direktori `team/features/`, bukan satu file). Cari dengan 2–3 kata kunci berbeda (misal: `login`, lalu `auth`, lalu `sesi`) — satu pencarian yang kosong belum membuktikan fitur itu tidak ada.
2. **Laporkan top-5 dengan skor, bukan cuma hit pertama.** Ranking deterministik, selalu sama: `stable` > `experimental` > `deprecated`; lalu versi tertinggi; lalu `used_by` terbanyak; lalu `id` paling awal. Kalau hasilnya seri, berhenti dan tanya manusia / catat ambiguitasnya di PR — jangan pilih acak.
3. **Kalau ketemu yang `stable` dan kanonis: PAKAI, jangan bikin.** Import / panggil `entry`-nya — tapi cek dulu `entry_blob_sha` terhadap file saat ini: kalau blob berubah, entry itu *suspect*, reverify dulu (`features.py reverify --id login-api`) sebelum dipakai. Lalu catat pemakaian lo: `features.py use --id login-api --by t-002`.
4. **Kalau ketemu yang `experimental`: pakai dengan hati-hati, atau perbaiki di tempat.** Klaim dulu file-nya (`claim.py`), perbaiki fitur yang sama, naikkan versinya. Jangan bikin salinan baru di sebelahnya dengan nama mirip — itu awal dari dobel. Versi in-progress di branch ditulis sebagai pre-release (`1.6.0-<branch>.1`) dengan field `branch` terisi, supaya tidak pernah disangka stabil.
5. **Kalau ketemu yang `deprecated`: jangan pakai untuk kerjaan baru, dan jangan perbaiki.** Pakai penggantinya kalau ada. Tapi jangan hapus yang deprecated — mungkin masih ada task lama yang memakainya (cek `used_by`).
6. **Kalau tidak ketemu: baru bikin.** Klaim file-nya dulu (aturan v1.1/v2 — dengan `reserves_feature` di klaim kalau lo sudah tahu id yang mau dipakai, lihat "Reservasi id fitur" di bawah), bikin, tes, lalu **daftarkan** sebelum push:
   ```bash
   python3 scripts/features.py register --id checkout-api --name "Checkout API" --does "Create a checkout order and return its payment link" --entry src/checkout.py:create_order --files src/checkout.py tests/test_checkout.py --owner <your-roster-id> --keywords checkout order payment --breaking false --breaking-notes ""
   git add team/features/checkout-api.json && git commit -m "register: checkout-api" && git push
   ```
   Fitur baru default `experimental` — status `stable` hanya lewat PR yang direview anggota roster lain yang bukan pemiliknya. Fitur yang tidak didaftarkan dianggap tidak ada — AI berikutnya akan bikin dobel tanpa salah, karena memang tidak bisa menemukannya.

## Lanjutkan di tempat, jangan fork — ini inti skillnya

**AI pertama bikin fitur yang bagus → AI kedua nyambung / ngubah langsung fitur itu → fitur yang sama jadi makin bagus, bukan ada dua versi.**

1. **Cari** fiturnya (`features.py search`).
2. **Klaim** file-file fitur itu (`claim.py claim`) — supaya tidak ada AI ketiga yang edit bersamaan.
3. **Ubah file yang sama.** `id` tetap, `entry` tetap kalau pemakai lama harus tetap jalan.
4. **Naikkan versi** (`features.py improve --bump minor`): `patch` untuk perbaikan bug, `minor` untuk kemampuan baru yang kompatibel (default), `major` hanya kalau `entry` berubah / pemakai lama rusak. Script menolak ganti `entry` tanpa `--bump major`. `breaking` + `breaking_notes` wajib diisi di setiap improve.
5. **Catat siapa yang lanjutin.** Owner tetap pemilik pertama; yang memperbaiki masuk ke `contributors` dan `changes` (versi, siapa, catatan, tanggal).
6. **Push registry-nya.** Kalau tidak di-push, AI berikutnya akan memperbaiki versi lama — dobel lagi, cuma beda bentuk.

Kapan boleh bikin fitur baru, bukan lanjutin yang lama?
- Tujuannya beda secara mendasar, bukan kemampuan yang sama tapi lebih besar.
- Yang lama `deprecated` dan ada penggantinya — lanjutin penggantinya.
- Yang lama tidak bisa diperbaiki tanpa merusak semua pemakainya, dan sudah dibuktikan, bukan sekadar "kayaknya lebih gampang bikin baru". Kalau alasannya itu, tulis alasannya di entri baru, supaya orang berikutnya tahu.

Fork diam-diam adalah dobel work dengan nama lain. Kalau ada dua entri dengan `does` yang hampir sama, salah satunya harus dideprecate dan diarahkan ke pemenang.

## Validasi entry point (v2, audit §1.6)

Hook (dan CI twin) memverifikasi setiap entry fitur yang disentuh saat push:

- File entry **ada** di tree yang di-push.
- Simbol yang dinamai **ada** di file itu (heuristic stdlib-only: parse `ast` untuk Python, atau regex atas nama simbol — dokumentasikan bahwa ini heuristic, bukan parser penuh).
- `entry_blob_sha` file entry dicatat di fitur.

Pada setiap pembacaan berikutnya, agent **wajib** mengecek `entry_blob_sha` terhadap file saat ini: kalau berbeda, entry itu *suspect* dan harus di-reverify (`features.py reverify`) sebelum dipakai ulang. **Rename harus meng-update registry di commit yang sama dengan rename-nya** — hook memblokir push kalau commit me-rename file yang terdaftar sebagai entry point tanpa meng-update `team/features/`.

## Aturan SemVer mekanis (v2, audit §1.7)

Hook menegakkan bentuknya (bukan maknanya — makna perubahan breaking tidak bisa dideteksi mesin):

- Format valid `MAJOR.MINOR.PATCH`, tanpa leading zero.
- Reset ke nol saat bump (`1.4.7 → 2.0.0`, bukan `2.4.7`).
- Naik monotonik — merge-time CI menolak versi yang ≤ versi yang sudah ada di `main` untuk fitur yang sama (tidak ada versi yang mundur).
- `0.y.z` selalu `experimental`, apa pun kata field `status`.
- Versi pre-release tidak pernah bisa `stable`.
- `breaking: true/false` + `breaking_notes` wajib di setiap register dan improve. Patch/minor bump yang menyentuh baris definisi fungsi entry point di-flag oleh hook (bukan diblokir) — deteksi API-diff sejati butuh server/parser per bahasa.

## Graf transisi status (v2, audit §1.13)

Yang diizinkan:

```
experimental → stable
experimental → deprecated
stable → deprecated
```

**`deprecated → stable` DILARANG tanpa PR yang disetujui manusia.** Tidak ada transisi yang boleh lompat langkah. Setiap transisi wajib mencatat `status_reason` + `status_changed_by` + `status_changed_at` **di commit yang sama**, dan entri `changes` untuk transisi itu harus ada di commit yang sama. Promosi `experimental → stable` wajib punya `--reviewer` yang berbeda dari owner. Entry point fitur yang `deprecated` harus tetap ada — deprecate bukan delete.

## Reservasi id fitur (v2, audit §1.15)

Id fitur di-reserve saat klaim: klaim boleh membawa `reserves_feature: <id>`, dan hook menolak registrasi yang id-nya tidak di-reserve oleh agent yang sama, atau sudah di-reserve duluan oleh orang lain. Karena klaim di v2 adalah file sendiri, reservasinya atomik saat push klaim — dua agent tidak bisa me-reserve id yang sama tanpa salah satunya ditolak push.

## Pencarian + ranking kanonis (v2, audit §1.8/§4.1)

- `canonical: true/false` — tepat satu fitur kanonis per set keyword. Hook menolak dua fitur `stable` yang berbagi ≥1 keyword kecuali salah satunya kanonis. Nama non-kanonis dicatat sebagai alias di `does`/keyword pemenang, bukan sebagai fitur terpisah.
- Ranking deterministik: `stable` > `experimental` > `deprecated`, lalu versi tertinggi, lalu `used_by` terbanyak, lalu `id` paling awal. Laporkan **top-5 dengan skor**, jangan cuma hit pertama.
- Load `team/index.json` dulu untuk pencarian (compact: id → keyword → entry), lalu ambil file record individual hanya yang relevan — supaya 500 fitur tidak membebani context tiap sesi. Pencarian tetap keyword matching, bukan pemahaman semantik — embeddings sejati butuh server (lihat README, "Known limits").

## Titik buta AI A — dan cara menutupnya (v1.4, masih berlaku di v2)

**AI A lagi develop fitur login. AI B, di tempat lain, langsung nerusin fitur login yang sama — nambah login Google, versinya naik dari 1.0.0 ke 1.1.0, terus B push. Apakah A tahu kalau fiturnya diubah?**

Jawaban jujur: **tidak, belum — bukan secara real-time.** A cuma tahu apa yang A baca terakhir kali. Kalau A terakhir fetch jam 10 pagi, lalu kerja sampai jam 11, semua yang B push jam 10:30 itu tidak ada di laptop A.

**Cara menutupnya: `scripts/sync_check.py`.** Sebelum push — detik-detik terakhir, bukan di awal sesi — A wajib jalanin ini (hook menjalankannya otomatis di v2):

```bash
git fetch origin
python3 scripts/sync_check.py team/features team/claims
```

Script ini membandingkan versi tiap fitur: kalau versi remote lebih baru dari versi lokal A, **FAIL — push diblokir.** A harus fetch dulu, baca ulang registry-nya, terus lanjutin di atas versi terbaru B (bukan menimpa). Kalau ada klaim aktif di remote atas file yang A sentuh, FAIL juga. Di v2 `sync_check` berjalan sebagai bagian dari hook, dan CI twin menjalankannya lagi di server — jadi jendelanya menyusut dari **berjam-jam jadi hitungan detik**.

**Kalau push-nya barengan, gimana?** GitHub yang jadi wasit: push pertama diterima, push kedua **ditolak** (`non-fast-forward`). Yang ditolak wajib fetch, baca ulang, dan beresin bentroknya. Jangan pernah `--force push`.

**Yang tetap tidak bisa diperbaiki (jujur saja):** kesadaran real-time yang sesungguhnya butuh server atau channel live. `sync_check` hanya memastikan kebutaan itu ketahuan **sebelum** push merusak, bukan selama A bekerja. Kalau tim lo butuh yang benar-benar real-time, itu sudah di luar skill ini — butuh server yang selalu nyala.

**Catatan v2 — baca sambil tulis ulang (audit §1.12/§5.5):** setiap agent mencatat `base_commit` (SHA `origin/main` saat registry dibaca) di klaimnya dan di trailer commit; hook memberi warning (CI gagal) kalau `base_commit` sudah lebih dari N commit di belakang `origin/main` untuk file yang disentuh saat push. Sharding membuat cek ini murah karena per record.

## Aturan anti-hilang

- **Jangan pernah hapus fitur.** Kalau sudah tidak dipakai, ubah status jadi `deprecated`, isi `replacement` (fitur penggantinya, kalau ada), dan biarkan entrinya tetap di daftar. Penghapusan hanya boleh dilakukan manusia (owner) setelah `used_by` kosong dan sudah lewat 30 hari masa deprecated.
- **Jangan pernah ganti `entry` tanpa naikkan `version`.** Kalau cara pakainya berubah, pemakai lama harus tahu — versi adalah sinyalnya.
- **Jangan pernah ganti `id`.** ID adalah alamat tetap. Kalau namanya jelek, ganti `name`-nya, bukan `id`-nya.
- **Satu kemampuan = satu entri.** Kalau ada dua entri dengan `does` yang hampir sama, itu sudah dobel. Gabungkan: pilih yang lebih bagus, deprecated-kan yang kalah, dan arahkan pemakainya ke pemenang.

## Kalau bukan coding?

Sama, yang didaftarkan adalah "hasil kerja yang bisa dipakai ulang" — dengan `kind` yang sesuai:

- **Tim konten** (`kind: doc`): fitur = template postingan, angle, atau script yang sudah terbukti: `id: angle-diskon`, `does: "Caption diskon untuk produk toko"`, `entry: "docs/angle-diskon.md"`.
- **Tim lapangan** (`kind: process`): fitur = prosedur yang sudah jadi: `id: ukur-sinyal`, `does: "Cara ukur sinyal di lapangan dan di titik tertinggi"`, `entry: "dokumen/ukur-sinyal.md"`.
- **Freelance** (`kind: code`): fitur = script laporan yang sudah jadi: `id: laporan-penjualan-otomatis`, `does: "Generate laporan penjualan Excel dari CSV"`, `entry: "buat_laporan.py"`.

## Yang TIDAK dijamin

- **Pencarian di sini masih pencocokan kata, bukan pemahaman.** Kalau keywords-nya jelek, fitur yang ada tidak ketemu. Makanya `keywords` wajib diisi dengan kata yang akan diketik orang lain, bukan kata yang lo suka. Perbaiki keywords tiap kali ada yang nyaris bikin dobel.
- **Tidak menggantikan review manusia.** `features.py check` hanya menangkap dobel yang persis sama (id atau entry yang sama). Dua fitur yang "mirip tapi beda sedikit" hanya ketahuan kalau ada yang membaca `does`-nya. Kalau ragu, tanya owner sebelum bikin.
- **Registry harus di-push seperti roster dan klaim.** Fitur yang hanya ada di laptop lo tidak ada buat tim. Daftarkan, commit, push — sebelum push kerjaan lo yang memakainya.
- **Entry yang tidak terverifikasi adalah klaim, bukan fakta.** `entry_blob_sha` kosong berarti belum pernah diverifikasi terhadap repo nyata — perlakukan sebagai `experimental` sampai `reverify` mengisinya.
