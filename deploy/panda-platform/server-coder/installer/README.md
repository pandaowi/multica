# Installer dua VM — Coder, Multica, dan runtime project

Installer ini menyiapkan dua VM baru: Coder untuk menjalankan aplikasi dan Multica untuk aplikasi Multica serta runtime agent. Cloudflare tetap pada gateway yang sudah ada. Semua alamat, domain, port dan anggaran resource berasal dari `.env`. Core dan runtime memakai Docker rootless; systemd, firewall, dan broker SSH dipasang pada Linux host.

Kode ini merupakan installer baru. Installer dan template v2 yang dipakai di 7.70 tidak diubah atau diterapkan ulang. Belum ada instalasi langsung pada VM baru atau perubahan Cloudflare dalam pengujian paket ini.

## Pembagian server

| Server | Tugas |
|---|---|
| VM Coder baru | Coder, PostgreSQL metadata, Docker rootless, workspace, Traefik, watchdog, pencatatan resource dan OOM |
| VM Multica baru | Backend dan frontend Multica dengan patch privasi, PostgreSQL/pgvector, reverse proxy, serta runtime CLI terpisah per project |
| Gateway 7.101 | Tunnel Cloudflare yang sudah ada, token Cloudflare dan broker DNS terbatas |

Runtime Multica tidak menerima token Cloudflare. VM Coder juga tidak menerima token Cloudflare. Permintaan DNS dari controller Coder dikirim melalui SSH dengan key khusus ke broker pada gateway. Key ini hanya dapat meminta provisioning hostname preview pada suffix yang ditetapkan operator.

## 1. Isi konfigurasi

Jalankan dari root repository:

```sh
cp server-coder/installer/.env.example .env
chmod 600 .env
```

Edit `.env`. Alamat contoh bukan penetapan peran VM secara otomatis. Domain `example.com` dan ID Cloudflare kosong wajib diganti sebelum pemasangan/aktivasi.

| Variabel | Kegunaan |
|---|---|
| `DEPLOYMENT_ID` | Namespace baru, misalnya `coder-pilot` |
| `CODER_SSH_HOST` | Akun admin dan alamat SSH VM baru |
| `CODER_BIND_IP` | IP milik VM Coder, yang dapat dicapai gateway |
| `MULTICA_SSH_HOST`, `MULTICA_BIND_IP` | SSH administrasi dan IP layanan VM Multica baru |
| `MULTICA_DOMAIN`, `MULTICA_HTTP_PORT` | Dashboard Multica dan port privat untuk gateway |
| `MULTICA_OPERATOR_EMAIL` | Email operator pertama; signup umum belum dibuka |
| `MULTICA_SMTP_*` | SMTP pengiriman OTP; password dibaca dari file privat pada VM Multica |
| `MULTICA_OPERATOR_TOKEN_FILE` | PAT admin Multica untuk konfigurasi policy, disimpan hanya pada VM Multica |
| `MULTICA_RUNTIME_TOKEN_DIR` | Direktori PAT akun layanan member, satu file `SLUG.token` per project |
| `CODER_HOST_KEY_FILE` | `known_hosts` yang memuat host key terverifikasi untuk `CODER_BIND_IP` |
| `INTERCONNECT_SOURCE_CIDR` | IP/CIDR asal SSH runtime yang terlihat pada VM Coder |
| `INTERCONNECT_GRANT_HOURS` | Masa berlaku izin project, maksimum 720 jam |
| `RUNTIME_CPU`, `RUNTIME_MEMORY_MIB`, `RUNTIME_MAX_CONCURRENT_TASKS` | Batas tiap profil runtime, bukan jumlah workspace Coder |
| `RUNTIME_AGY_BINARY_FILE`, `RUNTIME_AGY_SHA256` | Binary Linux amd64 AGY opsional beserta checksum |
| `CF_GATEWAY_SSH_HOST` | Alamat SSH administrasi gateway |
| `CF_BROKER_SSH_TARGET` | User broker khusus dan alamat gateway yang dapat dicapai dari VM Coder |
| `CF_BROKER_ALLOWED_SOURCE` | IP/CIDR sumber koneksi SSH yang benar-benar terlihat oleh gateway |
| `CODER_DOMAIN` | Domain dashboard; gunakan satu label di bawah zone, misalnya `coder-baru.domain.id` |
| `PREVIEW_DOMAIN_SUFFIX` | Suffix khusus deployment, misalnya `preview-baru.domain.id` |
| `CF_ZONE_NAME` | Zone domain yang dimiliki operator |
| `CF_ACCOUNT_ID`, `CF_TUNNEL_ID` | Identitas akun dan tunnel yang sudah disiapkan |
| `CF_ORIGIN_HOSTNAME` | Hostname origin yang disetujui untuk konfigurasi SaaS Cloudflare |
| `CF_DNS_TOKEN_FILE` | Lokasi token DNS/SSL khusus pada gateway, bukan nilai token |
| `CF_TUNNEL_TOKEN_FILE` | Lokasi token pengelolaan tunnel pada gateway, bukan nilai token |
| `CODER_ALLOWED_CIDRS` | Daftar CIDR admin dan gateway yang boleh mengakses port Coder serta preview |
| `CODER_AUTHORIZED_KEY_FILE` | File public key SSH operator pada komputer yang menjalankan deployment |
| `CF_GATEWAY_HOST_KEY_FILE` | File `known_hosts` dengan host key gateway yang telah diverifikasi |

