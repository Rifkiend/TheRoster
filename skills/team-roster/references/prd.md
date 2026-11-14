# PRD layer — team-roster v2.2

## Ide lo, dalam satu kalimat

**Setiap kerjaan harus bisa ditelusur balik ke satu keputusan: item PRD yang mana.**

Klaim bilang *siapa lagi kerja apa*. Roster bilang *siapa yang boleh kerja*. PRD bilang *kenapa kerjaan itu ada*. Tanpa PRD, klaim atas fitur yang tidak pernah diputuskan tidak bisa dibedakan dari klaim atas fitur yang sudah disetujui — dua-duanya cuma file di `team/claims/`.

## Apa yang disimpan

Satu item PRD = satu file, `team/prd/<id>.json` — sharded seperti yang lain, supaya dua agent yang bikin item berbeda tidak pernah conflict.

| Field | Required | Artinya |
|---|---|---|
| `id` | yes | Nama pendek, kebab-case. Contoh: `prd-001`. Grammar id ketat. |
| `title` | yes | Judul item yang dibaca manusia. |
| `source` | no | Pointer ke dokumen PRD-nya, contoh `docs/prd.md#login`. Ini pointer, bukan link hidup — tidak ada yang mengecek dokumennya masih bilang hal yang sama. Itu batas yang jujur, bukan yang diam-diam. |
| `status` | yes | `planned`, `in_progress`, `done`, atau `cancelled`. |
| `features` | yes | Daftar id fitur yang mengimplementasikan item ini (boleh kosong, boleh menunjuk fitur yang belum terdaftar — itu kerjaan yang direncanakan). |
| `owner_id` | yes | Penanggung jawab item ini. Harus ada di `team/roster/` dan `active`. |
| `created_at` / `updated_at` | yes | UTC ISO-8601 `Z`. |
| `status_reason` | yes | Alasan setiap perubahan status. |
| `status_changed_by` / `status_changed_at` | yes | Siapa dan kapan. |
| `lamport` | yes | Counter Lamport file ini. |

## Siklus hidup

```
planned → in_progress → done
planned → cancelled
in_progress → cancelled
```

`done` dan `cancelled` itu **terminal** — tidak ada jalan keluar. Item `done` yang butuh kerjaan lagi dapat item BARU. Item `cancelled` tetap jadi catatan keputusan, tidak pernah dihapus.

```bash
python3 scripts/prd.py create --id prd-001 --title "User login" --owner <you> --source "docs/prd.md#login" --features login-api
python3 scripts/prd.py status --id prd-001 --to in_progress --by <you> --reason "work started"
python3 scripts/claim.py claim --task t-002 --title "Kerjakan login" --owner <you> --files src/auth/login.py --prd prd-001
```

## Gerbang kejujuran — kenapa `done` itu ketat

Item boleh menunjuk fitur yang belum ada (itu kerjaan yang direncanakan). Tapi menandai item `done` adalah klaim "kerjaan ini selesai", dan klaim itu dicek dua lapis:

1. `prd.py status --to done` FAIL kalau ada fitur yang ditunjuk belum terdaftar.
2. `validate.py` FAIL kalau item `done` tapi fiturnya bukan `stable` atau `cancelled` — fitur `experimental` berarti belum selesai, fitur yang tidak ada berarti bohong.

Klaim atas item `cancelled` atau `done` ditolak di dua tempat: saat klaim dibuat (`claim.py --prd`) dan saat push (`validate.py`). Item yang sudah selesai tidak punya kerjaan lagi.

Item `cancelled` yang fiturnya masih hidup cuma warning — cancel fiturnya, atau lepas dari item. Keputusan batalnya item tidak membatalkan fiturnya secara otomatis (fitur bisa dipakai item lain); yang membatalkan fitur adalah `features.py cancel`.

## Knob kendali — `prd_required`

Di `team/config.json`, default `false`. Kalau `true`, `claim.py` menolak klaim baru tanpa `--prd`, dan validate menolak klaim aktif tanpa PRD item. Tim kecil tidak dipaksa disiplin PRD; tim yang mau disiplin tinggal nyalakan. Jangan nyalakan di tengah sprint tanpa membereskan klaim aktif yang belum ter-`--prd` — mereka akan FAIL di push berikutnya.

## Yang TIDAK dijamin

- **PRD masih file.** Tidak ada yang mengecek `source` masih sinkron dengan dokumen PRD di luar repo. Kalau dokumennya berubah tanpa item di-update, item-nya basi — dan tidak ada yang memberi tahu. Itu batasnya; yang dicek adalah konsistensi item ↔ fitur ↔ klaim, bukan item ↔ dokumen.
- **Satu arah.** Item menunjuk fitur, bukan sebaliknya. Fitur tidak tahu item PRD mana yang memilikinya, kecuali lewat daftar `features` di item. Kalau satu fitur dipakai dua item, itu sah-sah saja (fitur bersama), dan tidak ada yang melarangnya.
- **Tidak menggantikan diskusi.** PRD layer mencatat keputusan, bukan membuatnya. Item `planned` yang tidak pernah dikerjakan tetap `planned` — tidak ada yang mendesak owner-nya.
