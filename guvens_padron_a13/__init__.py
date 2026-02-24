# Monkey-patch: l10n_ar_padron/models.py usa _logger pero no lo define.
# No podemos pushear a a2systems/odoo-argentina, así que lo inyectamos acá.
import logging
from odoo.addons.l10n_ar_padron import models as _padron_models
if not hasattr(_padron_models, '_logger'):
    _padron_models._logger = logging.getLogger(_padron_models.__name__)

from . import models