Untuk jaringan ZeroTier, alamat gateway dari VM Coder mungkin berbeda dengan IP LAN `10.0.0.101`. Isi alamat yang dapat dijangkau dari VM Coder. Jangan menonaktifkan verifikasi host key. Installer tidak mengambil atau memercayai host key baru secara otomatis.

`.env` dibaca sebagai data, tidak dijalankan oleh shell. Ekspansi seperti `$(...)`, `${...}` dan backtick ditolak. Gunakan path absolut untuk instalasi langsung dengan sudo. Path relatif dibaca terhadap lokasi `.env`. Jangan menaruh nilai token/API key atau private key di `.env`.

## 2. Tinjau rencana dan hasil render

```sh
./server-coder/install.sh --env .env plan
./server-coder/install.sh --env .env --output ./build/coder-pilot render
```

`plan` tidak menghubungi server. `render` hanya membuat paket pada direktori lokal yang kosong, meliputi Compose, template, konfigurasi observer, unit systemd dan rencana Cloudflare. File sumber lama tidak ditimpa. Direktori output yang sudah berisi file ditolak.

Perubahan resource ikut masuk ke template Terraform, pemeriksaan kapasitas, verifikasi container dan batas pool systemd. Perubahan domain ikut masuk ke helper preview, origin aplikasi dan controller; tidak hanya mengganti URL pada dokumentasi.

## 3. Pasang VM Coder dengan satu perintah

Dari terminal komputer operator:

```sh
./server-coder/install.sh --env .env deploy-coder
```

Skrip mengirim paket terpilih melalui SSH, kemudian menjalankan installer dengan sudo pada VM baru. Operator dapat diminta password sudo di terminal. File private key SSH dan token Cloudflare tidak ikut dikirim. Hanya public key operator serta entri `known_hosts` gateway yang diperlukan yang dikirim.

Alternatif jika repository dan `.env` sudah berada di VM baru:

```sh
./server-coder/install.sh --env .env check
sudo ./server-coder/install.sh --env .env install-coder
```

Perintah `check` memeriksa host tempat perintah dijalankan. Jalankan pada VM Coder, bukan laptop atau gateway.

Installer akan:

