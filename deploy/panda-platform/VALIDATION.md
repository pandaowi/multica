# Validasi integrasi fork Panda

Tanggal: 17 November 2026.

Dasar upstream: `b6018d5a566ac96fab7a071ea355e46541de3d7c` dari `multica-ai/multica`. Fork sebelumnya berada 254 commit di belakang dan diperbarui secara fast-forward. Konflik integrasi privasi pada import router dan field handler diselesaikan dengan mempertahankan fitur upstream.

Source installer dipatok ke `67046a46b51f9b3a28ad0e666a6e574fe692811b` di `pandaowi/multica`. Commit itu memuat API/UI penyedia dan Dockerfile yang membangun API, CLI, migrator, maintenance, serta backfill dari source dan direktori migrasi yang sama. Image API lama tidak digunakan sebagai pembawa migrasi untuk source baru.

## Pemeriksaan yang dijalankan

- Go: `go test -p 2` pada `internal/providerprivacy`, `internal/leaderrouting`, `internal/realtime`, `internal/middleware`, `internal/handler`, `cmd/server`, `cmd/migrate`, dan `internal/migrations`: lolos. Database produksi tidak dipakai; pengujian yang memerlukan database nyata tetap memerlukan lingkungan pengembangan tersendiri.
- Build backend dan CLI: lolos untuk Linux amd64 dengan CGO dinonaktifkan. Build migrator lokal juga lolos.
- Frontend: 626 tes pada schema API, permission, filter squad, UI squad, dan kesetaraan terjemahan: lolos.
- Typecheck core/views dan lint file TypeScript yang berubah: lolos.
- Build produksi web Next.js: lolos.
- Installer/template/preview/observer/quota: 72 tes Python: lolos.
- Empat konfigurasi Compose: validasi schema lolos untuk core Coder, proxy Coder, core Multica, dan runtime project.
- Pemindaian paket yang ditambahkan: tidak ditemukan kredensial deployment, file `.env` aktual, key privat aktual, log pelanggan, hasil audit deployment, atau alamat server asli. Konfigurasi publik memakai contoh.
- `git diff --check`: lolos.

## Batas validasi

Pekerjaan ini memperbarui repository dan installer. Belum dilakukan instalasi root dua VM baru, build image Docker lengkap, penerapan migrasi ke database yang sudah ada, login provider CLI baru, atau perubahan Cloudflare. Uji koneksi SSH antarkedua VM baru akan dilakukan oleh `link-project` saat deployment sebenarnya. Aplikasi dan konfigurasi server lama tidak diubah.

Sebelum upgrade deployment lama, backup database, verifikasi migrasi pada salinan staging, dan lakukan pengujian akun pelanggan serta runtime. Installer ini memang untuk VM baru dan menolak mengambil alih stack lama.
