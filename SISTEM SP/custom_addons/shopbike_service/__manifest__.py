{
    'name': 'Shop & Bike Service Management',
    'version': '19.0.1.0.0',
    'category': 'Services',
    'summary': 'Manajemen Servis Motor Shop & Bike Terintegrasi Telegram Bot & Midtrans Sandbox',
    'description': """
        Modul custom Odoo 19 untuk operasional gerai Shop & Bike:
        - Master Kendaraan & Riwayat Servis Pelanggan
        - Service Order dengan Approval Biaya Tambahan via Telegram Inline Button (Webhook)
        - Pemotongan Stok Otomatis & Low Stock Alert ke Grup Telegram Kepala Toko
        - Generate Payment Link Midtrans Sandbox & Pelunasan Otomatis via Webhook SHA-512
        - Integration Logs untuk audit request & response API
    """,
    'author': 'Tim PSET - Universitas Telkom',
    'depends': ['base', 'contacts', 'stock', 'account', 'mail'],
    'data': [
        'security/shopbike_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/res_partner_views.xml',
        'views/product_template_views.xml',
        'views/shopbike_vehicle_views.xml',
        'views/shopbike_service_views.xml',
        'views/shopbike_log_views.xml',
        'views/shopbike_menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