1. Memastikan IP target ada pada host, cgroup v2 tersedia, serta CPU/RAM/disk memenuhi anggaran.
2. Menolak host lama yang dilindungi, user `coder` yang tidak dikenali, direktori instalasi lama, rootful Docker yang sudah ada, layanan aplikasi lama dan port yang terpakai.
3. Memasang paket Docker dari repository bertanda tangan resmi pada Ubuntu 24.04/26.04 amd64. Versi paket yang dipilih dicatat; tidak menjalankan upgrade OS secara menyeluruh.
4. Membuat user layanan `coder`, subordinate UID/GID, delegasi cgroup dan Docker rootless. User ini tidak dimasukkan ke grup Docker rootful. Daemon Docker rootful pada VM baru dimask agar tidak dijalankan oleh pemasangan paket.
5. Memasang batas pool dan aturan nftables khusus port Coder/preview. Tidak menjalankan `flush ruleset`, menonaktifkan firewall lama atau mengubah port SSH/ZeroTier.
6. Memasang Coder, database metadata dan Traefik dengan volume terpisah serta namespace deployment baru.
7. Membangun image dasar dari sumber yang dipatok, bukan mengambil ID image lokal milik 7.70.
8. Mengaktifkan controller preview, watchdog/resource observer dan pembaca OOM kernel. Pembaca kernel mendapat grup journal hanya pada proses servicenya; user agent/workspace tidak diberi akses journal host.
9. Membuat key broker DNS khusus dan perintah `~/.local/bin/coder-workspace` pada akun `coder`.

Akun admin Coder pertama belum dibuat otomatis. Setelah instalasi, akses URL privat melalui jaringan admin, buat admin pertama, lalu siapkan PAT operator pada `~/.config/coder-infra/session-token` milik `coder`, dengan direktori `0700` dan file `0600`. Jangan mengaktifkan domain dashboard publik sebelum admin pertama tersedia.

## 4. Pasang VM Multica

Setelah VM Coder siap, dari komputer operator dengan `.env` yang sama:

```sh
./server-coder/install.sh --env .env deploy-multica
```

Alternatif dari VM Multica yang sudah berisi checkout dan konfigurasi:

```sh
./server-coder/install.sh --env .env check-multica
sudo ./server-coder/install.sh --env .env install-multica
```

Isi SMTP dan email operator sebelum instalasi. Bila SMTP memakai autentikasi, buat file `MULTICA_SMTP_PASSWORD_FILE` pada VM Multica dengan mode `0600`, parent `0700`; isinya hanya password, tidak dimasukkan ke command line. Untuk port 465 gunakan `MULTICA_SMTP_TLS=implicit`; port 587 biasanya `starttls`.

Installer menolak host lama, VM yang sama dengan Coder, user layanan atau stack yang sudah ada. Ia memasang user `multica`, Docker rootless, cgroup pool, firewall port privat, image backend/frontend dari source fork Panda yang sudah memuat perubahan privasi, database, edge, dan image runtime CLI. Build dilakukan berurutan sebelum core berjalan. Log build privat dapat diperiksa operator. Durasi build awal berbeda dengan durasi agent membuat aplikasi.

Source Multica diambil dari `MULTICA_SOURCE_REPO=https://github.com/pandaowi/multica.git` pada commit `MULTICA_SOURCE_REV` yang dipatok. Perubahan API/UI dan routing leader sudah menyatu dalam fork; installer tidak menempelkan patch ulang. Revisi baru harus digabungkan dan diuji oleh maintainer sebelum pin diperbarui.

Codex CLI terpasang dengan versi yang ditetapkan konfigurasi. AGY hanya disertakan bila binary Linux dan SHA-256 disediakan operator; instalasi tidak mengunduh lalu menjalankan skrip AGY tanpa verifikasi. Kemampuan login/model AGY perlu diverifikasi sesuai binary yang dipilih.

Instalasi baru memakai ID placeholder pada policy privasi, bukan menyalin ID agent, runtime, skill, atau kredensial layanan lama. Signup awal dibatasi pada email operator. **Jangan mengundang pelanggan saat bootstrap.** Akses internal awal tersedia pada `http://MULTICA_BIND_IP:MULTICA_HTTP_PORT`; route HTTPS diaktifkan pada tahap berikutnya. Login pertama dan undangan akun layanan memakai OTP melalui SMTP.

## 5. Siapkan broker pada gateway 7.101

