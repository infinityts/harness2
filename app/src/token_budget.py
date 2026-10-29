"""Presupuesto de tokens y perfiles de modelo para modelos locales/cloud.

La estimacion es deliberadamente conservadora: no depende de un tokenizer
externo y aplica un margen de seguridad sobre caracteres. Para modelos locales
con tokenizers propios, `chars_per_token` se puede ajustar en
`harness_model_profiles`.
"""
import math
from typing import Any, Dict, Optional

import sqlalchemy as sa

DEFAULT_PROFILE = "local-32k"
DEFAULT_CHARS_PER_TOKEN = 3.6
DEFAULT_MARGIN = 1.08


def estimate_tokens(
    text: str,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    margin: float = DEFAULT_MARGIN,
) -> int:
    value = str(text or "")
    if not value:
        return 0
    ratio = max(1.0, float(chars_per_token or DEFAULT_CHARS_PER_TOKEN))
    return max(1, int(math.ceil((len(value) / ratio) * max(1.0, float(margin or 1.0)))))


def fit_text(
    text: str,
    max_tokens: int,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    margin: float = DEFAULT_MARGIN,
    suffix: str = "\n[...truncated by token_budget]",
) -> str:
    value = str(text or "")
    if max_tokens <= 0:
        return ""
    ratio = max(1.0, float(chars_per_token or DEFAULT_CHARS_PER_TOKEN))
    max_chars = max(0, int(max_tokens * ratio / max(1.0, margin)))
    if len(value) <= max_chars:
        return value
    if max_chars <= len(suffix):
        return suffix[:max_chars]
    return value[: max_chars - len(suffix)] + suffix


def _profile_dict(row) -> Dict[str, Any]:
    item = dict(row)
    item["chars_per_token"] = float(item.get("chars_per_token") or DEFAULT_CHARS_PER_TOKEN)
    return item


def get_profile(conn, name: Optional[str] = None) -> Dict[str, Any]:
    profile_name = str(name or DEFAULT_PROFILE).strip()
    row = conn.execute(
        sa.text("""
            SELECT name, display_name, provider, model_id, context_window_tokens,
                   max_input_tokens, max_output_tokens, reserved_tokens,
                   chars_per_token, supports_tools, is_default
            FROM harness_model_profiles
            WHERE name = :name
        """),
        {"name": profile_name},
    ).mappings().first()
    if not row:
        raise ValueError(f"Unknown model profile: {profile_name}")
    return _profile_dict(row)


def get_default_profile(conn) -> Dict[str, Any]:
    row = conn.execute(
        sa.text("""
            SELECT name, display_name, provider, model_id, context_window_tokens,
                   max_input_tokens, max_output_tokens, reserved_tokens,
                   chars_per_token, supports_tools, is_default
            FROM harness_model_profiles
            WHERE is_default = true
            ORDER BY name
            LIMIT 1
        """)
    ).mappings().first()
    if not row:
        return get_profile(conn, DEFAULT_PROFILE)
    return _profile_dict(row)


def resolve_profile(conn, name: Optional[str] = None, max_context_tokens: Optional[int] = None) -> Dict[str, Any]:
    """Elige el perfil explicito o infiere uno por la ventana declarada."""
    if name:
        return get_profile(conn, name)
    if max_context_tokens:
        tokens = int(max_context_tokens)
        if tokens >= 96000:
            return get_profile(conn, "local-120k")
        if tokens >= 48000:
            return get_profile(conn, "local-64k")
    return get_default_profile(conn)


def context_budget(profile: Dict[str, Any], requested: Optional[int] = None) -> int:
    max_input = int(profile.get("max_input_tokens") or 20000)
    if requested is None:
        return max_input
    return max(300, min(int(requested), max_input))
