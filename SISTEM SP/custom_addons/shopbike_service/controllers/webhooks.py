import base64
import hashlib
import hmac
import json
import requests

from odoo import http
from odoo.http import request


class ShopbikeWebhookController(http.Controller):

    # ==========================================================
    # 1. INBOUND WEBHOOK: TELEGRAM INLINE BUTTON (APPROVE/REJECT)
    # ==========================================================
    @http.route('/api/shopbike/telegram/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def telegram_webhook(self, **kwargs):
        try:
            raw_body = request.httprequest.data.decode('utf-8')
            payload = json.loads(raw_body) if raw_body else {}
        except Exception as err:
            return request.make_json_response({'status': 'invalid_json', 'error': str(err)}, status=400)

        callback_query = payload.get('callback_query')
        if not callback_query:
            return request.make_json_response({'status': 'ignored'}, status=200)

        cb_id = callback_query.get('id')
        cb_data = callback_query.get('data', '')
        message = callback_query.get('message', {})
        chat_id = message.get('chat', {}).get('id')
        message_id = message.get('message_id')

        if ':' not in cb_data:
            return request.make_json_response({'status': 'invalid_callback_data'}, status=200)

        action, order_id_str = cb_data.split(':', 1)
        try:
            order_id = int(order_id_str)
        except ValueError:
            return request.make_json_response({'status': 'invalid_order_id'}, status=200)

        order = request.env['shopbike.service.order'].sudo().browse(order_id)
        if not order.exists():
            return request.make_json_response({'status': 'order_not_found'}, status=200)

        pending_lines = order.line_ids.filtered(lambda l: l.approval_status == 'pending')
        token = request.env['ir.config_parameter'].sudo().get_param('shopbike.telegram_bot_token')

        if action == 'approve':
            pending_lines.write({'approval_status': 'approved'})
            if order.state == 'waiting_approval':
                order.write({'state': 'in_progress'})
            reply_text = (
                f"✅ <b>Persetujuan Diterima!</b>\n"
                f"Order <code>{order.name}</code>: Item tambahan <b>DISETUJUI</b>.\n"
                f"Total tagihan terbaru: <b>Rp {int(order.amount_total):,}</b>.\n"
                f"Mekanik segera melanjutkan pengerjaan."
            )
            order.message_post(body="Pelanggan MENYETUJUI biaya tambahan melalui Telegram Webhook. Status kembali ke In Progress.")
        elif action == 'reject':
            pending_lines.write({'approval_status': 'rejected'})
            if order.state == 'waiting_approval':
                order.write({'state': 'in_progress'})
            reply_text = (
                f"❌ <b>Konfirmasi Diterima!</b>\n"
                f"Order <code>{order.name}</code>: Item tambahan <b>DITOLAK</b>.\n"
                f"Mekanik hanya melanjutkan servis awal (Total: <b>Rp {int(order.amount_total):,}</b>)."
            )
            order.message_post(body="Pelanggan MENOLAK biaya tambahan melalui Telegram Webhook. Lanjut servis awal saja.")
        else:
            return request.make_json_response({'status': 'unknown_action'}, status=200)

        order._create_integration_log(
            'telegram', 'inbound', '/api/shopbike/telegram/webhook',
            200, payload, {'action_processed': action, 'order': order.name},
        )

        if token and chat_id and message_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                    json={'callback_query_id': cb_id, 'text': 'Pilihan Anda telah dicatat!'},
                    timeout=5,
                )
                requests.post(
                    f"https://api.telegram.org/bot{token}/editMessageText",
                    json={
                        'chat_id': chat_id,
                        'message_id': message_id,
                        'text': reply_text,
                        'parse_mode': 'HTML',
                    },
                    timeout=5,
                )
            except Exception:
                pass

        return request.make_json_response({'status': 'ok'}, status=200)

    # ==========================================================
    # 2. INBOUND WEBHOOK: MIDTRANS PAYMENT NOTIFICATION (SHA-512)
    # ==========================================================
    @http.route('/api/shopbike/midtrans/callback', type='http', auth='public', methods=['POST'], csrf=False)
    def midtrans_callback(self, **kwargs):
        try:
            raw_body = request.httprequest.data.decode('utf-8')
            payload = json.loads(raw_body) if raw_body else {}
        except Exception as err:
            return request.make_json_response({'status': 'invalid_json', 'error': str(err)}, status=400)

        midtrans_order_id = payload.get('order_id', '')
        status_code = str(payload.get('status_code', ''))
        gross_amount = str(payload.get('gross_amount', ''))
        signature_key = payload.get('signature_key', '')
        tx_status = payload.get('transaction_status', '')
        fraud_status = payload.get('fraud_status', 'accept')

        server_key = request.env['ir.config_parameter'].sudo().get_param('shopbike.midtrans_server_key') or ''
        expected_signature = hashlib.sha512(
            f"{midtrans_order_id}{status_code}{gross_amount}{server_key}".encode('utf-8')
        ).hexdigest()

        order = request.env['shopbike.service.order'].sudo().search(
            [('midtrans_order_id', '=', midtrans_order_id)], limit=1,
        )

        signature_valid = hmac.compare_digest(expected_signature, signature_key)
        if not signature_valid and server_key and midtrans_order_id:
            # Fallback: verify directly with Midtrans Status API (handles rotated/secondary keys)
            base_url = "https://api.sandbox.midtrans.com" if server_key.startswith("SB-") else "https://api.midtrans.com"
            auth_b64 = base64.b64encode(f"{server_key}:".encode('utf-8')).decode('utf-8')
            try:
                st_resp = requests.get(
                    f"{base_url}/v2/{midtrans_order_id}/status",
                    headers={'Authorization': f'Basic {auth_b64}', 'Accept': 'application/json'},
                    timeout=10,
                )
                if st_resp.status_code == 200:
                    st_data = st_resp.json()
                    if st_data.get('signature_key') == signature_key and st_data.get('transaction_status') == tx_status:
                        signature_valid = True
            except Exception:
                pass

        if not signature_valid:
            request.env['shopbike.integration.log'].sudo().create({
                'order_id': order.id if order else False,
                'service_type': 'midtrans',
                'direction': 'inbound',
                'endpoint': '/api/shopbike/midtrans/callback',
                'status_code': 403,
                'request_payload': json.dumps(payload, indent=2),
                'response_payload': json.dumps({'error': 'Invalid SHA-512 signature_key'}),
            })
            return request.make_json_response({'status': 'forbidden', 'reason': 'invalid_signature'}, status=403)

        if not order:
            return request.make_json_response({'status': 'order_not_found'}, status=404)

        response_info = {'status': 'ok', 'transaction_status': tx_status}

        if tx_status in ('settlement', 'capture') and fraud_status != 'deny':
            order.write({
                'payment_status': 'paid',
                'state': 'paid',
            })
            if order.invoice_id and order.invoice_id.payment_state != 'paid':
                try:
                    payment_wizard = request.env['account.payment.register'].sudo().with_context(
                        active_model='account.move',
                        active_ids=order.invoice_id.ids,
                    ).create({})
                    payment_wizard.action_create_payments()
                except Exception as pay_err:
                    response_info['invoice_payment_note'] = str(pay_err)

            order.message_post(
                body=f"Pembayaran LUNAS terverifikasi otomatis via Midtrans Webhook (Order ID: {midtrans_order_id})."
            )

            if order.telegram_chat_id:
                receipt_msg = (
                    f"<b>🎉 Pembayaran Berhasil (LUNAS)!</b>\n"
                    f"Terima kasih <b>{order.partner_id.name}</b>.\n"
                    f"Pembayaran untuk Order <code>{order.name}</code> sebesar "
                    f"<b>Rp {int(order.amount_total):,}</b> telah kami terima.\n"
                    f"Silakan ambil kunci motor <b>{order.vehicle_id.display_name}</b> Anda di kasir Shop & Bike!"
                )
                order._send_telegram_message(order.telegram_chat_id, receipt_msg)

        elif tx_status in ('cancel', 'deny', 'expire'):
            order.write({'payment_status': 'failed'})
            order.message_post(body=f"Pembayaran Midtrans berstatus: {tx_status}.")

        order._create_integration_log(
            'midtrans', 'inbound', '/api/shopbike/midtrans/callback',
            200, payload, response_info,
        )
        return request.make_json_response(response_info, status=200)
