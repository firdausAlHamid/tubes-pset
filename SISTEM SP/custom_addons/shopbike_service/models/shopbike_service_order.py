import base64
import hashlib
import hmac
import json
import time
import requests

from odoo import api, fields, models
from odoo.exceptions import UserError


class ShopbikeServiceOrder(models.Model):
    _name = 'shopbike.service.order'
    _description = 'Tiket Service Order Shop & Bike'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(
        string='No. Service Order',
        required=True,
        copy=False,
        readonly=True,
        default='New',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Pelanggan', required=True, tracking=True,
    )
    whatsapp_number = fields.Char(
        related='partner_id.whatsapp_number',
        string='Nomor WhatsApp',
        readonly=False,
    )
    telegram_chat_id = fields.Char(
        related='partner_id.telegram_chat_id',
        string='Telegram Chat ID',
        readonly=False,
    )
    vehicle_id = fields.Many2one(
        'shopbike.vehicle',
        string='Kendaraan',
        required=True,
        domain="[('partner_id', '=', partner_id)]",
        tracking=True,
    )
    odometer = fields.Integer(string='Odometer Masuk (KM)')
    mechanic_id = fields.Many2one(
        'res.users',
        string='Mekanik Penanggung Jawab',
        required=True,
        tracking=True,
    )
    date_order = fields.Datetime(
        string='Tanggal Masuk', default=fields.Datetime.now, required=True,
    )
    complaint = fields.Text(string='Keluhan Awal Pelanggan')
    diagnosis = fields.Text(string='Hasil Inspeksi & Diagnosa Mekanik')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('in_progress', 'In Progress'),
        ('waiting_approval', 'Waiting Customer Approval'),
        ('done', 'Done (Siap Bayar)'),
        ('paid', 'Paid (Lunas)'),
        ('cancelled', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    line_ids = fields.One2many(
        'shopbike.service.line', 'order_id', string='Rincian Suku Cadang & Jasa',
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Mata Uang',
        default=lambda self: self.env.company.currency_id,
    )
    amount_total = fields.Monetary(
        string='Total Tagihan (Disetujui)',
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
    )
    amount_pending_additional = fields.Monetary(
        string='Estimasi Tambahan (Pending)',
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
    )

    picking_id = fields.Many2one(
        'stock.picking', string='Delivery Order (Stok)', readonly=True, copy=False,
    )
    invoice_id = fields.Many2one(
        'account.move', string='Customer Invoice', readonly=True, copy=False,
    )
    midtrans_order_id = fields.Char(string='Midtrans Order ID', readonly=True, copy=False)
    payment_url = fields.Char(string='Midtrans Payment Link', readonly=True, copy=False)
    payment_status = fields.Selection([
        ('unpaid', 'Belum Dibayar'),
        ('pending', 'Menunggu Pembayaran'),
        ('paid', 'Lunas (Settlement)'),
        ('failed', 'Gagal / Expired'),
    ], string='Status Pembayaran', default='unpaid', tracking=True)

    log_ids = fields.One2many(
        'shopbike.integration.log', 'order_id', string='Log Integrasi API',
    )

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        self.vehicle_id = False

    @api.onchange('vehicle_id')
    def _onchange_vehicle_id(self):
        if self.vehicle_id:
            self.odometer = self.vehicle_id.last_odometer

    @api.depends('line_ids.price_subtotal', 'line_ids.approval_status')
    def _compute_amount_total(self):
        for order in self:
            approved_lines = order.line_ids.filtered(
                lambda l: l.approval_status in ('included', 'approved')
            )
            pending_lines = order.line_ids.filtered(
                lambda l: l.approval_status == 'pending'
            )
            order.amount_total = sum(approved_lines.mapped('price_subtotal'))
            order.amount_pending_additional = sum(pending_lines.mapped('price_subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('shopbike.service.order') or 'New'
        return super().create(vals_list)

    # ---------------- HELPER LOG, WHATSAPP (FONNTE) & TELEGRAM ----------------

    @staticmethod
    def _normalize_wa_number(phone):
        if not phone:
            return ''
        raw = str(phone).split('@', 1)[0].strip()
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if digits.startswith('0'):
            digits = '62' + digits[1:]
        return digits

    def _get_customer_whatsapp(self):
        self.ensure_one()
        return self.whatsapp_number or self.partner_id.phone or ''

    def _get_public_base_url(self):
        try:
            tunnels = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=1).json().get('tunnels', [])
            if tunnels and tunnels[0].get('public_url'):
                return tunnels[0]['public_url'].rstrip('/')
        except Exception:
            pass
        return (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or 'http://127.0.0.1:8070').rstrip('/')

    def _sign_wa_action(self, action):
        self.ensure_one()
        secret = self.env['ir.config_parameter'].sudo().get_param('database.secret') or 'shopbike-secret'
        msg = f"{self.id}:{action}".encode('utf-8')
        return hmac.new(secret.encode('utf-8'), msg, hashlib.sha256).hexdigest()[:24]

    def _send_whatsapp_message(self, phone, message):
        token = self.env['ir.config_parameter'].sudo().get_param('shopbike.fonnte_token')
        target = self._normalize_wa_number(phone)
        if not token or not target:
            return False

        endpoint = "https://api.fonnte.com/send"
        payload = {'target': target, 'message': message}
        try:
            resp = requests.post(
                endpoint,
                headers={'Authorization': token.strip()},
                data=payload,
                timeout=15,
            )
            try:
                res_json = resp.json()
            except Exception:
                res_json = {'raw': resp.text}
            self._create_integration_log(
                'whatsapp', 'outbound', endpoint, resp.status_code, payload, res_json,
            )
            return resp.status_code == 200 and bool(res_json.get('status'))
        except requests.exceptions.RequestException as err:
            self._create_integration_log(
                'whatsapp', 'outbound', endpoint, 0, payload, {'error': str(err)},
            )
            return False

    def _notify_customer(self, wa_text, tg_text, tg_reply_markup=None):
        """Kirim notifikasi ke WhatsApp (Fonnte) jika aktif, atau fallback ke Telegram."""
        self.ensure_one()
        fonnte_token = self.env['ir.config_parameter'].sudo().get_param('shopbike.fonnte_token')
        wa_target = self._get_customer_whatsapp()
        if fonnte_token and wa_target:
            return 'whatsapp', self._send_whatsapp_message(wa_target, wa_text)
        if self.telegram_chat_id:
            return 'telegram', self._send_telegram_message(
                self.telegram_chat_id, tg_text, reply_markup=tg_reply_markup,
            )
        return None, False

    def _create_integration_log(self, service_type, direction, endpoint, status_code, req_data, res_data):
        self.env['shopbike.integration.log'].sudo().create({
            'order_id': self.id if self else False,
            'service_type': service_type,
            'direction': direction,
            'endpoint': endpoint,
            'status_code': status_code,
            'request_payload': json.dumps(req_data, indent=2, ensure_ascii=False) if isinstance(req_data, (dict, list)) else str(req_data),
            'response_payload': json.dumps(res_data, indent=2, ensure_ascii=False) if isinstance(res_data, (dict, list)) else str(res_data),
        })

    def _send_telegram_message(self, chat_id, text, reply_markup=None):
        token = self.env['ir.config_parameter'].sudo().get_param('shopbike.telegram_bot_token')
        if not token or not chat_id:
            return False

        endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            'chat_id': str(chat_id).strip(),
            'text': text,
            'parse_mode': 'HTML',
        }
        if reply_markup:
            payload['reply_markup'] = reply_markup

        try:
            resp = requests.post(endpoint, json=payload, timeout=10)
            try:
                res_json = resp.json()
            except Exception:
                res_json = resp.text
            self._create_integration_log(
                'telegram', 'outbound', 'https://api.telegram.org/bot<TOKEN>/sendMessage',
                resp.status_code, payload, res_json,
            )
            return resp.status_code == 200
        except requests.exceptions.RequestException as err:
            self._create_integration_log(
                'telegram', 'outbound', 'https://api.telegram.org/bot<TOKEN>/sendMessage',
                0, payload, {'error': str(err)},
            )
            return False

    # ---------------- WORKFLOW ACTIONS ----------------

    def action_confirm(self):
        for order in self:
            if order.odometer and order.vehicle_id:
                order.vehicle_id.sudo().last_odometer = order.odometer
            order.state = 'confirmed'

    def action_start_service(self):
        for order in self:
            order.state = 'in_progress'

    def action_request_approval(self):
        """Kirim rincian temuan tambahan ke WhatsApp (Fonnte) / Telegram pelanggan."""
        for order in self:
            pending_lines = order.line_ids.filtered(lambda l: l.approval_status == 'pending')
            if not pending_lines:
                raise UserError(
                    "Tidak ada item tambahan berstatus 'Menunggu Persetujuan'. "
                    "Centang kolom 'Temuan Tambahan?' pada baris suku cadang/jasa baru terlebih dahulu."
                )

            fonnte_token = order.env['ir.config_parameter'].sudo().get_param('shopbike.fonnte_token')
            has_wa = bool(fonnte_token and order._get_customer_whatsapp())
            if not has_wa and not order.telegram_chat_id:
                raise UserError("Nomor WhatsApp atau Telegram Chat ID pelanggan belum diisi!")

            detail_lines = "\n".join([
                f"• {l.product_id.display_name} ({int(l.quantity)}x): Rp {int(l.price_subtotal):,}"
                for l in pending_lines
            ])
            total_tambahan = int(sum(pending_lines.mapped('price_subtotal')))
            total_jika_setuju = int(order.amount_total + total_tambahan)

            base_url = order._get_public_base_url()
            approve_url = f"{base_url}/api/shopbike/whatsapp/confirm?order_id={order.id}&action=approve&sig={order._sign_wa_action('approve')}"
            reject_url = f"{base_url}/api/shopbike/whatsapp/confirm?order_id={order.id}&action=reject&sig={order._sign_wa_action('reject')}"

            wa_msg = (
                f"*🔧 Shop & Bike — Konfirmasi Biaya Tambahan*\n"
                f"Halo *{order.partner_id.name}*,\n"
                f"Saat inspeksi motor *{order.vehicle_id.display_name}* (`{order.name}`), "
                f"mekanik menemukan komponen berikut perlu diganti:\n\n"
                f"*Rekomendasi Tambahan:*\n{detail_lines}\n\n"
                f"Servis Awal: Rp {int(order.amount_total):,}\n"
                f"Biaya Tambahan: *+Rp {total_tambahan:,}*\n"
                f"Estimasi Total Jika Setuju: *Rp {total_jika_setuju:,}*\n\n"
                f"✅ *Klik untuk SETUJU:*\n{approve_url}\n\n"
                f"❌ *Klik untuk TOLAK:*\n{reject_url}\n\n"
                f"_(Atau balas *YA* / *TIDAK* pada chat ini)_"
            )
            tg_msg = (
                f"<b>🔧 Shop & Bike — Konfirmasi Biaya Tambahan</b>\n"
                f"Halo <b>{order.partner_id.name}</b>,\n"
                f"Saat inspeksi motor <b>{order.vehicle_id.display_name}</b> (<code>{order.name}</code>), "
                f"mekanik menemukan komponen berikut perlu diganti:\n\n"
                f"<b>Rekomendasi Tambahan:</b>\n{detail_lines}\n\n"
                f"Servis Awal: Rp {int(order.amount_total):,}\n"
                f"Biaya Tambahan: <b>+Rp {total_tambahan:,}</b>\n"
                f"Estimasi Total Jika Setuju: <b>Rp {total_jika_setuju:,}</b>\n\n"
                f"Apakah Anda menyetujui pengerjaan tambahan ini?"
            )
            reply_markup = {
                'inline_keyboard': [
                    [{'text': f"✅ Ya, Setuju (+Rp {total_tambahan:,})", 'callback_data': f"approve:{order.id}"}],
                    [{'text': "❌ Tidak, Lanjut Servis Biasa", 'callback_data': f"reject:{order.id}"}],
                ]
            }
            channel, sent = order._notify_customer(wa_msg, tg_msg, tg_reply_markup=reply_markup)
            if not sent:
                raise UserError("Gagal mengirim pesan persetujuan. Cek Token Fonnte / Telegram di Integration Logs.")
            order.state = 'waiting_approval'
            label = "WhatsApp" if channel == 'whatsapp' else "Telegram"
            order.message_post(body=f"Permintaan persetujuan biaya tambahan telah dikirim ke {label} pelanggan.")

    def _deduct_stock_and_check_alert(self):
        """Buat & validasi stock.picking untuk produk storable yang disetujui, lalu cek Low Stock Alert."""
        self.ensure_one()
        storable_lines = self.line_ids.filtered(
            lambda l: l.approval_status in ('included', 'approved')
            and l.product_id.type == 'consu'
            and getattr(l.product_id, 'is_storable', False)
            and l.quantity > 0
        )
        if not storable_lines:
            return

        StockPicking = self.env['stock.picking'].sudo()
        StockMove = self.env['stock.move'].sudo()
        warehouse = self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', self.env.company.id)], limit=1,
        )
        if not warehouse:
            raise UserError("Warehouse tidak ditemukan untuk perusahaan aktif.")

        picking_type = warehouse.out_type_id
        loc_src = picking_type.default_location_src_id or warehouse.lot_stock_id
        loc_dest = self.partner_id.property_stock_customer or self.env.ref('stock.stock_location_customers')

        picking = StockPicking.create({
            'partner_id': self.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': loc_src.id,
            'location_dest_id': loc_dest.id,
            'origin': self.name,
        })

        has_name_field = 'name' in StockMove._fields
        for line in storable_lines:
            move_vals = {
                'picking_id': picking.id,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'product_uom': line.product_id.uom_id.id,
                'location_id': loc_src.id,
                'location_dest_id': loc_dest.id,
            }
            if has_name_field:
                move_vals['name'] = line.description or line.product_id.display_name
            StockMove.create(move_vals)

        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            if 'picked' in move._fields:
                move.picked = True
        picking.with_context(skip_sms=True, skip_backorder=True).button_validate()
        self.picking_id = picking.id

        # Cek Low Stock Alert untuk Kepala Toko (WhatsApp Fonnte / Telegram)
        fonnte_token = self.env['ir.config_parameter'].sudo().get_param('shopbike.fonnte_token')
        wa_manager = self.env['ir.config_parameter'].sudo().get_param('shopbike.whatsapp_manager_phone')
        manager_chat_id = self.env['ir.config_parameter'].sudo().get_param('shopbike.telegram_manager_chat_id')
        if (fonnte_token and wa_manager) or manager_chat_id:
            for line in storable_lines:
                product = line.product_id.sudo()
                product.invalidate_recordset(['qty_available'])
                qty_now = product.qty_available
                threshold = product.product_tmpl_id.min_stock_threshold
                if qty_now <= threshold:
                    if fonnte_token and wa_manager:
                        wa_alert = (
                            f"*⚠️ [PERINGATAN STOK MENIPIS] — Shop & Bike*\n"
                            f"Produk: *{product.display_name}*\n"
                            f"Sisa Stok Saat Ini: *{qty_now:g} {product.uom_id.name}*\n"
                            f"Batas Minimum: {threshold:g} {product.uom_id.name}\n"
                            f"Pemicu Order: `{self.name}`\n"
                            f"Harap segera lakukan restock ke supplier."
                        )
                        self._send_whatsapp_message(wa_manager, wa_alert)
                    elif manager_chat_id:
                        alert_msg = (
                            f"<b>⚠️ [PERINGATAN STOK MENIPIS] — Shop & Bike</b>\n"
                            f"Produk: <b>{product.display_name}</b>\n"
                            f"Sisa Stok Saat Ini: <b>{qty_now:g} {product.uom_id.name}</b>\n"
                            f"Batas Minimum: {threshold:g} {product.uom_id.name}\n"
                            f"Pemicu Order: <code>{self.name}</code>\n"
                            f"Harap segera lakukan restock ke supplier."
                        )
                        self._send_telegram_message(manager_chat_id, alert_msg)

    def action_done(self):
        for order in self:
            pending_lines = order.line_ids.filtered(lambda l: l.approval_status == 'pending')
            if pending_lines:
                raise UserError(
                    "Masih ada item tambahan berstatus Pending! "
                    "Tunggu konfirmasi pelanggan di Telegram atau hapus item tersebut sebelum menyelesaikan servis."
                )
            if not order.line_ids.filtered(lambda l: l.approval_status in ('included', 'approved')):
                raise UserError("Minimal harus ada 1 suku cadang atau jasa yang disetujui.")

            if not order.picking_id:
                order._deduct_stock_and_check_alert()

            order.state = 'done'

            # Kirim Notifikasi ke Pelanggan bahwa motor selesai diservis
            approved_lines = order.line_ids.filtered(lambda l: l.approval_status in ('included', 'approved'))
            items_text = "\n".join([
                f"• {l.product_id.display_name} ({int(l.quantity)}x): Rp {int(l.price_subtotal):,}"
                for l in approved_lines
            ])
            wa_msg = (
                f"*✅ Shop & Bike — Motor Selesai Diservis!*\n"
                f"Halo *{order.partner_id.name}*,\n"
                f"Motor *{order.vehicle_id.display_name}* Anda telah selesai dikerjakan.\n\n"
                f"No. Order: `{order.name}`\n"
                f"*Rincian Pekerjaan:*\n{items_text}\n\n"
                f"*Total Tagihan: Rp {int(order.amount_total):,}*\n"
                f"Silakan menuju kasir atau tunggu tautan pembayaran QRIS/VA dari kami."
            )
            tg_msg = (
                f"<b>✅ Shop & Bike — Motor Selesai Diservis!</b>\n"
                f"Halo <b>{order.partner_id.name}</b>,\n"
                f"Motor <b>{order.vehicle_id.display_name}</b> Anda telah selesai dikerjakan.\n\n"
                f"No. Order: <code>{order.name}</code>\n"
                f"<b>Rincian Pekerjaan:</b>\n{items_text}\n\n"
                f"<b>Total Tagihan: Rp {int(order.amount_total):,}</b>\n"
                f"Silakan menuju kasir atau tunggu tautan pembayaran QRIS/VA dari kami."
            )
            order._notify_customer(wa_msg, tg_msg)

    def action_generate_midtrans(self):
        """Buat Customer Invoice (account.move), tembak Midtrans Snap API, dan kirim link ke Telegram."""
        self.ensure_one()
        if self.state != 'done':
            raise UserError("Generate Payment Link hanya bisa dilakukan pada status 'Done (Siap Bayar)'.")

        server_key = self.env['ir.config_parameter'].sudo().get_param('shopbike.midtrans_server_key')
        if not server_key:
            raise UserError("Midtrans Server Key belum diatur di System Parameters (shopbike.midtrans_server_key)!")

        approved_lines = self.line_ids.filtered(lambda l: l.approval_status in ('included', 'approved'))
        if not approved_lines:
            raise UserError("Tidak ada item yang dapat ditagihkan.")

        # 1. Buat & Post Invoice di Odoo jika belum ada
        if not self.invoice_id:
            invoice_lines = []
            for line in approved_lines:
                invoice_lines.append((0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.description or line.product_id.display_name,
                    'quantity': line.quantity,
                    'price_unit': line.price_unit,
                }))
            invoice = self.env['account.move'].sudo().create({
                'move_type': 'out_invoice',
                'partner_id': self.partner_id.id,
                'invoice_origin': self.name,
                'invoice_line_ids': invoice_lines,
            })
            invoice.action_post()
            self.invoice_id = invoice.id

        # 2. Siapkan Payload Midtrans Snap (Pastikan gross_amount == sum item_details)
        item_details = []
        gross_amount = 0
        for idx, line in enumerate(approved_lines, start=1):
            unit_price = int(round(line.price_unit))
            qty = max(1, int(round(line.quantity)))
            gross_amount += unit_price * qty
            item_details.append({
                'id': (line.product_id.default_code or f"ITEM-{idx}")[:50],
                'price': unit_price,
                'quantity': qty,
                'name': (line.product_id.display_name or 'Item')[:50],
            })

        midtrans_order_id = f"{self.name.replace('/', '-')}-{int(time.time())}"
        endpoint = "https://app.sandbox.midtrans.com/snap/v1/transactions"
        auth_str = base64.b64encode(f"{server_key}:".encode('utf-8')).decode('utf-8')
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': f'Basic {auth_str}',
        }
        try:
            tunnels = requests.get('http://127.0.0.1:4040/api/tunnels', timeout=1).json().get('tunnels', [])
            if tunnels and tunnels[0].get('public_url'):
                headers['X-Override-Notification'] = f"{tunnels[0]['public_url']}/api/shopbike/midtrans/callback"
        except Exception:
            pass
        payload = {
            'transaction_details': {
                'order_id': midtrans_order_id,
                'gross_amount': gross_amount,
            },
            'customer_details': {
                'first_name': (self.partner_id.name or 'Pelanggan')[:50],
                'email': self.partner_id.email or 'customer@shopbike.local',
                'phone': self.partner_id.phone or '08123456789',
            },
            'item_details': item_details,
        }

        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=15)
            try:
                res_json = resp.json()
            except Exception:
                res_json = {'raw': resp.text}
            self._create_integration_log(
                'midtrans', 'outbound', endpoint, resp.status_code, payload, res_json,
            )
            if resp.status_code not in (200, 201):
                raise UserError(f"Midtrans API Error ({resp.status_code}): {res_json}")
        except requests.exceptions.RequestException as err:
            self._create_integration_log(
                'midtrans', 'outbound', endpoint, 0, payload, {'error': str(err)},
            )
            raise UserError(f"Gagal terhubung ke Midtrans Sandbox: {err}")

        redirect_url = res_json.get('redirect_url')
        self.write({
            'midtrans_order_id': midtrans_order_id,
            'payment_url': redirect_url,
            'payment_status': 'pending',
        })
        self.message_post(body=f"Payment Link Midtrans dibuat: {redirect_url}")

        # 3. Kirim Link Pembayaran ke WhatsApp / Telegram Pelanggan
        if redirect_url:
            wa_pay_msg = (
                f"*💳 Shop & Bike — Tautan Pembayaran Digital*\n"
                f"No. Order: `{self.name}`\n"
                f"Total Tagihan: *Rp {gross_amount:,}*\n\n"
                f"Silakan selesaikan pembayaran (QRIS / Virtual Account) melalui link berikut:\n"
                f"{redirect_url}"
            )
            tg_pay_msg = (
                f"<b>💳 Shop & Bike — Tautan Pembayaran Digital</b>\n"
                f"No. Order: <code>{self.name}</code>\n"
                f"Total Tagihan: <b>Rp {gross_amount:,}</b>\n\n"
                f"Silakan selesaikan pembayaran (QRIS / Virtual Account) melalui link berikut:\n"
                f"{redirect_url}"
            )
            self._notify_customer(wa_pay_msg, tg_pay_msg)

    def action_cancel(self):
        for order in self:
            order.state = 'cancelled'


