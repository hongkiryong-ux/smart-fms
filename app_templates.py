"""앱 전역 Jinja2 템플릿 (main·board_ops·streetlamp 공유)."""
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

templates = Jinja2Templates(directory="templates")

_JSON_SCRIPT_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    ord("\u2028"): "\\u2028",
    ord("\u2029"): "\\u2029",
}


def json_script(value) -> Markup:
    """이미 직렬화된 JSON 문자열을 <script> 안에 넣을 때 사용 — </script> 탈출을 막는다."""
    return Markup(str(value if value is not None else "null").translate(_JSON_SCRIPT_ESCAPES))


templates.env.filters["json_script"] = json_script
