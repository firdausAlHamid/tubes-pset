from odoo import fields, models


class ShopbikeIntegrationLog(models.Model):
    _name = 'shopbike.integration.log'
    _description = 'Log Integrasi API Telegram & Midtrans'
    _order = 'create_date desc'

    order_id = fields.Many2one(
        'shopbike.service.order', string='Service Order', ondelete='set null',
    )
    service_type = fields.Selection([
        ('telegram', 'Telegram Bot API'),
        ('midtrans', 'Midtrans Payment Gateway'),
    ], string='Layanan Eksternal', required=True)
    direction = fields.Selection([
        ('outbound', 'Outbound (Odoo -> External)'),
        ('inbound', 'Inbound (Webhook -> Odoo)'),
    ], string='Arah Komunikasi', required=True)
    endpoint = fields.Char(string='Endpoint / URL')
    status_code = fields.Integer(string='HTTP Status Code')
    request_payload = fields.Text(string='Request Payload (JSON)')
    response_payload = fields.Text(string='Response Payload (JSON)')
