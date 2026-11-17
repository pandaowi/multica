# Platform Development Web v2

Template v2 dipublikasikan sebagai template Coder terpisah bernama `webdev-platform`. Penerapannya tidak memperbarui `webdev-lan`, workspace Development yang sudah ada, image maupun data aplikasinya. Setiap versi template merujuk ID image hasil build yang pasti. Workspace baru memakai `automatic_updates=never`, sehingga pembaruan template tidak otomatis mengganti lingkungan yang sedang dipakai.

Dokumentasi ini ditujukan untuk tim infra. Perintah dijalankan sebagai user `coder` pada server yang sudah disiapkan, saat ini `10.0.0.10`.

## Penggunaan di server

Masuk melalui SSH:

```sh
ssh coder@10.0.0.10
```

Buat workspace dengan satu perintah:

```sh
~/.local/bin/coder-workspace provision --name project-baru --owner tantowi-jauhari
```

Ganti `--name` dengan nama workspace dan `--owner` dengan username pemilik yang sudah terdaftar di Coder. Nama workspace dan owner dipakai kembali untuk operasi berikutnya.

Skrip melakukan langkah berikut secara otomatis:

1. Memeriksa kapasitas CPU, RAM dan ruang disk.
2. Membangun atau memakai kembali image rilis berdasarkan sidik hash sumber.
3. Mempublikasikan atau memakai kembali versi template `webdev-platform`.
4. Membuat workspace dan menunggu bootstrap kemampuan standar selesai.
5. Memeriksa identitas workspace, image, UID, batas resource dan volume home.
6. Mendaftarkan ID workspace serta volumenya ke controller preview dan observer pada host.
7. Memverifikasi helper, browser QA, supervisor dan pencatatan resource.

Menjalankan kembali `provision` pada workspace buatan skrip yang masih berjalan akan memverifikasi dan mendaftarkannya kembali tanpa menggandakan registrasi. Workspace lama yang bukan buatan skrip tidak diambil alih. Jika workspace buatan skrip sedang berhenti, gunakan `start`. Eksekusi beberapa operator diantrekan melalui lock agar perubahan tidak bertabrakan.

## Menjalankan dari repository

Jika bekerja dari checkout repository pada komputer operator, gunakan pembungkus SSH berikut:

```sh
./server-coder/workspace-template/infra-workspace.sh provision --name project-baru --owner tantowi-jauhari
```

Pembungkus hanya mengirim sumber platform yang dipilih ke host, kemudian menjalankan proses yang sama. Akses SSH ke user `coder` diperlukan. Data project pelanggan, state runtime dan kredensial tidak ikut dikirim.

## Prasyarat dan kredensial

Host harus sudah memiliki stack Coder rootless, controller preview, observer resource, pembaca OOM kernel dan image dasar yang disetujui. **Skrip ini bukan installer server kosong.** Jika memindahkan platform ke server lain, image dasar serta layanan host perlu disiapkan terlebih dahulu.

Operator menggunakan personal access token (PAT) Coder yang memiliki izin publikasi template dan pembuatan workspace. Sebelum penggunaan pertama, simpan token melalui terminal atau editor yang aman pada host:

```text
~/.config/coder-infra/session-token
```

Direktori harus berizin `0700` dan file `0600`, dengan pemilik user `coder`. Jangan memasukkan nilai token ke argumen perintah, repository, template atau workspace. Opsi `--token-file` dapat memilih file privat lain. Variabel lingkungan `CODER_SESSION_TOKEN` juga didukung jika menjalankan skrip langsung pada host. Token yang hilang atau kedaluwarsa menyebabkan proses gagal sebelum membuat workspace.

Kredensial Cloudflare tetap berada di lokasi privat yang sudah ada pada host. Skrip tidak meminta atau menyalinnya ke workspace.

## Operasi sehari-hari

Periksa workspace yang sedang berjalan:

```sh
~/.local/bin/coder-workspace verify --name project-baru --owner tantowi-jauhari
```

Hentikan workspace dengan mempertahankan volume datanya:

```sh
~/.local/bin/coder-workspace stop --name project-baru --owner tantowi-jauhari
```

Jalankan kembali workspace. Skrip memeriksa kapasitas dan memulihkan registrasi host bila diperlukan:

