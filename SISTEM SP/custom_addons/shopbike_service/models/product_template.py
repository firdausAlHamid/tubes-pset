from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    min_stock_threshold = fields.Float(
        string='Batas Minimum Stok (Alert Telegram)',
        default=5.0,
        help='Jika stok fisik turun sampai atau di bawah angka ini, Odoo mengirim Low Stock Alert ke Grup Telegram Kepala Toko.',
    )
