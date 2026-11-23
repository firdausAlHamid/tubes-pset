from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    telegram_chat_id = fields.Char(
        string='Telegram Chat ID',
        help='Chat ID Telegram pelanggan untuk menerima notifikasi servis & persetujuan biaya tambahan.',
    )
    vehicle_ids = fields.One2many(
        'shopbike.vehicle', 'partner_id', string='Kendaraan',
    )
    vehicle_count = fields.Integer(
        string='Jumlah Kendaraan', compute='_compute_vehicle_count',
    )

    def _compute_vehicle_count(self):
        for rec in self:
            rec.vehicle_count = len(rec.vehicle_ids)

    def action_view_vehicles(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Kendaraan Pelanggan',
            'res_model': 'shopbike.vehicle',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }
