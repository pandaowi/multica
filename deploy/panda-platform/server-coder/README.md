# Komponen platform Coder–Multica

Gunakan [panduan instalasi bahasa Indonesia](installer/README.md) dan jalankan perintah dari direktori `deploy/panda-platform` pada repository ini.

Direktori ini berisi installer dua VM baru, controller preview, template workspace, bootstrap PostgreSQL, browser QA, supervisor job, serta observability/OOM. Alamat pada template sumber merupakan contoh; installer merender nilai aktual dari `.env`.

`multica/provider-privacy` menyimpan Dockerfile build. Implementasi API/UI dan leader routing sudah menyatu dalam source Multica pada root repository; tidak ada patch yang dipasang ulang oleh installer. Deploy lama tidak diperbarui otomatis.

Installer menolak host lama yang tercantum pada `PROTECTED_HOST_IPS`. Isi daftar tersebut dengan IP server layanan lama sebelum memasang VM baru. Nilai bawaan hanya contoh.