```sh
~/.local/bin/coder-workspace start --name project-baru --owner tantowi-jauhari
```

Preview ikut berhenti ketika workspace berhenti. Registrasi workspace yang hanya dihentikan tetap dihitung sebagai reservasi kapasitas. Gunakan prosedur pelepasan registrasi jika reservasi tersebut memang ingin dilepas.

## Pengujian penerimaan

Gunakan nama khusus untuk workspace uji:

```sh
~/.local/bin/coder-workspace provision --name infra-check --owner tantowi-jauhari --test
```

Pada awal pengujian, workspace harus kosong. Skrip hanya membuat project `infra-acceptance` miliknya sendiri dan menguji:

- Hak akses PostgreSQL, penyimpanan data, listener loopback dan penolakan launcher database ganda.
- Penggunaan ulang akun sintetis QA, penyamaran nilai akun dalam keluaran dan penolakan izin file yang tidak aman.
- Browser QA pada ukuran desktop dan seluler serta penyerahan akun uji kepada pengujian.
- Pencatatan resource per project dan job yang dikelola helper.
- Preview publik melalui HTTPS Cloudflare dan interaksi browser pada aplikasi uji.
- Pemulihan supervisor workspace uji dengan PID aplikasi tetap sama.
- Stop/start penuh melalui Coder, pemulihan preview dan persistensi database.
- Perbandingan state aplikasi workspace lain sebelum dan sesudah pengujian.

Jika fixture pengujian milik skrip terputus, perintah yang sama dapat melanjutkannya. Skrip menolak fixture yang sumbernya sudah diubah atau workspace yang mendapat project tambahan.

Jika resolver DNS LAN host masih menyimpan hasil “domain tidak ditemukan” untuk domain baru, probe menggunakan jawaban DNS publik melalui DoH khusus untuk pemeriksaan tersebut. Sertifikat tetap diverifikasi terhadap hostname HTTPS aslinya. DNS sistem tidak diubah, dan hasil pengujian mencatat penggunaan fallback tersebut.

Secara default, pengujian diakhiri dengan workspace berhenti dan registrasi host dilepas agar reservasi uji tidak terus dipakai. Volume data dipertahankan. Tambahkan `--keep-running` jika workspace uji ingin tetap berjalan:

```sh
~/.local/bin/coder-workspace provision --name infra-check --owner tantowi-jauhari --test --keep-running
```

Pengujian tidak sengaja memicu OOM atau menghentikan proses workspace lain. Integrasi pencatatan OOM kernel diperiksa melalui registrasi observer tanpa menghabiskan RAM workspace.

## Kegagalan dan rollback

Jika provisioning gagal, skrip mempertahankan workspace dan volumenya untuk investigasi. Data tidak dihapus otomatis. Setelah penyebabnya diperbaiki, jalankan kembali `provision`; untuk workspace yang sedang berhenti, gunakan `start`. Pengujian yang terputus dapat dilanjutkan dengan `--test` selama fixture masih memenuhi syarat di atas.

Untuk melepas workspace dari layanan host, hentikan terlebih dahulu lalu hapus registrasinya:

```sh
~/.local/bin/coder-workspace stop --name project-baru --owner tantowi-jauhari
~/.local/bin/coder-workspace unregister --name project-baru --owner tantowi-jauhari
```

`unregister` hanya menghapus dua registrasi workspace tersebut: preview dan observer. Controller menghapus route preview yang tidak tersedia secara asinkron. Volume, DNS, sertifikat dan reservasi hostname dipertahankan; penghentian permanennya memerlukan tindakan operator tersendiri. Perintah `start` dapat menjalankan kembali workspace dan mendaftarkannya lagi jika kapasitas tersedia.

Jangan menjadikan penghapusan workspace atau volume Docker sebagai rollback rutin. Jika penulisan registrasi gagal, skrip mengembalikan kedua file konfigurasi selama masih memegang lock controller.

## Lokasi skrip, hasil dan log

| Kebutuhan | Lokasi pada host |
|---|---|
| Perintah operator | `~/.local/bin/coder-workspace` |
| Sumber rilis terpasang | `/opt/coder/workspace-kit/releases/source-*` |
| Rujukan sumber aktif | `/opt/coder/workspace-kit/current` |
| Hasil dan log per eksekusi | `/opt/coder/workspace-kit/state/run-*` |
| Token Coder privat | `~/.config/coder-infra/session-token` |