Ambil public key broker yang dihasilkan installer dari:

```text
/var/lib/coder-installer/DEPLOYMENT_ID/public/cloudflare-broker.pub
```

Salin public key tersebut ke gateway. Private key tetap berada pada VM Coder. Siapkan token DNS/SSL khusus dengan cakupan zone yang diperlukan pada path `CF_DNS_TOKEN_FILE`, dan token pengelolaan tunnel pada `CF_TUNNEL_TOKEN_FILE`, masing-masing dengan izin `0600`. Kedua token dapat memiliki cakupan berbeda; jangan memakai kredensial pelanggan.

Dengan checkout repository serta `.env` yang sama pada gateway:

```sh
sudo ./server-coder/install.sh --env .env --public-key ./cloudflare-broker.pub gateway-install-broker
```

Perintah ini menambah user baru `cf-DEPLOYMENT_ID`, policy baru dan forced-command SSH. Tidak mengubah konfigurasi `cloudflared`, akun operator lama atau container pada gateway. Key menolak shell bebas, forwarding dan hostname di luar suffix preview yang ditetapkan. Pemasangan ulang terhadap user/direktori broker yang sudah ada ditolak agar token atau policy tidak tertimpa diam-diam.

Token DNS disalin hanya ke direktori privat broker pada gateway; token sumber dan layanan lama tidak diubah. File policy dan kode broker dikendalikan root, sedangkan state serta salinan token hanya dapat dibaca oleh user broker.

## 6. Tinjau lalu aktifkan route Cloudflare

Masih pada gateway:

```sh
umask 077
sudo ./server-coder/install.sh --env .env gateway-plan > cloudflare-candidate.json
```

Periksa kandidat ingress. Aturan lama dan properti tunnel lain dipertahankan. Route exact-host atau wildcard lama yang bertabrakan dengan domain baru menyebabkan penolakan. Installer tidak mengambil alih hostname lama.

Setelah admin Coder pertama tersedia, health Multica dapat dicapai dari gateway, dan rencana telah diperiksa, jalankan:

```sh
sudo ./server-coder/install.sh --env .env gateway-apply
```

Aktivasi membaca konfigurasi tunnel lagi sebelum perubahan, menyimpan backup, menambahkan route baru dan membuat DNS dashboard Coder, dashboard Multica, dan origin jika belum ada. DNS yang berbeda ditolak. Tidak diperlukan restart service `cloudflared` untuk konfigurasi tunnel yang dikelola secara remote.

Cloudflare API tidak menyediakan transaksi atomik bersama untuk seluruh operasi ini. Hindari perubahan tunnel bersamaan melalui dashboard/alat lain selama aktivasi. Jika sebagian operasi gagal, periksa backup dan status aktual; jangan menghapus konfigurasi lama atau mengulang PUT secara buta.

**Batas kompatibilitas Cloudflare:** preview bertingkat memakai provisioning custom hostname/sertifikat individual sebagaimana platform sebelumnya, beserta guard kuota. Cloudflare for SaaS dan izin zone yang diperlukan harus tersedia. Skrip tidak mengaktifkan layanan berbayar atau memesan sertifikat berbayar. Jika zone sudah memiliki fallback origin berbeda, proses menolak perubahan: gunakan zone/origin/tunnel yang kompatibel atau lakukan perancangan ulang yang ditinjau. Jangan memindahkan fallback origin milik layanan lama hanya agar instalasi baru berhasil. Hostname yang sudah ada juga tidak otomatis dihapus saat rollback.

## 7. Publikasikan template dan uji workspace

Pada VM Coder, sebagai user `coder`, setelah PAT serta broker tersedia:

```sh
~/.local/bin/coder-workspace release
~/.local/bin/coder-workspace provision --name infra-check --owner USER_CODER --test
```

Ganti `USER_CODER` dengan akun Coder yang sudah ada. Build image kemampuan v3 memakai base image yang baru dibangun di VM ini. Pengujian membuat project uji, memeriksa PostgreSQL/QA/HTTPS/resource tracking serta pemulihan, kemudian menghentikan workspace dan melepas reservasinya sambil mempertahankan volume.

