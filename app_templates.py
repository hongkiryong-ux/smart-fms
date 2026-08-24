"""앱 전역 Jinja2 템플릿 (main·board_ops·streetlamp 공유)."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