Manifest rilis mengikat sidik hash sumber, ID image, ID template dan versi template. Log serta state dapat berisi metadata internal operasional; jangan menyajikannya kepada pelanggan melalui frontend.

## Kemampuan yang disertakan dan batasnya

- Node 22, npm, Python, Git dan VS Code Web; batas per workspace 2 CPU, RAM 4 GiB dan 512 PID, tanpa tambahan swap.
- Home persisten, UID 1000, Linux capabilities dilepas dan `no-new-privileges` aktif. Workspace tidak menerima socket Docker, mount direktori host atau port host yang dipublikasikan.
- Bootstrap helper preview, launcher dan instruksi agent dapat dijalankan ulang. Identitas preview berasal dari UUID workspace agar project bernama sama pada workspace berbeda tidak mendapat hostname yang bertabrakan. Helper atau launcher lama yang tidak dikenali tidak ditimpa. Instruksi `AGENTS`, sumber aplikasi dan database yang sudah ada dipertahankan.
- Dependency npm helper, binary PostgreSQL, browser, library dan font sudah disertakan dalam image. Bootstrap kemampuan standar tidak perlu mengunduh npm/apt/browser saat startup. Dependency masing-masing aplikasi tetap dipasang pada projectnya.
- Preview banyak aplikasi, pemulihan proses otomatis, PostgreSQL per project, akun sintetis QA privat, browser QA dan metadata job yang dikelola helper.
- Registrasi host mengaktifkan jaringan preview per workspace serta observer supervisor, resource dan OOM yang sudah ada.

Skrip mencadangkan 4 GiB RAM dan 2 CPU per workspace, ditambah 4 GiB RAM dan 3 CPU untuk kebutuhan platform. Skrip juga memeriksa sisa RAM saat itu dan minimal 10 GiB ruang disk kosong. Workspace terdaftar yang sedang berhenti tetap mendapat reservasi secara konservatif.

Pemeriksaan ini berlaku pada alur operator melalui skrip. Pembuatan workspace langsung melalui UI/API Coder dapat melewatinya; pemeriksaan tersebut belum menjadi scheduler seluruh server atau kuota disk.

## Hubungan dengan Cloudflare

Alur pembuatan preview:

```text
Agent menjalankan coder-preview start dalam project
  → helper menulis manifest lokal
  → controller host membaca workspace yang sudah disetujui
  → route aplikasi, DNS dan HTTPS disiapkan
  → trafik melewati tunnel yang sudah ada dan Traefik
  → frontend aplikasi di workspace
```

Setiap workspace tidak memerlukan tunnel atau token Cloudflare baru. Pemeriksaan konflik hostname dan batas provisioning yang sudah ada tetap berlaku. Hanya frontend yang dipublikasikan; database mendengarkan pada loopback.

Preview memerlukan workspace yang sedang berjalan. Menghapus registrasi tidak otomatis menghapus DNS atau sertifikat. Deployment aplikasi production merupakan alur terpisah dari preview.

## Pengelolaan versi dan cakupan SaaS

Setelah suatu rilis tercatat, perubahan sumber kemampuan, image atau template memerlukan perubahan nilai `RELEASE`. Jangan mengubah isi rilis yang sudah tercatat secara langsung. Pertahankan image dasar pada daemon rootless; pemindahan ke host lain memerlukan pemuatan atau build ulang image tersebut.

`Dockerfile` lama tetap menjadi resep image dasar. Image kemampuan v2 dibangun dari `infra/Dockerfile`, yang mematok ID image dasar lokal yang disetujui. Berkas `main.tf` memuat placeholder `__WORKSPACE_IMAGE__`; skrip operator menggantinya dengan ID image terverifikasi sebelum mengunggah template. **Jangan langsung mengunggah direktori sumber ini dengan `coder templates push`.**

Template ini belum menerapkan tenant/RBAC, onboarding dan billing pelanggan, pengikatan runtime Multica, maupun isolasi jaringan dan rahasia antartenant. Jaringan dasar Coder dan resource host masih dipakai bersama. Desain tenant tahap 2 perlu diterapkan sebelum rilis SaaS pelanggan; workspace berbeda belum membuktikan isolasi tenant yang lengkap.