Untuk workspace normal:

```sh
~/.local/bin/coder-workspace provision --name project-baru --owner USER_CODER
~/.local/bin/coder-workspace verify --name project-baru --owner USER_CODER
```

Helper publikasi production statis dinonaktifkan dalam paket ini. Fokus instalasi adalah development dan preview. Publikasi aplikasi production dan remote build pada server ketiga memerlukan tahap tersendiri. Runtime Multica dihubungkan pada tahap berikut.

## 8. Hubungkan project Multica ke workspace Coder

Hubungan antarkedua VM dibuat oleh perintah `link-project`, bukan dengan memberi agent akses shell host. Alurnya:

```text
Pelanggan → HTTPS Cloudflare di 7.101 → Multica
Agent CLI di runtime project pada VM Multica
    → coder-project → SSH forced-command pada VM Coder
    → Docker exec UID 1000 pada workspace yang diizinkan
    → source, PostgreSQL project, build, QA, dan preview
Hasil perintah → koneksi SSH yang sama → agent → pelanggan
Preview pelanggan → Cloudflare di 7.101 → VM Coder
```

Tidak dibutuhkan akses SSH balik dari VM Coder ke VM Multica. Kedua VM membutuhkan akses keluar ke registry, dependency aplikasi dan layanan model; runtime harus bisa mencapai port SSH VM Coder, dan gateway harus bisa mencapai port privat dashboard/proxy. Sesuaikan firewall jaringan/ZeroTier di luar host bila ada. Public key host harus diverifikasi operator dan sudah ada di `known_hosts`; installer tidak menonaktifkan pemeriksaan host key.

Persiapan awal melalui akun operator:

1. Login ke Multica baru, buat workspace dan project yang akan dihubungkan. Buat agent project memakai akun operator, bukan akun pelanggan. Instruksi global runtime sudah menyediakan cara memakai `coder-project`.
2. Undang **akun layanan member khusus project** ke tepat satu workspace Multica. Akun layanan boleh melihat tugas dalam workspace tersebut sesuai ACL Multica; ini bukan isolasi tenant penuh. Buat PAT dari akun layanan dan simpan privat pada `MULTICA_RUNTIME_TOKEN_DIR/SLUG.token` di VM Multica. Jangan memakai PAT admin/operator untuk runtime. Undangan memang diperlukan agar akun layanan dapat signup di deployment yang tertutup.
3. Buat PAT operator untuk konfigurasi privasi, simpan di `MULTICA_OPERATOR_TOKEN_FILE` dengan mode `0600`. PAT Multica adalah autentikasi ke Multica, berbeda dengan autentikasi model. Login Codex/AGY dilakukan melalui sesi CLI, tidak membutuhkan pengisian model API key pada `.env`.
4. Provision workspace Coder khusus project menggunakan tahap 7. Catat UUID workspace Coder, UUID project Multica, dan UUID workspace Multica. Ketiganya berbeda. Workspace Coder harus terdaftar pada controller instalasi ini dan tidak boleh berisi aplikasi project lain.

Dari komputer operator, jalankan satu perintah penghubung:

```sh
./server-coder/install.sh --env .env \
  --project aplikasi-demo \
  --workspace-id UUID_WORKSPACE_CODER \
  --multica-workspace-id UUID_WORKSPACE_MULTICA \
  --multica-project-id UUID_PROJECT_MULTICA \
  link-project
```

Operator dapat diminta sudo pada masing-masing VM. Skrip membuat key project pada VM Multica, mengirim **public key saja** ke VM Coder, membuat grant root yang mengikat key ke project/workspace/image/masa berlaku, memasang forced-command dan sudoers yang spesifik, lalu mengonfigurasi volume runtime. Private key administrasi operator tidak disalin. Private key project hanya dipakai oleh profil runtime project tersebut.