class ShopbikeServiceLine(models.Model):
    _name = 'shopbike.service.line'
    _description = 'Detail Suku Cadang & Jasa Service Order'

    order_id = fields.Many2one(
        'shopbike.service.order', string='Service Order', required=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='Suku Cadang / Jasa', required=True,
    )
    description = fields.Char(string='Keterangan')
    quantity = fields.Float(string='Qty', default=1.0, required=True)
    price_unit = fields.Float(string='Harga Satuan', required=True)
    price_subtotal = fields.Float(
        string='Subtotal', compute='_compute_price_subtotal', store=True,
    )
    is_additional = fields.Boolean(
        string='Temuan Tambahan?',
        default=False,
        help='Centang jika item ini adalah temuan tambahan mekanik saat inspeksi yang butuh persetujuan pelanggan via Telegram.',
    )
    approval_status = fields.Selection([
        ('included', 'Servis Awal'),
        ('pending', 'Menunggu Persetujuan'),
        ('approved', 'Disetujui Pelanggan'),
        ('rejected', 'Ditolak Pelanggan'),
    ], string='Status Item', default='included', required=True)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.description = self.product_id.display_name
            self.price_unit = self.product_id.lst_price

    @api.onchange('is_additional')
    def _onchange_is_additional(self):
        if self.is_additional:
            self.approval_status = 'pending'
        else:
            self.approval_status = 'included'

    @api.depends('quantity', 'price_unit')
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = line.quantity * line.price_unit
