from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ShopbikeVehicle(models.Model):
    _name = 'shopbike.vehicle'
    _description = 'Master Kendaraan Pelanggan Shop & Bike'
    _rec_name = 'display_name'

    license_plate = fields.Char(string='Nomor Polisi', required=True, index=True)
    brand = fields.Char(string='Merk Motor', required=True)
    model_name = fields.Char(string='Tipe / Model', required=True)
    manufacture_year = fields.Integer(string='Tahun Pembuatan', default=2023)
    last_odometer = fields.Integer(string='Odometer Terakhir (KM)', default=0)
    partner_id = fields.Many2one(
        'res.partner', string='Pemilik (Pelanggan)', required=True, ondelete='restrict',
    )
    service_order_ids = fields.One2many(
        'shopbike.service.order', 'vehicle_id', string='Riwayat Servis',
    )
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('license_plate', 'brand', 'model_name')
    def _compute_display_name(self):
        for rec in self:
            plate = (rec.license_plate or '').upper()
            rec.display_name = f"{plate} - {rec.brand or ''} {rec.model_name or ''}".strip()

    @api.constrains('license_plate')
    def _check_unique_license_plate(self):
        for rec in self:
            if rec.license_plate:
                normalized = rec.license_plate.strip().upper()
                duplicate = self.search([
                    ('id', '!=', rec.id),
                    ('license_plate', '=ilike', normalized),
                ], limit=1)
                if duplicate:
                    raise ValidationError(
                        f"Nomor Polisi '{rec.license_plate}' sudah terdaftar di sistem!"
                    )
