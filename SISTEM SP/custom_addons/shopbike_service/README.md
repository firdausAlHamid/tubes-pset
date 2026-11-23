# Shop & Bike Service Management (`shopbike_service`) — Odoo 19

## Cara Install Cepat
1. Ekstrak folder `shopbike_service` ke dalam direktori `custom_addons/` Odoo 19 Anda.
2. Restart server Odoo 19.
3. Aktifkan **Developer Mode** (*Settings -> Activate the developer mode*).
4. Buka menu **Apps -> Update Apps List**, cari **Shop & Bike Service Management**, lalu klik **Install**.

## Setup 3 Kredensial (Settings -> Technical -> Parameters -> System Parameters)
Buat 3 parameter berikut:
- `shopbike.telegram_bot_token` : Token bot dari @BotFather
- `shopbike.telegram_manager_chat_id` : Chat ID Grup Telegram Kepala Toko (contoh: `-1001234567890`)
- `shopbike.midtrans_server_key` : Server Key Midtrans Sandbox (contoh: `SB-Mid-server-xxxx`)

## Setup Webhook (Jalankan `ngrok http 8069`)
1. **Telegram Webhook** (buka URL ini sekali di browser):
   `https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://<DOMAIN_NGROK>/api/shopbike/telegram/webhook`
2. **Midtrans Notification URL** (di Dashboard Midtrans Sandbox -> Settings -> Payment):
   `https://<DOMAIN_NGROK>/api/shopbike/midtrans/callback`
