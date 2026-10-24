
from pkgutil import extend_path

# Domain owns app.db; shared technical adapters are app.platform, never app.core.
__path__ = extend_path(__path__, __name__)