Izin ini memberikan kebebasan menjalankan kode **di dalam satu workspace Coder**, termasuk shell di container untuk build/test. Izin tidak memberikan shell Linux host, pilihan workspace lain, Docker socket, token Cloudflare, atau direktori project lain. Isolasi utama adalah container dan volume workspace; folder dengan UID sama bukan batas tenant.

Setelah konfigurasi, skrip otomatis menguji tulis/baca dan UID/direktori kerja melalui koneksi antarkedua VM. Jika gagal, `link-project` gagal dan tidak menyatakan runtime siap. Pengujian ini tidak memanggil model. Daemon belum dijalankan sebelum login CLI.

Pada VM Multica, login CLI sekali untuk profil tersebut dari terminal interaktif:

```sh
sudo python3 /opt/platform-installer/DEPLOYMENT_ID/main.py --env /ABS/PATH/.env \
  --project aplikasi-demo --provider codex runtime-login
```

Gunakan `--provider agy` bila binary opsionalnya sudah disediakan. Ikuti login yang ditampilkan CLI. Kredensial disimpan pada volume privat profil tersebut, tidak disalin ke VM Coder. `runtime-login` memeriksa kapasitas, lalu memulai daemon project. Login ulang memerlukan runtime project dihentikan dulu melalui Compose profilnya; jangan menghentikan stack Multica.

Bind agent project ke runtime `project-SLUG` melalui akun operator. Source aplikasi harus diedit melalui `coder-project`, karena file di folder task runtime hanyalah scratch. Native editor/file tool CLI tidak otomatis menjadi remote filesystem. Verifikasi perilaku agent pada pekerjaan pertama, termasuk build lewat `coder-dev job`, browser QA, dan link HTTPS.

Sebelum pelanggan memperoleh akses, saat semua agent idle, jalankan pada VM Multica:

```sh
sudo python3 /opt/platform-installer/DEPLOYMENT_ID/main.py --env /ABS/PATH/.env \
  --multica-workspace-id UUID_WORKSPACE_MULTICA configure-multica
```

Perintah ini memverifikasi identitas/role operator melalui API, memasang ID workspace baru, menandai agent/squad/skill milik operator dan runtime terdaftar sebagai internal, kemudian restart backend baru saja. Jalankan kembali setelah menambah resource penyedia. Penandaan ini saat ini melindungi **satu workspace Multica**; ekspansi ke beberapa tenant membutuhkan desain RBAC/tenant berikutnya. Jangan mengganti workspace policy yang sudah aktif dengan workspace lain.

Akun pelanggan tidak perlu menerima file akses, key SSH, PAT operator, instruction internal atau log tool mentah. Runtime tetap memerlukan kredensial CLI untuk bekerja; proses dengan UID yang sama di runtime dapat membaca kredensial profilnya. Ini belum menggantikan credential broker dan isolasi tugas untuk SaaS publik yang tidak tepercaya. Jangan menaruh kredensial penyedia lintas pelanggan pada profil tersebut.

Untuk mencabut koneksi project, pada VM Coder:

```sh
sudo python3 /opt/platform-installer/DEPLOYMENT_ID/main.py --env /ABS/PATH/.env \
  --project aplikasi-demo revoke-project
```

Permintaan baru ditolak; perintah foreground yang sudah berjalan dibatasi maksimum 300 detik. Preview yang sudah berjalan tidak otomatis dihentikan, dan volume/source tidak dihapus. Grant kedaluwarsa juga menolak permintaan baru. Pembaruan grant dilakukan operator melalui enrolment ulang yang ditinjau.

## Resource awal untuk VM 8 CPU / RAM 8 GB

| Kebutuhan | Konfigurasi contoh |
|---|---|
| Pool seluruh akun `coder` | 6 CPU, RAM maksimum 6 GiB |
| Workspace | 2 CPU, RAM 3 GiB, 512 PID, satu workspace |
| Reservasi layanan platform | 3 CPU dan 3 GiB dalam pemeriksaan kapasitas |
| Batas build image awal | 1 CPU dan 768 MiB |
| Swap pool | 0 |
| Ruang disk minimum | 20 GiB; ini bukan kuota per project |

