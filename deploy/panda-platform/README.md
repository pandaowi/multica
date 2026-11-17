# Multica Panda dan platform Coder

Fork ini menyertakan perubahan backend/API dan UI untuk melindungi informasi penyedia, mempertahankan progres teks pelanggan, dan mengurangi pemicu leader squad yang tidak perlu. Fitur ini perlu konfigurasi operator; bukan pengganti isolasi tenant dan RBAC SaaS lengkap.

Source upstream yang menjadi dasar dicatat pada `UPSTREAM.json`. Direktori `server-coder` berisi installer dua VM, template, supervisor, observability, dan penghubung runtime. Jalankan installer dari direktori ini agar path pada dokumentasi sesuai:

```sh
cd deploy/panda-platform
cp server-coder/installer/.env.example .env
chmod 600 .env
./server-coder/install.sh --env .env plan
```

Semua alamat, domain dan anggaran resource ada di `.env`. Konfigurasi contoh bukan identitas server produksi. Jangan commit `.env`, PAT, sesi login CLI, key privat, database, log atau hasil audit pelanggan.

Panduan instalasi bahasa Indonesia: [server-coder/installer/README.md](server-coder/installer/README.md).

## Source dan pembaruan

Installer mengambil commit yang dipatok dari `https://github.com/pandaowi/multica.git`. Perubahan penyedia sudah berada dalam source tersebut; installer tidak mengaplikasikan patch ulang. Mengikuti `main` yang berubah secara otomatis saat produksi tidak didukung. Pembaruan upstream harus digabungkan, diuji, lalu commit baru dipatok pada installer.

Login pertama Multica/Coder, PAT akun layanan terbatas, login CLI, dan penetapan agent ke runtime adalah bootstrap operator. Key project dan grant SSH dikonfigurasi oleh `link-project`. Pelanggan tidak membutuhkan SSH atau akses ke konfigurasi penyedia.