VM Multica memakai pool 6 CPU/6 GiB, core dianggarkan minimal 3 CPU/2,5 GiB, dan tiap profil runtime 2 CPU/3 GiB. Dengan default ini, hanya satu profil project dapat aktif; profil lain disimpan tetapi perlu menghentikan runtime idle atau menambah kapasitas sebelum diaktifkan. Dua task paralel dalam satu profil berbeda dari dua runtime project. Pemeriksaan kapasitas menolak aktivasi tambahan, tidak menyediakan scheduler antrean lintas project. Batas pool tetap menjadi pengaman terakhir.

Angka merupakan anggaran pilot, belum benchmark kapasitas produksi. Installer menyisakan minimal 1 GiB RAM dan satu CPU di luar pool serta menolak konfigurasi yang melebihi resource host. Build dan browser QA berat tetap perlu diantrekan. Model AI lokal, multi-tenant lengkap, billing serta isolasi tenant belum disediakan oleh installer ini.

## Kegagalan, pemeriksaan dan rollback

- Log Multica: `/var/lib/multica-installer/DEPLOYMENT_ID/install.log`.
- Stack Multica: `MULTICA_INSTALL_ROOT/compose.json`; profil: `MULTICA_INSTALL_ROOT/projects/SLUG/compose.json`.
- Grant project: `/etc/coder-execution/DEPLOYMENT_ID/SLUG.json`, dikelola root.
- Log instalasi privat root: `/var/lib/coder-installer/DEPLOYMENT_ID/install.log`.
- Penanda instalasi root: `/var/lib/coder-installer/DEPLOYMENT_ID/installation.json`.
- Sumber terpasang: `INSTALL_ROOT/workspace-kit/releases/initial`.
- Log/state workspace: `INSTALL_ROOT/workspace-kit/state/run-*`.
- Konfigurasi route/observer: `INSTALL_ROOT/app-platform`.
- Backup tunnel: `/var/lib/coder-cloudflare-backups/DEPLOYMENT_ID/tunnel-before.json`.

Installer yang menemukan instalasi selesai dengan konfigurasi sama tidak menimpa data. Instalasi tidak lengkap atau konfigurasi berbeda ditolak untuk ditinjau operator. Kegagalan tidak menghapus volume, akun, database atau file secara otomatis. Kode instalasi host dapat mengubah OS target baru; karena itu jangan mengeksekusinya pada server yang masih dipakai layanan lama.

Untuk menghentikan layanan baru, gunakan Compose milik namespace baru dan unit systemd yang dibuat installer. Jangan memakai `down -v`, `docker system prune`, atau menghapus `/home/coder` sebagai rollback. Pemulihan tunnel harus membandingkan konfigurasi terbaru dengan backup agar tidak membatalkan perubahan sah dari layanan lain setelah instalasi.

## Validasi paket

```sh
python3 -m unittest discover -s server-coder/installer/tests
```

Pengujian mencakup validasi `.env`, penolakan shell expansion/secret inline, overbooking, host lama, integritas source, propagasi konfigurasi, pelestarian route lama, konflik wildcard dan pembatasan broker. Compose dan sintaks hasil render juga diperiksa. Pengujian ini tidak menggantikan uji instalasi root pada VM baru dengan domain/token yang benar.


Uji broker Docker yang terisolasi dapat dijalankan secara eksplisit pada host rootless yang memiliki image dasar lama:

```sh
python3 server-coder/installer/tests/broker_docker_smoke.py coder@HOST_UJI
```

Fixture ini dibatasi 0,25 CPU/192 MiB, tanpa jaringan dan tanpa port publik. Hanya container/volume dengan label fixture sendiri yang dibersihkan. Uji ini memeriksa broker ke container; tidak memasang sudoers/SSH dan bukan bukti bahwa dua VM baru sudah terhubung. Bukti koneksi dua VM baru dihasilkan oleh pemeriksaan otomatis dalam `link-project` setelah instalasi nyata.
